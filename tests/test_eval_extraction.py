import asyncio
import json

import yaml

from job_scout import eval_extraction as eval_module
from job_scout.eval_extraction import (
    _list_field_f1,
    _scalar_matches,
    compare_fields,
    run_eval_extraction,
)
from job_scout.llm.base import LLMProvider
from job_scout.llm.extraction import PostingExtraction


class _FakeProvider(LLMProvider):
    def __init__(self, results, available=True):
        self._results = list(results)
        self.available = available
        self.calls = 0

    async def extract(self, posting_text: str) -> PostingExtraction:
        result = self._results[self.calls]
        self.calls += 1
        return result

    async def check_available(self) -> tuple[bool, str]:
        return self.available, "" if self.available else "unavailable"

    async def aclose(self) -> None:
        pass


def test_scalar_matches_is_case_insensitive():
    assert _scalar_matches("Senior", "senior") is True
    assert _scalar_matches("Senior", "Junior") is False
    assert _scalar_matches(5, 5) is True
    assert _scalar_matches(None, None) is True


def test_list_field_f1_perfect_match():
    assert _list_field_f1(["Python", "SQL"], ["python", "sql"]) == 1.0


def test_list_field_f1_partial_overlap():
    score = _list_field_f1(["Python", "SQL"], ["python", "Go"])
    # precision=1/2, recall=1/2 -> F1=0.5
    assert score == 0.5


def test_list_field_f1_both_empty_is_perfect():
    assert _list_field_f1([], []) == 1.0


def test_list_field_f1_one_empty_is_zero():
    assert _list_field_f1(["Python"], []) == 0.0
    assert _list_field_f1([], ["Python"]) == 0.0


def test_compare_fields_excludes_unlabeled_fields_from_denominator():
    predicted = PostingExtraction(seniority="senior", min_years=5, location="Remote")
    expected = {"seniority": "senior"}  # min_years and location not labeled
    scores: dict[str, list[float]] = {}

    compare_fields(predicted, expected, scores)

    assert scores["seniority"] == [1.0]
    assert "min_years" not in scores
    assert "location" not in scores


def test_compare_fields_scores_wrong_scalar_as_zero():
    predicted = PostingExtraction(seniority="junior")
    expected = {"seniority": "senior"}
    scores: dict[str, list[float]] = {}

    compare_fields(predicted, expected, scores)

    assert scores["seniority"] == [0.0]


def test_run_eval_extraction_reports_unavailable_provider(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        eval_module, "get_provider", lambda config: _FakeProvider([], available=False)
    )
    asyncio.run(run_eval_extraction(tmp_path, tmp_path / "labels.yaml"))

    assert "Cannot run eval-extraction" in capsys.readouterr().out


def test_run_eval_extraction_end_to_end_with_tiny_fixture_set(
    monkeypatch, tmp_path, capsys
):
    fixtures_dir = tmp_path / "postings"
    fixtures_dir.mkdir()
    (fixtures_dir / "a.json").write_text(
        json.dumps(
            {"id": "a", "title": "Engineer", "company": "Acme", "text": "text a"}
        )
    )
    (fixtures_dir / "b.json").write_text(
        json.dumps(
            {"id": "b", "title": "Designer", "company": "Acme", "text": "text b"}
        )
    )
    labels_file = tmp_path / "labels.yaml"
    labels_file.write_text(
        yaml.dump(
            {
                "a": {"seniority": "senior", "required_skills": ["Python"]},
                "b": {"seniority": "junior"},
            }
        )
    )

    provider = _FakeProvider(
        [
            PostingExtraction(seniority="senior", required_skills=["python"]),
            PostingExtraction(seniority="mid"),  # wrong vs. expected "junior"
        ]
    )
    monkeypatch.setattr(eval_module, "get_provider", lambda config: provider)

    asyncio.run(run_eval_extraction(fixtures_dir, labels_file))
    output = capsys.readouterr().out

    assert "Evaluated 2 fixture(s)" in output
    assert provider.calls == 2
