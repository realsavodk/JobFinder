import asyncio
from datetime import UTC, datetime

from sqlmodel import Session, SQLModel, create_engine, select

from job_scout.llm.base import LLMProvider
from job_scout.llm.extraction import PostingExtraction
from job_scout.models import Posting
from job_scout.pipeline import extract as extract_module
from job_scout.pipeline.extract import extract_postings
from job_scout.scoring.profile import Profile, SkillWeight

_PROFILE = Profile(skills=[SkillWeight(name="Python")])


class _FakeProvider(LLMProvider):
    def __init__(self, result_or_results=None):
        self._results = (
            result_or_results if isinstance(result_or_results, list) else None
        )
        self._single = None if self._results else result_or_results
        self.calls = 0

    async def extract(self, posting_text: str) -> PostingExtraction:
        self.calls += 1
        value = (
            self._results[self.calls - 1] if self._results is not None else self._single
        )
        if isinstance(value, Exception):
            raise value
        return value

    async def check_available(self) -> tuple[bool, str]:
        return True, ""

    async def aclose(self) -> None:
        pass


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def _posting(
    external_id: str, *, description_html: str = "<p>Python role</p>", **overrides
) -> Posting:
    now = datetime.now(UTC)
    defaults = {
        "source": "greenhouse",
        "company": "Acme",
        "external_id": external_id,
        "title": "Engineer",
        "location": "Remote",
        "url": f"https://acme.example/{external_id}",
        "description_html": description_html,
        "first_seen": now,
        "last_seen": now,
    }
    defaults.update(overrides)
    return Posting(**defaults)


