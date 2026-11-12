"""Independent checks of the recovery listing and its application boundary."""

import copy
import json
from threading import RLock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_edda.access import OwnerAuth
from ai_edda.api.app import create_app
from ai_edda.api.job_catalog import create_job_catalog_router

PROJECT = "12345678-1234-1234-1234-123456789abc"
OTHER = "12345678-1234-1234-1234-123456789abd"
FIELDS = {
    "id",
    "kind",
    "state",
    "progress",
    "project_id",
    "request_id",
    "label",
    "has_publication_result",
    "error",
    "coordinator_error",
}


def save_job(config, job_id, **values):
    directory = config / "jobs"
    directory.mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_id,
        "kind": "future_kind",
        "state": "completed",
        "progress": None,
        "context": None,
        **values,
    }
    (directory / f"{job_id}.json").write_text(json.dumps(job), encoding="utf-8")


def login(client):
    assert client.post("/api/session", json={"password": "review-password"}).status_code == 200


def test_real_app_read_only_session_and_persisted_restart(tmp_path, monkeypatch):
    config, projects = tmp_path / "config", tmp_path / "projects"
    OwnerAuth.setup(config / "owner.json", "review-password")
    first, second = "1" * 32, "2" * 32
    save_job(
        config,
        first,
        kind="project_import",
        context={"file_name": "archive.zip"},
        publication_result={"project_id": PROJECT, "large_result": "secret" * 10000},
    )
    save_job(config, second, context=["malformed optional context"], state="running")
    expected = None
    for _ in range(2):
        app = create_app(projects, config)
        with TestClient(app) as client:
            assert client.get("/api/jobs").status_code == 401
            client.cookies.set("ai_edda_session", "invalid")
            assert client.get("/api/jobs").status_code == 401
            client.cookies.clear()
            login(client)
            assert not app.state.leases._leases
            before = {path.name: path.read_bytes() for path in (config / "jobs").glob("*.json")}

            def forbidden(*args, **kwargs):
                pytest.fail(
                    "Listing attempted a project, media, publication, or mutation operation"
                )

            with monkeypatch.context() as patch:
                patch.setattr(app.state.service, "get", forbidden)
                patch.setattr(app.state.service.store, "_dir", forbidden)
                patch.setattr(app.state.coordinator, "get", forbidden)
                patch.setattr(app.state.coordinator, "cancel", forbidden)
                patch.setattr(app.state.jobs, "_save", forbidden)
                response = client.get("/api/jobs")
                assert response.status_code == 200
                body = response.json()
                assert client.get("/api/jobs", params={"project_id": PROJECT}).json()["items"] == [
                    body["items"][0]
                ]
            assert {
                path.name: path.read_bytes() for path in (config / "jobs").glob("*.json")
            } == before
            assert [row["id"] for row in body["items"]] == [first, second]
            assert body["items"][1]["kind"] == "future_kind"
            assert body["items"][1]["state"] == "interrupted"
            assert body["items"][1]["project_id"] is None
            assert all(set(row) == FIELDS for row in body["items"])
            assert "large_result" not in response.text and "secret" not in response.text
            assert not app.state.leases._leases
            if expected is not None:
                assert body == expected
            expected = body
            assert client.delete("/api/session").status_code == 200
            assert client.get("/api/jobs").status_code == 401


def test_real_unknown_pending_recovery_error_remains_visible(tmp_path):
    config = tmp_path / "config"
    OwnerAuth.setup(config / "owner.json", "review-password")
    job_id = "a" * 32
    save_job(
        config, job_id, state="publication_pending", context=[], result={"private": [0] * 1000}
    )
    app = create_app(tmp_path / "projects", config)
    with TestClient(app) as client:
        app.state.coordinator.stopping.set()
        app.state.coordinator.observer.join(timeout=2)
        login(client)
        row = client.get("/api/jobs?state=active").json()["items"][0]
        assert row["id"] == job_id and row["kind"] == "future_kind"
        assert row["state"] == "publication_pending"
        assert row["coordinator_error"] == app.state.coordinator._job_errors[job_id]
        assert row["coordinator_error"]
        assert row["project_id"] is None
        assert set(row) == FIELDS


