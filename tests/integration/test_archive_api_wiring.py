import asyncio
import copy
import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_archive_upload_router_review import wait_job
from test_import_worker import (
    game_upload as game_upload,
)
from test_import_worker import (
    media as media,
)
from test_import_worker import (
    native_upload as native_upload,
)
from test_import_worker import (
    package as package,
)

from ai_edda.access import OwnerAuth
from ai_edda.api import archives
from ai_edda.api.app import create_app
from ai_edda.import_receipts import build_import_receipt
from ai_edda.project_store import _json


@pytest.fixture
def running(tmp_path):
    config = tmp_path / "app-config"
    projects = tmp_path / "published"
    OwnerAuth.setup(config / "owner.json", "secret")
    app = create_app(projects, config)
    with TestClient(app) as client:
        assert client.post("/api/session", json={"password": "secret"}).status_code == 200
        yield app, client, projects, config


def upload_headers(client, size=3):
    return {
        "host": "testserver",
        "cookie": "ai_edda_session=" + client.cookies.get("ai_edda_session"),
        "content-type": "application/octet-stream",
        "x-request-id": str(uuid4()),
        "x-archive-kind": "native",
        "x-file-name": "source.zip",
        "x-file-size": str(size),
    }


async def invoke(app, headers, path="/api/projects/imports", method="POST", chunks=None):
    receives, sent, error = 0, [], None
    chunks = iter(chunks if chunks is not None else [b"zip"])

    async def receive():
        nonlocal receives
        receives += 1
        chunk = next(chunks, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": method,
        "scheme": "http",
        "http_version": "1.1",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": ("127.0.0.1", 1000),
        "server": ("testserver", 80),
    }
    try:
        await app(scope, receive, send)
    except BaseException as exc:
        error = exc
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    return status, receives, error


@pytest.mark.parametrize("kind", ["native", "game"])
def test_real_create_app_imports_before_any_project_and_opens_published_result(
    running, request, kind
):
    app, client, _, _ = running
    upload = request.getfixturevalue("native_upload" if kind == "native" else "game_upload")
    source = upload[0] / "source.zip"
    body = source.read_bytes()
    assert client.get("/api/projects").json() == []
    request_headers = upload_headers(client, len(body))
    request_headers["x-archive-kind"] = kind
    response = client.post("/api/projects/imports", headers=request_headers, content=body)
    assert response.status_code == 202, response.text
    job = wait_job(app.state.coordinator, response.json()["job_id"])
    result = job["publication_result"]
    opened = client.get("/api/projects/" + result["project_id"])
    assert opened.status_code == 200
    assert opened.json()["project"]["revision"] == result["revision"]
    assert (
        len(opened.json()["charts"]) == 3 if kind == "game" else len(opened.json()["charts"]) == 1
    )
    assert source.read_bytes() == body
    assert "project_import" in app.state.coordinator.handlers


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"cookie": ""}, 401),
        ({"x-request-id": "invalid"}, 422),
        ({"content-type": "application/json"}, 422),
        ({"content-type": ""}, 422),
        ({"cookie": "", "content-type": "application/json"}, 401),
        ({"x-file-name": "bad%QQ"}, 422),
        ({"x-file-size": str(1024**3 + 1)}, 413),
        ({"host": "untrusted.example"}, 400),
        ({"origin": "http://untrusted.example"}, 400),
        ({"content-length": "4"}, 422),
    ],
)
def test_full_boundary_rejects_auth_headers_and_origin_before_any_receive(running, changes, status):
    app, client, _, _ = running
    before = copy.deepcopy(app.state.jobs.jobs)
    result, receives, error = asyncio.run(invoke(app, {**upload_headers(client), **changes}))
    assert result == status and receives == 0, (result, receives, error)
    assert app.state.jobs.jobs == before


def test_global_upload_larger_than_eight_mib_streams_without_general_body_buffer(
    running, monkeypatch
):
    app, client, _, _ = running
    count, size = 12, 1024 * 1024
    started = []

    def start(coordinator, job):
        assert not any(coordinator.writers.values())
        assert (coordinator._workspace(job) / "source.zip").stat().st_size == count * size
        started.append(job)

    monkeypatch.setattr(archives, "start_project_import", start)
    status, receives, error = asyncio.run(
        invoke(app, upload_headers(client, count * size), chunks=[b"x" * size] * count)
    )
    assert status == 202, error
    assert receives == count + 1 and len(started) == 1
    assert client.get("/api/projects").json() == []
    app.state.coordinator.cancel(started[0])


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/projects/imports/", "POST"),
        ("/api/projects/imports-extra", "POST"),
        ("/prefix/api/projects/imports", "POST"),
        ("/api/projects/imports/extra", "POST"),
        ("/api/projects/imports", "PUT"),
        ("/api/projects/imports", "PATCH"),
        ("/api/projects/imports", "DELETE"),
    ],
)
def test_only_exact_post_path_bypasses_general_eight_mib_limit(running, path, method):
    app, client, _, _ = running
    request_headers = upload_headers(client, 9 * 1024 * 1024)
    request_headers["content-length"] = str(9 * 1024 * 1024)
    status, receives, _ = asyncio.run(invoke(app, request_headers, path, method))
    assert status == 413 and receives == 0
    assert not app.state.jobs.jobs


