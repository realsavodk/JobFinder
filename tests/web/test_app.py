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
