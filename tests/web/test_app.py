import asyncio
import importlib
import sys
import time

from fastapi.testclient import TestClient


def _fresh_app_module(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    sys.modules.pop("job_scout.web.app", None)
    return importlib.import_module("job_scout.web.app")


def test_run_now_returns_immediately_without_waiting_for_the_pipeline(
    monkeypatch, tmp_path
):
    app_module = _fresh_app_module(monkeypatch, tmp_path)

    async def slow_run_all(companies_file, profile_file):
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(app_module, "run_all", slow_run_all)
    monkeypatch.setattr(app_module, "COMPANIES_FILE", tmp_path / "companies.yaml")
    monkeypatch.setattr(app_module, "PROFILE_FILE", tmp_path / "profile.yaml")

    with TestClient(app_module.app) as client:
        start = time.monotonic()
        response = client.post("/run", follow_redirects=False)
        elapsed = time.monotonic() - start

    assert response.status_code == 303
    assert elapsed < 0.5  # well under the 1s the "pipeline" takes


def test_run_now_does_not_start_a_second_run_while_one_is_in_flight(
    monkeypatch, tmp_path
):
    app_module = _fresh_app_module(monkeypatch, tmp_path)
    calls = {"n": 0}

    async def slow_run_all(companies_file, profile_file):
        calls["n"] += 1
        await asyncio.sleep(0.3)
        return []

    monkeypatch.setattr(app_module, "run_all", slow_run_all)

    with TestClient(app_module.app) as client:
        client.post("/run", follow_redirects=False)
        client.post("/run", follow_redirects=False)
        time.sleep(0.5)  # let the in-flight task finish

    assert calls["n"] == 1


def _resume_app(monkeypatch, tmp_path, fake_import):
    app_module = _fresh_app_module(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "import_resume", fake_import)
    monkeypatch.setattr(app_module, "COMPANIES_FILE", tmp_path / "companies.yaml")
    monkeypatch.setattr(app_module, "PROFILE_FILE", tmp_path / "profile.yaml")
    return app_module


def test_upload_resume_imports_it_and_shows_the_summary(monkeypatch, tmp_path):
    seen = {}

    async def fake_import(resume_path, profile_path):
        seen["text"] = resume_path.read_text()
        seen["suffix"] = resume_path.suffix
        profile_path.write_text("years_experience: 4.0\n")
        return ["years_experience: 4.0", "Skills added (1): Go"]

    app_module = _resume_app(monkeypatch, tmp_path, fake_import)
    with TestClient(app_module.app) as client:
        response = client.post(
            "/resume", files={"resume": ("cv.txt", b"my resume", "text/plain")}
        )

    assert response.status_code == 200
    assert seen == {"text": "my resume", "suffix": ".txt"}
    assert "Skills added (1): Go" in response.text
    assert "years_experience: 4.0" in response.text


def test_upload_resume_rejects_other_file_types(monkeypatch, tmp_path):
    async def fake_import(resume_path, profile_path):
        raise AssertionError("should not be called")

    app_module = _resume_app(monkeypatch, tmp_path, fake_import)
    with TestClient(app_module.app) as client:
        response = client.post(
            "/resume", files={"resume": ("cv.docx", b"x", "application/octet-stream")}
        )

    assert response.status_code == 400
    assert "Upload a .pdf, .txt or .md file." in response.text


def test_upload_resume_shows_import_errors(monkeypatch, tmp_path):
    from job_scout.resume_import import ResumeImportError

    async def fake_import(resume_path, profile_path):
        raise ResumeImportError("Ollama not reachable")

    app_module = _resume_app(monkeypatch, tmp_path, fake_import)
    with TestClient(app_module.app) as client:
        response = client.post(
            "/resume", files={"resume": ("cv.pdf", b"%PDF", "application/pdf")}
        )

    assert response.status_code == 400
    assert "Ollama not reachable" in response.text


def test_progress_bar_shows_running_then_done(monkeypatch, tmp_path):
    app_module = _fresh_app_module(monkeypatch, tmp_path)
    progress = app_module.pipeline_run.progress
    monkeypatch.setattr(progress, "running", False)

    with TestClient(app_module.app) as client:
        idle = client.get("/progress").text

        monkeypatch.setattr(progress, "running", True)
        monkeypatch.setattr(progress, "stage", "Reading postings with the LLM: Acme")
        monkeypatch.setattr(progress, "done", 3)
        monkeypatch.setattr(progress, "total", 10)
        running = client.get("/progress").text

        monkeypatch.setattr(progress, "running", False)
        finished = client.get("/progress?polling=true").text

    assert "<progress" not in idle and "Done" not in idle
    assert '<progress value="3" max="10">' in running
    assert "3 / 10" in running
    assert 'hx-trigger="every 2s"' in running
    assert "Done" in finished
    assert "every 2s" not in finished


def test_dashboard_filters_by_state_or_province(monkeypatch, tmp_path):
    from datetime import UTC, datetime

    from sqlmodel import Session

    from job_scout.models import Posting

    app_module = _fresh_app_module(monkeypatch, tmp_path)
    now = datetime.now(UTC)
    with Session(app_module._engine) as session:
        for i, (title, location) in enumerate(
            [("Toronto Dev", "Toronto"), ("SF Dev", "San Francisco, CA")]
        ):
            session.add(
                Posting(
                    source="greenhouse",
                    company="Acme",
                    external_id=str(i),
                    title=title,
                    location=location,
                    url=f"https://example.com/{i}",
                    description_html="",
                    first_seen=now,
                    last_seen=now,
                )
            )
        session.commit()

    with TestClient(app_module.app) as client:
        ontario = client.get("/?region=ON").text
        everywhere = client.get("/").text

    assert "Toronto Dev" in ontario and "SF Dev" not in ontario
    assert "Toronto Dev" in everywhere and "SF Dev" in everywhere
    assert "Ontario (1)" in everywhere and "California (1)" in everywhere