@pytest.fixture
def catalog():
    states = [
        "reserved",
        "running",
        "cancelling",
        "ready",
        "publication_pending",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    ]
    jobs = {
        f"{index:032x}": {
            "kind": "future_kind",
            "state": states[index % len(states)],
            "progress": 0.75,
            "context": {"project_id": PROJECT, "request_id": "request"},
        }
        for index in reversed(range(55))
    }
    coordinator = SimpleNamespace(
        jobs=SimpleNamespace(lock=RLock(), jobs=jobs), lock=RLock(), _job_errors={}
    )
    app = FastAPI()
    app.include_router(
        create_job_catalog_router(
            coordinator, SimpleNamespace(authenticate=lambda token: token == "ok")
        )
    )
    with TestClient(app) as client:
        client.cookies.set("ai_edda_session", "ok")
        yield client, coordinator


def test_pagination_default_max_order_end_and_no_mutation(catalog):
    client, coordinator = catalog
    before = copy.deepcopy(coordinator.jobs.jobs)
    default = client.get("/api/jobs").json()
    assert len(default["items"]) == 20 and default["next_cursor"] == f"{19:032x}"
    first = client.get("/api/jobs?limit=50").json()
    second = client.get("/api/jobs", params={"limit": 50, "after": first["next_cursor"]}).json()
    assert len(first["items"]) == 50 and len(second["items"]) == 5
    assert second["next_cursor"] is None
    ids = [row["id"] for row in first["items"] + second["items"]]
    assert ids == sorted(coordinator.jobs.jobs)
    assert client.get("/api/jobs", params={"after": "f" * 32}).json() == {
        "items": [],
        "next_cursor": None,
    }
    assert client.get("/api/jobs", params={"project_id": OTHER}).json() == {
        "items": [],
        "next_cursor": None,
    }
    assert coordinator.jobs.jobs == before


@pytest.mark.parametrize(
    "state",
    [
        "reserved",
        "running",
        "cancelling",
        "ready",
        "publication_pending",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    ],
)
def test_every_known_state_classification(catalog, state):
    client, coordinator = catalog
    coordinator.jobs.jobs = {"a" * 32: {"kind": "test", "state": state}}
    terminal = state in {"completed", "failed", "cancelled", "interrupted"}
    assert len(client.get("/api/jobs?state=terminal").json()["items"]) == int(terminal)
    assert len(client.get("/api/jobs?state=active").json()["items"]) == int(not terminal)


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 51},
        {"limit": "x"},
        {"limit": 1.5},
        {"state": ""},
        {"state": "running"},
        {"state": "ACTIVE"},
        {"project_id": ""},
        {"project_id": "../file"},
        {"project_id": PROJECT.upper()},
        {"project_id": PROJECT.replace("-", "")},
        {"project_id": "{" + PROJECT + "}"},
        {"after": ""},
        {"after": "a" * 31},
        {"after": "a" * 33},
        {"after": "A" * 32},
        {"after": PROJECT},
    ],
)
def test_malformed_query(catalog, params):
    client, _ = catalog
    assert client.get("/api/jobs", params=params).status_code == 422


@pytest.mark.parametrize(
    "context",
    [
        None,
        [],
        "bad",
        7,
        {"request": []},
        {"file_name": []},
        {"request_id": []},
        {"project_id": []},
    ],
)
def test_optional_context_does_not_hide_unknown_job(catalog, context):
    client, coordinator = catalog
    coordinator.jobs.jobs = {
        "a" * 32: {"kind": "future_kind", "state": "completed", "context": context}
    }
    row = client.get("/api/jobs").json()["items"][0]
    assert row["id"] == "a" * 32 and row["kind"] == "future_kind"
    assert row["project_id"] is None and row["request_id"] is None


def test_compact_projection_and_current_coordinator_error(catalog):
    client, coordinator = catalog
    job_id = "a" * 32
    private = "PRIVATE_FULL_PAYLOAD" * 10000
    coordinator.jobs.jobs = {
        job_id: {
            "kind": "generation",
            "state": "failed",
            "progress": 0.8,
            "context": {
                "project_id": PROJECT,
                "request_id": "request",
                "request": {"generator_id": "generator", "payload": private},
            },
            "result": {"charts": private},
            "publication_result": {"charts": private},
            "error": "worker failure",
        }
    }
    coordinator._job_errors[job_id] = "current failure"
    response = client.get("/api/jobs")
    row = response.json()["items"][0]
    assert row == {
        "id": job_id,
        "kind": "generation",
        "state": "failed",
        "progress": 0.8,
        "project_id": PROJECT,
        "request_id": "request",
        "label": "generator",
        "has_publication_result": True,
        "error": "worker failure",
        "coordinator_error": "current failure",
    }
    assert len(response.content) < 1000 and "PRIVATE_FULL_PAYLOAD" not in response.text
    coordinator._job_errors.clear()
    assert client.get("/api/jobs").json()["items"][0]["coordinator_error"] is None