def test_successful_extraction_populates_fields(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(_posting("1"))
        session.commit()

        provider = _FakeProvider(
            PostingExtraction(
                seniority="senior",
                min_years=5,
                required_skills=["Python"],
                nice_skills=["SQL"],
                degree_fields=["Computer Science"],
                location="Remote",
                remote="remote",
                role_family="engineering",
            )
        )
        asyncio.run(
            extract_postings(
                session,
                companies=["Acme"],
                profile=_PROFILE,
                provider=provider,
                num_ctx=8192,
            )
        )
        row = session.exec(select(Posting)).one()

    assert row.extraction_status == "success"
    assert row.seniority == "senior"
    assert row.min_years == 5
    assert row.required_skills == ["Python"]
    assert row.remote == "remote"
    assert provider.calls == 1


def test_cache_hit_skips_unchanged_posting_on_second_run(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(_posting("1"))
        session.commit()

        provider = _FakeProvider(PostingExtraction(seniority="senior"))
        asyncio.run(
            extract_postings(
                session,
                companies=["Acme"],
                profile=_PROFILE,
                provider=provider,
                num_ctx=8192,
            )
        )
        assert provider.calls == 1

        asyncio.run(
            extract_postings(
                session,
                companies=["Acme"],
                profile=_PROFILE,
                provider=provider,
                num_ctx=8192,
            )
        )

    assert provider.calls == 1  # not re-extracted, content unchanged


def test_changed_content_is_re_extracted_even_after_failure(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        posting = _posting("1", description_html="<p>Original Python text</p>")
        session.add(posting)
        session.commit()

        provider = _FakeProvider([RuntimeError("boom"), RuntimeError("boom")])
        asyncio.run(
            extract_postings(
                session,
                companies=["Acme"],
                profile=_PROFILE,
                provider=provider,
                num_ctx=8192,
            )
        )
        row = session.exec(select(Posting)).one()
        assert row.extraction_status == "failed"

        row.description_html = "<p>Totally different Python text now</p>"
        session.add(row)
        session.commit()

        provider2 = _FakeProvider(PostingExtraction(seniority="mid"))
        asyncio.run(
            extract_postings(
                session,
                companies=["Acme"],
                profile=_PROFILE,
                provider=provider2,
                num_ctx=8192,
            )
        )
        row = session.exec(select(Posting)).one()

    assert row.extraction_status == "success"
    assert provider2.calls == 1


def test_duplicate_postings_are_skipped(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        canonical = _posting("1")
        session.add(canonical)
        session.commit()

        dup = _posting("2")
        dup.duplicate_of_id = canonical.id
        session.add(dup)
        session.commit()

        provider = _FakeProvider(PostingExtraction(seniority="senior"))
        attempted = asyncio.run(
            extract_postings(
                session,
                companies=["Acme"],
                profile=_PROFILE,
                provider=provider,
                num_ctx=8192,
            )
        )

    assert attempted == 1
    assert provider.calls == 1


def test_max_extractions_per_run_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_module, "MAX_EXTRACTIONS_PER_RUN", 2)
    engine = _engine(tmp_path)
    with Session(engine) as session:
        for i in range(5):
            session.add(
                _posting(str(i), description_html=f"<p>Python role number {i}</p>")
            )
        session.commit()

        provider = _FakeProvider(PostingExtraction(seniority="mid"))
        attempted = asyncio.run(
            extract_postings(
                session,
                companies=["Acme"],
                profile=_PROFILE,
                provider=provider,
                num_ctx=8192,
            )
        )

    assert attempted == 2
    assert provider.calls == 2


def test_on_progress_reports_done_out_of_postings_needing_extraction(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(_posting("1"))
        session.add(_posting("2"))
        session.commit()

        calls = []
        asyncio.run(
            extract_postings(
                session,
                companies=["Acme"],
                profile=_PROFILE,
                provider=_FakeProvider(PostingExtraction(seniority="mid")),
                num_ctx=8192,
                on_progress=lambda done, total: calls.append((done, total)),
            )
        )

    assert calls == [(0, 2), (1, 2)]


def _extract(session, provider, profile=_PROFILE, companies=("Acme",)):
    return asyncio.run(
        extract_postings(
            session,
            companies=list(companies),
            profile=profile,
            provider=provider,
            num_ctx=8192,
        )
    )


def test_prefilter_skips_postings_the_keyword_scorer_rules_out(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(_posting("match", description_html="<p>Python role</p>"))
        session.add(_posting("no-skills", description_html="<p>Sell things</p>"))
        session.add(_posting("too-senior", title="Senior Staff Engineer"))
        session.add(_posting("closed", status="closed"))
        session.commit()

        provider = _FakeProvider(PostingExtraction(seniority="entry"))
        attempted = _extract(session, provider)
        statuses = {
            p.external_id: p.extraction_status for p in session.exec(select(Posting))
        }

    assert attempted == 1
    assert statuses == {
        "match": "success",
        "no-skills": "pending",
        "too-senior": "pending",
        "closed": "pending",
    }


def test_budget_is_shared_across_companies_and_spent_on_best_matches(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(extract_module, "MAX_EXTRACTIONS_PER_RUN", 2)
    profile = Profile(
        skills=[SkillWeight(name="Python", weight=1), SkillWeight(name="SQL", weight=1)]
    )
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(_posting("a-weak", description_html="<p>Python</p>"))
        session.add(
            _posting("b-strong", company="Beta", description_html="<p>Python SQL</p>")
        )
        session.add(
            _posting("c-strong", company="Gamma", description_html="<p>Python SQL</p>")
        )
        session.commit()

        _extract(
            session,
            _FakeProvider(PostingExtraction()),
            profile,
            ("Acme", "Beta", "Gamma"),
        )
        done = {
            p.external_id
            for p in session.exec(select(Posting))
            if p.extraction_status == "success"
        }

    assert done == {"b-strong", "c-strong"}


def test_profile_without_skills_still_extracts(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(_posting("1", description_html="<p>Anything</p>"))
        session.commit()
        provider = _FakeProvider(PostingExtraction())
        assert _extract(session, provider, Profile()) == 1
