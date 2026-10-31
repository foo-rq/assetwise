import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Each test gets a real temporary SQL file without touching the user's saved workspace
    monkeypatch.setenv("ASSETWISE_DB_PATH", str(tmp_path / "portfolios.sqlite3"))
    with TestClient(app) as client:
        yield client


KEY = {"X-Workspace-Key": "a" * 64}
OTHER = {"X-Workspace-Key": "b" * 64}


def test_retired_demo_identifiers_remain_valid_without_runtime_generation():
    from backend.storage import dataset_symbols
    from backend.tests.simulated_history import build_simulated_history

    # Keep every old identifier preservable after moving fictional price generation out of the app
    assert dataset_symbols("demo-v2") == {asset.symbol for asset in build_simulated_history().assets}


def snapshot(name="", weights=None, dataset="archive-2017-v1"):
    return {"name": name, "version": 1, "datasetVersion": dataset,
            "weights": {"SPY": 60, "IEF": 30} if weights is None else weights}


def test_corrected_and_original_price_versions_preserve_separate_allocations(client):
    original = snapshot(weights={"AAPL": 100})
    corrected = snapshot(weights={"AAPL": 70, "SPY": 30}, dataset="archive-2017-v2")
    state = {"revision": 0, "current": [original, corrected], "scenarios": [snapshot("Original")]}
    assert client.put("/api/storage", headers=KEY, json=state).status_code == 200
    loaded = client.get("/api/storage", headers=KEY).json()
    assert {item["datasetVersion"]: item["weights"] for item in loaded["current"]} == {
        "archive-2017-v1": original["weights"], "archive-2017-v2": corrected["weights"]}
    assert loaded["scenarios"][0]["weights"] == snapshot()["weights"]


def test_save_reload_and_normalised_sql_rows(client, tmp_path):
    state = {"revision": 0, "current": [snapshot()],
             "scenarios": [snapshot("Original"), snapshot("Cash", {})]}
    response = client.put("/api/storage", headers=KEY, json=state)
    assert response.status_code == 200
    assert response.json()["revision"] == 1
    loaded = client.get("/api/storage", headers=KEY)
    assert loaded.json() == response.json()
    assert loaded.headers["cache-control"] == "no-store"
    with sqlite3.connect(tmp_path / "portfolios.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM portfolios").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM holdings").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM workspaces").fetchone()[0] == 1


def test_visitors_cannot_read_or_replace_each_others_saves(client):
    client.put("/api/storage", headers=KEY, json={"current": [snapshot()]})
    assert client.get("/api/storage", headers=OTHER).json()["current"] == []
    client.put("/api/storage", headers=OTHER, json={"scenarios": [snapshot("Other")]})
    assert client.get("/api/storage", headers=KEY).json()["scenarios"] == []


def test_stale_revision_rolls_back_without_losing_rows(client):
    first = client.put("/api/storage", headers=KEY, json={"scenarios": [snapshot("Keep")]}).json()
    conflict = client.put("/api/storage", headers=KEY, json={"revision": 0, "scenarios": []})
    assert conflict.status_code == 409
    assert client.get("/api/storage", headers=KEY).json() == first


def test_removal_restore_and_independent_dataset_currents(client):
    historical = snapshot()
    demo = snapshot(weights={"DEMO-EQ1": 70}, dataset="demo-v2")
    state = client.put("/api/storage", headers=KEY, json={
        "current": [historical, demo], "scenarios": [snapshot("Before")]
    }).json()
    removed = state["scenarios"].pop()
    state["removed"] = removed
    state = client.put("/api/storage", headers=KEY, json=state).json()
    assert state["removed"]["weights"] == removed["weights"]
    state["scenarios"] = [{**removed, "name": "After"}]
    state["removed"] = None
    saved = client.put("/api/storage", headers=KEY, json=state).json()
    assert saved["scenarios"][0]["name"] == "After"
    assert len(saved["current"]) == 2


@pytest.mark.parametrize("headers", [{}, {"X-Workspace-Key": "guess"}, {"X-Workspace-Key": "G" * 64}])
def test_no_access_without_private_key(client, headers):
    assert client.get("/api/storage", headers=headers).status_code == 401
    assert client.put("/api/storage", headers=headers, json={}).status_code == 401


