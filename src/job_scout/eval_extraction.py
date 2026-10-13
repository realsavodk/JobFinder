from __future__ import annotations

import json
from pathlib import Path

import yaml

from job_scout.config import load_llm_config
from job_scout.llm.base import ExtractionError
from job_scout.llm.extraction import PostingExtraction
from job_scout.llm.provider import extract_with_retry, get_provider
from job_scout.llm.text import prepare_posting_text

_SCALAR_FIELDS = ("seniority", "min_years", "location", "remote", "role_family")
_LIST_FIELDS = ("required_skills", "nice_skills", "degree_fields")


def _normalize_set(values: list[str] | None) -> set[str]:
    return {v.strip().lower() for v in (values or []) if v.strip()}


def _list_field_f1(predicted: list[str], expected: list[str]) -> float:
    predicted_set = _normalize_set(predicted)
    expected_set = _normalize_set(expected)
    if not predicted_set and not expected_set:
        return 1.0
    if not predicted_set or not expected_set:
        return 0.0
    overlap = predicted_set & expected_set
    precision = len(overlap) / len(predicted_set)
    recall = len(overlap) / len(expected_set)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _scalar_matches(predicted, expected) -> bool:
    if isinstance(predicted, str) and isinstance(expected, str):
        return predicted.strip().lower() == expected.strip().lower()
    return predicted == expected


def compare_fields(
    predicted: PostingExtraction,
    expected: dict,
    field_scores: dict[str, list[float]],
) -> None:
    for field in _SCALAR_FIELDS:
        if field not in expected or expected[field] is None:
            continue  # unlabeled field — excluded from that field's denominator
        score = (
            1.0 if _scalar_matches(getattr(predicted, field), expected[field]) else 0.0
        )
        field_scores.setdefault(field, []).append(score)

    for field in _LIST_FIELDS:
        if field not in expected or expected[field] is None:
            continue
        score = _list_field_f1(getattr(predicted, field), expected[field])
        field_scores.setdefault(field, []).append(score)


def _print_report(field_scores: dict[str, list[float]]) -> None:
    print(f"{'field':<16} {'score':>8} {'labeled':>8}")
    for field in (*_SCALAR_FIELDS, *_LIST_FIELDS):
        scores = field_scores.get(field, [])
        if not scores:
            print(f"{field:<16} {'n/a':>8} {0:>8}")
            continue
        avg = sum(scores) / len(scores)
        print(f"{field:<16} {avg * 100:>7.0f}% {len(scores):>8}")


async def run_eval_extraction(fixtures_dir: Path, labels_file: Path) -> None:
    llm_config = load_llm_config()
    provider = get_provider(llm_config)
    try:
        ok, reason = await provider.check_available()
        if not ok:
            print(f"Cannot run eval-extraction: {reason}")
            return

        labels = yaml.safe_load(labels_file.read_text()) or {}
        field_scores: dict[str, list[float]] = {}
        evaluated = 0

        for fixture_path in sorted(fixtures_dir.glob("*.json")):
            fixture = json.loads(fixture_path.read_text())
            expected = labels.get(fixture["id"])
            if expected is None:
                continue

            text = prepare_posting_text(fixture["text"], num_ctx=llm_config.num_ctx)
            try:
                predicted = await extract_with_retry(provider, text)
            except ExtractionError as exc:
                print(f"{fixture['id']}: extraction failed ({exc})")
                continue

            compare_fields(predicted, expected, field_scores)
            evaluated += 1

        print(f"Evaluated {evaluated} fixture(s)\n")
        _print_report(field_scores)
    finally:
        await provider.aclose()
