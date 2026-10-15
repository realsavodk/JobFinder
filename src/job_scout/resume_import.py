from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import yaml

from job_scout.config import load_llm_config
from job_scout.llm.provider import EXTRACTION_TIMEOUT_SECONDS, get_provider
from job_scout.llm.resume import ResumeExtraction, Role
from job_scout.llm.text import truncate_for_context
from job_scout.scoring.profile import Profile

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?$")


class ResumeImportError(Exception):
    pass


def read_resume_text(path: Path) -> str:
    if path.suffix.lower() != ".pdf":
        return path.read_text()
    if shutil.which("pdftotext") is None:
        raise ResumeImportError("reading PDFs needs pdftotext (install poppler)")
    result = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ResumeImportError(f"couldn't read PDF: {result.stderr.strip()}")
    return result.stdout


def _month_index(value: str) -> int | None:
    match = _DATE_RE.match(value.strip())
    if not match:
        return None
    # Year-only dates count as January: "2020 - 2022" becomes ~2 years, not 3.
    month = int(match.group(2) or 1)
    if not 1 <= month <= 12:
        return None
    return int(match.group(1)) * 12 + month - 1


def years_of_experience(roles: list[Role], today: date) -> float:
    """Total non-internship time, counting overlapping roles once."""
    now = today.year * 12 + today.month - 1
    spans: list[tuple[int, int]] = []
    for role in roles:
        if role.internship:
            continue
        start = _month_index(role.start)
        if role.end is None:
            end = now
        else:
            last = _month_index(role.end)
            # End month is inclusive: "2022-01 to 2022-06" is 6 months.
            end = None if last is None else min(last + 1, now)
        if start is not None and end is not None:
            spans.append((start, end))

    months = 0
    covered_until = 0
    for start, end in sorted(spans):
        start = max(start, covered_until)
        if end > start:
            months += end - start
            covered_until = end
    return round(months / 12, 1)


def seniority_targets_for(years: float) -> list[str]:
    # Values match the extraction prompt's seniority vocabulary.
    if years < 1:
        return ["intern", "entry", "associate"]
    if years < 3:
        return ["entry", "associate", "mid"]
    if years < 6:
        return ["mid", "senior"]
    return ["senior", "staff", "principal"]


def merge_into_profile(data: dict, skills: list[str], years: float) -> list[str]:
    """Add new skills (weight 1) and set years_experience; returns skills added."""
    existing = data.setdefault("skills", []) or []
    data["skills"] = existing
    known = {s["name"].lower() for s in existing}
    known |= {t.lower() for t in data.get("tools") or []}
    added = []
    for skill in skills:
        skill = skill.strip()
        if skill and skill.lower() not in known:
            existing.append({"name": skill, "weight": 1})
            known.add(skill.lower())
            added.append(skill)
    data["years_experience"] = years
    return added


async def import_resume(resume_path: Path, profile_path: Path) -> list[str]:
    """Update profile_path from the resume; returns summary lines to show the user."""
    llm_config = load_llm_config()
    provider = get_provider(llm_config)
    try:
        ok, reason = await provider.check_available()
        if not ok:
            raise ResumeImportError(reason)
        text = truncate_for_context(
            read_resume_text(resume_path), num_ctx=llm_config.num_ctx
        )
        if not text.strip():
            raise ResumeImportError("no text found in the resume (scanned image?)")
        try:
            extraction: ResumeExtraction = await asyncio.wait_for(
                provider.extract_resume(text), timeout=EXTRACTION_TIMEOUT_SECONDS
            )
        except Exception as exc:
            raise ResumeImportError(f"LLM extraction failed: {exc!r}") from exc
    finally:
        await provider.aclose()

    years = years_of_experience(extraction.roles, date.today())
    data = (
        (yaml.safe_load(profile_path.read_text()) or {})
        if profile_path.exists()
        else {}
    )
    added = merge_into_profile(data, extraction.skills, years)
    # Only fill in targets the user hasn't chosen themselves.
    targets_set = not data.get("seniority_targets")
    if targets_set:
        data["seniority_targets"] = seniority_targets_for(years)
    Profile(**data)  # refuse to write a profile the scorer can't load

    backup = profile_path.with_suffix(".yaml.bak")
    if profile_path.exists():
        shutil.copy(profile_path, backup)
    profile_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))

    lines = ["Roles found:"]
    for role in extraction.roles:
        kind = "internship, not counted" if role.internship else "counted"
        lines.append(
            f"  {role.title}: {role.start} to {role.end or 'present'} ({kind})"
        )
    lines.append(f"years_experience: {years}")
    lines.append(f"Skills added ({len(added)}): {', '.join(added) or 'none'}")
    lines.append(
        f"seniority_targets: {', '.join(data['seniority_targets'])}"
        + (" (set from your experience)" if targets_set else " (kept yours)")
    )
    lines.append(
        f"Wrote {profile_path}"
        + (f" (previous version: {backup})" if backup.exists() else "")
    )
    return lines