@pytest.mark.parametrize("state", [
    {"revision": True},
    {"current": [snapshot(weights={"SPY": 101})]},
    {"current": [snapshot(weights={"SPY": 80, "IEF": 30})]},
    {"current": [snapshot(weights={"SPY": "60"})]},
    {"scenarios": [snapshot(" ")]},
    {"scenarios": [snapshot("Original"), snapshot("original")]},
    {"current": [snapshot(), snapshot()]},
    {"current": [snapshot(weights={"a" * 33: 10})]},
    {"current": [snapshot(weights={"UNKNOWN": 10})]},
    {"removed": snapshot()},
    {"unexpected": "ignored?"},
])
def test_invalid_state_cannot_replace_saved_portfolios(client, state):
    before = client.put("/api/storage", headers=KEY, json={"current": [snapshot()]}).json()
    assert client.put("/api/storage", headers=KEY, json=state).status_code == 422
    assert client.get("/api/storage", headers=KEY).json() == before


def test_sql_failure_returns_recovery_message(client, tmp_path, monkeypatch):
    # A directory cannot be opened as a database, reproducing unavailable storage
    monkeypatch.setenv("ASSETWISE_DB_PATH", str(tmp_path))
    response = client.get("/api/storage", headers=KEY)
    assert response.status_code == 503
    assert "Keep this tab open" in response.json()["detail"]


def test_sql_parameters_preserve_names_as_data(client):
    name = "Robert'); DROP TABLE portfolios; --"
    response = client.put("/api/storage", headers=KEY, json={"scenarios": [snapshot(name)]})
    assert response.status_code == 200
    assert client.get("/api/storage", headers=KEY).json()["scenarios"][0]["name"] == name


PROFILE = {"experience": "new", "age": "private", "goal": "learn", "horizon": "unsure",
           "tolerance": "unsure", "capacity": "unsure", "existing": "yes", "amount": 1000}


def test_survey_round_trip_owner_isolation_and_legacy_client(client):
    saved = client.put("/api/storage", headers=KEY, json={"profile": PROFILE, "current": [snapshot()]}).json()
    expected = {**PROFILE, "horizon_years": None, "max_loss_percent": None}
    assert saved["profile"] == expected
    assert client.get("/api/storage", headers=OTHER).json()["profile"] is None
    # An older client omits profile rather than explicitly requesting its removal
    client.put("/api/storage", headers=KEY, json={"revision": saved["revision"], "current": [snapshot()]})
    assert client.get("/api/storage", headers=KEY).json()["profile"] == expected


def test_exact_investment_period_and_loss_limit_survive_sql_reload(client):
    profile = {**PROFILE, "horizon_years": 2.5, "max_loss_percent": 15.0}
    client.put("/api/storage", headers=KEY, json={"profile": profile, "current": [snapshot()]})
    assert client.get("/api/storage", headers=KEY).json()["profile"] == profile


@pytest.mark.parametrize("field,value", [("experience", "expertish"), ("age", 25), ("amount", -10), ("amount", True),
                                          ("horizon_years", 0), ("horizon_years", True),
                                          ("max_loss_percent", 101), ("max_loss_percent", True)])
def test_invalid_survey_cannot_replace_portfolios(client, field, value):
    saved = client.put("/api/storage", headers=KEY, json={"current": [snapshot()]}).json()
    result = client.put("/api/storage", headers=KEY, json={
        "revision": saved["revision"], "profile": {**PROFILE, field: value}, "current": []})
    assert result.status_code == 422
    assert client.get("/api/storage", headers=KEY).json()["current"] == saved["current"]


def test_survey_and_portfolio_conflict_is_atomic(client):
    saved = client.put("/api/storage", headers=KEY, json={"profile": PROFILE, "current": [snapshot()]}).json()
    rejected = client.put("/api/storage", headers=KEY, json={
        "revision": 0, "profile": {**PROFILE, "experience": "confident"}, "current": []})
    assert rejected.status_code == 409
    assert client.get("/api/storage", headers=KEY).json() == saved