def test_owner_can_relogin_and_cancel_global_import_without_project_lease(running):
    app, client, _, _ = running
    context = {
        "request_id": str(uuid4()),
        "archive_kind": "native",
        "file_name": "source.zip",
        "file_size": 3,
    }
    job = app.state.coordinator.reserve("project_import", context)
    assert client.delete("/api/session").status_code == 200
    assert client.post(f"/api/jobs/{job}/cancel").status_code == 401
    assert app.state.coordinator.get(job)["state"] == "reserved"
    assert client.post("/api/session", json={"password": "secret"}).status_code == 200
    assert client.post(f"/api/jobs/{job}/cancel").status_code == 200
    assert app.state.coordinator.get(job)["state"] == "cancelled"


@pytest.mark.parametrize("kind", ["audio_import", "cover_import", "analysis", "generation"])
def test_project_jobs_still_require_current_writer_lease(running, kind):
    app, client, _, _ = running
    view = client.post("/api/projects", json={"request_id": str(uuid4()), "metadata": {}}).json()
    pid = view["project"]["id"]
    job = app.state.coordinator.reserve(kind, {"project_id": pid})
    assert client.post(f"/api/jobs/{job}/cancel").status_code == 409
    lease = client.post(
        f"/api/projects/{pid}/writer-lease", json={"tab_id": "new", "action": "acquire"}
    ).json()["lease_id"]
    assert (
        client.post(
            f"/api/jobs/{job}/cancel", headers={"X-Tab-Id": "new", "X-Writer-Lease": lease}
        ).status_code
        == 200
    )
    assert app.state.coordinator.get(job)["state"] == "cancelled"


@pytest.mark.parametrize(
    "fault", ["nested", "extra-project", "bool-size", "bad-prior", "wrong-request-prior"]
)
def test_malformed_stored_global_context_is_denied_without_cancelling(running, fault):
    app, client, _, _ = running
    context = {
        "request_id": str(uuid4()),
        "archive_kind": "native",
        "file_name": "source.zip",
        "file_size": 3,
    }
    if fault == "nested":
        context = {"request": context}
    elif fault == "extra-project":
        context["project_id"] = str(uuid4())
    elif fault == "bool-size":
        context["file_size"] = True
    else:
        receipt = build_import_receipt(
            context, uuid4().hex, "a" * 64, str(uuid4()), 0, "b" * 64, "c" * 64
        ).model_dump(mode="json")
        if fault == "bad-prior":
            receipt["public_result"]["revision"] = True
        else:
            receipt["request_id"] = str(uuid4())
        context["prior_receipt"] = receipt
    job = app.state.coordinator.reserve("project_import", context)
    response = client.post(f"/api/jobs/{job}/cancel")
    assert response.status_code == 422, response.text
    assert app.state.coordinator.get(job)["state"] == "reserved"


def test_valid_prior_global_context_can_cancel_without_lease(running):
    app, client, _, _ = running
    context = {
        "request_id": str(uuid4()),
        "archive_kind": "game",
        "file_name": "source.zip",
        "file_size": 3,
    }
    context["prior_receipt"] = build_import_receipt(
        context, uuid4().hex, "a" * 64, str(uuid4()), 0, "b" * 64, "c" * 64
    ).model_dump(mode="json")
    job = app.state.coordinator.reserve("project_import", context)
    assert client.post(f"/api/jobs/{job}/cancel").status_code == 200
    assert app.state.coordinator.get(job)["state"] == "cancelled"


def test_startup_has_import_handler_before_pending_recovery(tmp_path):
    config = tmp_path / "config"
    projects = tmp_path / "projects"
    OwnerAuth.setup(config / "owner.json", "secret")
    job_id = uuid4().hex
    context = {
        "request_id": str(uuid4()),
        "archive_kind": "native",
        "file_name": "source.zip",
        "file_size": 1,
    }
    receipt = build_import_receipt(context, job_id, "a" * 64, str(uuid4()), 0, "b" * 64, "c" * 64)
    jobs = config / "jobs"
    jobs.mkdir()
    (jobs / f"{job_id}.json").write_bytes(
        _json(
            {
                "id": job_id,
                "kind": "project_import",
                "state": "publication_pending",
                "context": context,
                "result": {"receipt": receipt.model_dump(mode="json")},
            }
        )
    )
    stage = projects / (".import-" + job_id)
    stage.mkdir(parents=True)
    marker = {
        "version": 1,
        "job_id": job_id,
        "project_id": receipt.project_id,
        "receipt_sha256": hashlib.sha256(_json(receipt.model_dump(mode="json"))).hexdigest(),
    }
    (stage / "marker.json").write_bytes(_json(marker))
    (stage / "partial").write_bytes(b"owned stage")
    app = create_app(projects, config)
    with TestClient(app):
        assert "project_import" in app.state.coordinator.handlers
        assert app.state.coordinator.get(job_id)["state"] == "failed"
        assert not app.state.coordinator.blocked
        assert not stage.exists()
