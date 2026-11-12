from threading import RLock
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_edda.api.job_catalog import create_job_catalog_router


def test_listing():
    project = "12345678-1234-1234-1234-123456789abc"
    jobs = {
        "0" * 32: {
            "kind": "project_import",
            "state": "completed",
            "context": {"file_name": "a.zip"},
            "publication_result": {"project_id": project, "large": [0] * 1000},
        },
        "1" * 32: {"kind": "unknown", "state": "interrupted", "context": []},
        "2" * 32: {
            "kind": "generation",
            "state": "ready",
            "context": {
                "project_id": project,
                "request": {"generator_id": "gen", "large": [0] * 1000},
            },
        },
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
    client = TestClient(app)
    assert client.get("/api/jobs").status_code == 401
    client.cookies.set("ai_edda_session", "ok")
    page = client.get("/api/jobs?limit=1").json()
    assert page["next_cursor"] == "0" * 32
    assert page["items"][0]["label"] == "a.zip"
    assert "large" not in str(page)
    assert len(client.get("/api/jobs", params={"after": page["next_cursor"]}).json()["items"]) == 2
    assert len(client.get("/api/jobs", params={"project_id": project}).json()["items"]) == 2
    assert len(client.get("/api/jobs?state=terminal").json()["items"]) == 2
    assert len(client.get("/api/jobs?state=active").json()["items"]) == 1
    assert client.get("/api/jobs?after=ABC").status_code == 422
