import asyncio
import json
from datetime import date

import httpx
import yaml

from job_scout import resume_import
from job_scout.llm.base import LLMProvider
from job_scout.llm.extraction import PostingExtraction
from job_scout.llm.ollama_provider import OllamaProvider
from job_scout.llm.resume import ResumeExtraction, Role
from job_scout.resume_import import (
    import_resume,
    merge_into_profile,
    seniority_targets_for,
    years_of_experience,
)

TODAY = date(2026, 10, 15)


def _role(start, end, internship=False):
    return Role(title="x", start=start, end=end, internship=internship)


def test_years_counts_inclusive_end_month():
    assert years_of_experience([_role("2022-01", "2022-12")], TODAY) == 1.0


def test_years_counts_current_role_up_to_today():
    # Oct 2025 through the start of Oct 2026
    assert years_of_experience([_role("2025-10", None)], TODAY) == 1.0


def test_years_counts_overlapping_roles_once():
    roles = [_role("2020-01", "2021-12"), _role("2021-01", "2022-12")]
    assert years_of_experience(roles, TODAY) == 3.0


def test_years_excludes_internships_and_unparseable_dates():
    roles = [
        _role("2019-06", "2019-08", internship=True),
        _role("Summer 2020", "2020-08"),
        _role("2023-01", "2023-06"),
    ]
    assert years_of_experience(roles, TODAY) == 0.5


def test_years_treats_year_only_dates_as_january():
    assert years_of_experience([_role("2020", "2022")], TODAY) == 2.1


def test_merge_keeps_existing_weights_and_skips_known_skills_and_tools():
    data = {
        "skills": [{"name": "Python", "weight": 5}],
        "tools": ["Docker"],
        "locations": ["Remote"],
    }
    added = merge_into_profile(data, ["python", "docker", "SQL", "sql", " "], 2.5)

    assert added == ["SQL"]
    assert data["skills"] == [
        {"name": "Python", "weight": 5},
        {"name": "SQL", "weight": 1},
    ]
    assert data["years_experience"] == 2.5
    assert data["locations"] == ["Remote"]


def test_merge_into_empty_profile():
    data = {}
    assert merge_into_profile(data, ["Go"], 1.0) == ["Go"]
    assert data == {"skills": [{"name": "Go", "weight": 1}], "years_experience": 1.0}


class _FakeProvider(LLMProvider):
    def __init__(self, result):
        self.result = result
        self.resume_text = None

    async def extract(self, posting_text: str) -> PostingExtraction:
        raise AssertionError("not used")

    async def extract_resume(self, resume_text: str) -> ResumeExtraction:
        self.resume_text = resume_text
        return self.result

    async def check_available(self) -> tuple[bool, str]:
        return True, ""

    async def aclose(self) -> None:
        pass


def test_import_resume_updates_profile_and_keeps_backup(tmp_path, monkeypatch):
    resume = tmp_path / "resume.txt"
    resume.write_text("Jane Doe - Backend Engineer at Acme")
    profile = tmp_path / "profile.yaml"
    profile.write_text("skills:\n  - name: Python\n    weight: 5\nremote_ok: true\n")

    fake = _FakeProvider(
        ResumeExtraction(
            skills=["Python", "Postgres"],
            roles=[
                Role(
                    title="Backend Engineer",
                    start="2024-10",
                    end=None,
                    internship=False,
                )
            ],
        )
    )
    monkeypatch.setattr(resume_import, "get_provider", lambda _config: fake)

    asyncio.run(import_resume(resume, profile))

    assert fake.resume_text == "Jane Doe - Backend Engineer at Acme"
    written = yaml.safe_load(profile.read_text())
    assert written["skills"] == [
        {"name": "Python", "weight": 5},
        {"name": "Postgres", "weight": 1},
    ]
    assert written["remote_ok"] is True
    assert written["years_experience"] > 0
    assert "Postgres" not in (tmp_path / "profile.yaml.bak").read_text()


def test_ollama_extract_resume_requires_every_field():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        result = ResumeExtraction(skills=["Go"], roles=[])
        return httpx.Response(
            200, json={"message": {"content": result.model_dump_json()}}
        )

    async def run():
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="qwen3:8b",
            num_ctx=8192,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            return await provider.extract_resume("resume text")
        finally:
            await provider.aclose()

    result = asyncio.run(run())

    sent = captured["body"]["format"]
    assert set(sent["required"]) == {"skills", "roles"}
    assert set(sent["$defs"]["Role"]["required"]) == {
        "title",
        "start",
        "end",
        "internship",
    }
    assert result.skills == ["Go"]


def test_seniority_targets_follow_experience():
    assert seniority_targets_for(0) == ["intern", "entry", "associate"]
    assert seniority_targets_for(2) == ["entry", "associate", "mid"]
    assert seniority_targets_for(4) == ["mid", "senior"]
    assert seniority_targets_for(10) == ["senior", "staff", "principal"]


def _import_with(tmp_path, monkeypatch, profile_text, roles):
    resume = tmp_path / "resume.txt"
    resume.write_text("resume")
    profile = tmp_path / "profile.yaml"
    if profile_text is not None:
        profile.write_text(profile_text)
    fake = _FakeProvider(ResumeExtraction(skills=[], roles=roles))
    monkeypatch.setattr(resume_import, "get_provider", lambda _config: fake)
    asyncio.run(import_resume(resume, profile))
    return yaml.safe_load(profile.read_text())


def test_import_with_no_experience_targets_internships_and_entry_roles(
    tmp_path, monkeypatch
):
    intern_only = [
        Role(title="Intern", start="2025-05", end="2025-08", internship=True)
    ]
    written = _import_with(tmp_path, monkeypatch, None, intern_only)
    assert written["years_experience"] == 0.0
    assert written["seniority_targets"] == ["intern", "entry", "associate"]


def test_import_keeps_seniority_targets_the_user_set(tmp_path, monkeypatch):
    written = _import_with(tmp_path, monkeypatch, "seniority_targets: [senior]\n", [])
    assert written["seniority_targets"] == ["senior"]
