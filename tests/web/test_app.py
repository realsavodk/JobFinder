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
                    score=50,
                )
            )
        session.commit()

    with TestClient(app_module.app) as client:
        ontario = client.get("/?region=ON").text
        everywhere = client.get("/").text

    assert "Toronto Dev" in ontario and "SF Dev" not in ontario
    assert "Toronto Dev" in everywhere and "SF Dev" in everywhere
    assert "Ontario (1)" in everywhere and "California (1)" in everywhere


def test_dashboard_shows_every_score_by_default_and_caps_rows(monkeypatch, tmp_path):
    from datetime import UTC, datetime

    from sqlmodel import Session

    from job_scout.models import Posting

    app_module = _fresh_app_module(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "MAX_ROWS", 2)
    now = datetime.now(UTC)
    with Session(app_module._engine) as session:
        for i, score in enumerate([0, 30, 20, 10]):
            session.add(
                Posting(
                    source="greenhouse",
                    company="Acme",
                    external_id=str(i),
                    title=f"Job scoring {score}",
                    location="Remote",
                    url=f"https://example.com/{i}",
                    description_html="",
                    first_seen=now,
                    last_seen=now,
                    score=score,
                )
            )
        session.commit()

    with TestClient(app_module.app) as client:
        default = client.get("/").text
        scored = client.get("/?min_score=1").text

    assert "Job scoring 30" in default and "Job scoring 20" in default
    assert "Job scoring 10" not in default  # past the row cap
    assert "Showing the top 2 of 4 matches" in default
    assert "Showing the top 2 of 3 matches" in scored


def test_companies_page_lists_failures_first(monkeypatch, tmp_path):
    from sqlmodel import Session

    from job_scout.models import CompanyRunStatus

    app_module = _fresh_app_module(monkeypatch, tmp_path)
    companies = tmp_path / "companies.yaml"
    companies.write_text(
        "- {name: Alpha, ats: lever, token: a}\n- {name: Zeta, ats: lever, token: z}\n"
    )
    monkeypatch.setattr(app_module, "COMPANIES_FILE", companies)
    with Session(app_module._engine) as session:
        session.add(CompanyRunStatus(company="Alpha", ok=True, message="10 postings"))
        session.add(CompanyRunStatus(company="Zeta", ok=False, message="HTTP 500"))
        session.add(CompanyRunStatus(company="Removed", ok=False, message="old"))
        session.commit()

    with TestClient(app_module.app) as client:
        page = client.get("/companies").text
        jobs = client.get("/").text

    assert "2 companies" in page and "Removed" not in page
    assert "1 failed on the last run" in page
    assert page.index("Zeta") < page.index("Alpha")
    assert "HTTP 500" not in jobs  # run status no longer clutters the jobs page


def _add_postings(app_module, *rows):
    """rows: (title, status, follow_up_date) tuples."""
    from datetime import UTC, datetime

    from sqlmodel import Session

    from job_scout.models import Posting

    now = datetime.now(UTC)
    ids = []
    with Session(app_module._engine) as session:
        for i, (title, status, follow_up) in enumerate(rows):
            posting = Posting(
                source="greenhouse",
                company="Acme",
                external_id=str(i),
                title=title,
                location="Toronto",
                url=f"https://example.com/{i}",
                description_html="",
                first_seen=now,
                last_seen=now,
                score=40,
                status=status,
                follow_up_date=follow_up,
            )
            session.add(posting)
            session.commit()
            ids.append(posting.id)
    return ids


def test_jobs_tabs_split_by_status(monkeypatch, tmp_path):
    app_module = _fresh_app_module(monkeypatch, tmp_path)
    _add_postings(
        app_module,
        ("Todo Job", "new", None),
        ("Liked Job", "interested", None),
        ("Applied Job", "applied", None),
        ("Rejected Job", "rejected", None),
    )
    with TestClient(app_module.app) as client:
        todo = client.get("/").text
        applied = client.get("/?tab=applied").text
        archive = client.get("/?tab=archive").text

    assert "Todo Job" in todo and "Liked Job" in todo
    assert "Applied Job" not in todo and "Rejected Job" not in todo
    assert "Applied Job" in applied and "Todo Job" not in applied
    assert "Rejected Job" in archive and "Applied Job" not in archive
    assert 'Applied<span class="count">1</span>' in todo


def test_mark_applied_sets_a_follow_up_and_leaves_the_to_apply_tab(
    monkeypatch, tmp_path
):
    from datetime import date

    from sqlmodel import Session

    from job_scout.models import Posting

    app_module = _fresh_app_module(monkeypatch, tmp_path)
    (posting_id,) = _add_postings(app_module, ("Todo Job", "new", None))
    with TestClient(app_module.app) as client:
        response = client.post(
            f"/postings/{posting_id}",
            data={
                "status": "applied",
                "tab": "todo",
                "notes": "",
                "follow_up_date": "",
            },
        )
    with Session(app_module._engine) as session:
        posting = session.get(Posting, posting_id)

    assert response.text == ""  # row removed from the To apply list
    assert posting.status == "applied"
    assert posting.follow_up_date == date.today() + app_module.FOLLOW_UP_AFTER


def test_applied_tab_lists_soonest_follow_up_first(monkeypatch, tmp_path):
    from datetime import date, timedelta

    app_module = _fresh_app_module(monkeypatch, tmp_path)
    today = date.today()
    _add_postings(
        app_module,
        ("No Date Job", "applied", None),
        ("Later Job", "applied", today + timedelta(days=5)),
        ("Overdue Job", "applied", today - timedelta(days=2)),
    )
    with TestClient(app_module.app) as client:
        page = client.get("/?tab=applied").text

    assert (
        page.index("Overdue Job") < page.index("Later Job") < page.index("No Date Job")
    )
    assert "Overdue 2d" in page and "In 5d" in page
