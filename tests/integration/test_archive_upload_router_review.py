import asyncio
import copy
import json
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
from ai_edda.application import ApplicationService
from ai_edda.archive_handlers import make_import_handlers
from ai_edda.import_receipts import build_import_receipt
from ai_edda.jobs import BusyError
from ai_edda.project_store import ProjectStore
from ai_edda.work_coordinator import WorkCoordinator


def headers(size=3):
    return {
        "content-type": "application/octet-stream",
        "cookie": "ai_edda_session=owner",
        "x-request-id": str(uuid4()),
        "x-archive-kind": "native",
        "x-file-name": "map%20name.zip",
        "x-file-size": str(size),
    }


async def invoke(app, request_headers, messages=None, receive_failure=None, delay=0):
    received, sent, error = 0, [], None
    messages = iter(
        messages
        if messages is not None
        else [{"type": "http.request", "body": b"zip", "more_body": False}]
    )

    async def receive():
        nonlocal received
        received += 1
        if delay:
            await asyncio.sleep(delay)
        if receive_failure:
            raise receive_failure
        return next(messages, {"type": "http.disconnect"})

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "scheme": "http",
        "http_version": "1.1",
        "path": "/api/projects/imports",
        "raw_path": b"/api/projects/imports",
        "query_string": b"",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in request_headers.items()
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "root_path": "",
    }
    try:
        await app(scope, receive, send)
    except BaseException as exc:
        error = exc
    start = next((message for message in sent if message["type"] == "http.response.start"), {})
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return SimpleNamespace(status=start.get("status"), body=body, received=received, error=error)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    state = {
        "cancelled": [],
        "reserved": [],
        "writes": [],
        "writing": False,
        "stream": None,
        "started": [],
    }
    job_id = "a" * 32

    class Coordinator:
        @contextmanager
        def writer(self, job):
            assert job == job_id
            state["writing"] = True
            try:
                yield tmp_path
            finally:
                state["writing"] = False

        def cancel(self, job):
            state["cancelled"].append(job)
            if state.get("cancel_error"):
                raise OSError("cancellation persistence failed")

    coordinator = Coordinator()

    def reserve(*args):
        request_id, kind, name, size = args[2:]
        build_import_receipt(
            {"request_id": request_id, "archive_kind": kind, "file_name": name, "file_size": size},
            job_id,
            "a" * 64,
            str(uuid4()),
            0,
            "b" * 64,
            "c" * 64,
        )
        state["reserved"].append((request_id, kind, name, size))
        return {"job_id": job_id, "upload_required": True, "verifying": False}

    def start(*args):
        assert not state["writing"]
        assert state["stream"].closed
        state["started"].append(args[1])

    original = Path.open

    class Output:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, data):
            state["writes"].append(len(data))
            if state.get("disk_error"):
                raise OSError("disk full")
            return self.stream.write(data)

    def opened(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        if path.name == "source.zip" and args and args[0] == "wb":
            state["stream"] = stream
            return Output(stream)
        return stream

    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(archives, "reserve_project_import", reserve)
    monkeypatch.setattr(archives, "start_project_import", start)
    app = FastAPI()
    app.include_router(
        archives.create_archive_router(
            SimpleNamespace(store=None),
            SimpleNamespace(authenticate=lambda token: token == "owner"),
            coordinator,
        )
    )
    return app, state, coordinator


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"cookie": ""}, 401),
        ({"cookie": "ai_edda_session=invalid"}, 401),
        ({"content-type": "application/json"}, 422),
        ({"x-request-id": "bad"}, 422),
        ({"x-request-id": str(uuid4()).upper()}, 422),
        ({"x-archive-kind": "other"}, 422),
        ({"x-file-size": "0"}, 422),
        ({"x-file-size": "-1"}, 422),
        ({"x-file-size": "NaN"}, 422),
        ({"x-file-size": str(1024**3 + 1)}, 413),
        ({"x-file-size": "9" * 3000}, 413),
        ({"x-file-size": "9" * 5000}, 422),
        ({"content-length": "4"}, 422),
        ({"content-length": str(1024**3 + 1)}, 413),
        ({"x-file-name": "%FF.zip"}, 422),
        ({"x-file-name": ""}, 422),
    ],
)
def test_auth_and_bad_headers_reject_before_any_receive(fake, changes, status):
    app, state, _ = fake
    response = asyncio.run(invoke(app, {**headers(), **changes}))
    assert response.status == status and response.received == 0
    assert not state["reserved"] and not state["cancelled"]


@pytest.mark.parametrize("name", ["map%.zip", "map%2.zip", "map%GG.zip"])
def test_malformed_percent_encoding_is_rejected_before_body(fake, name):
    app, state, _ = fake
    response = asyncio.run(invoke(app, {**headers(), "x-file-name": name}))
    assert response.status == 422 and response.received == 0
    assert not state["reserved"]


def test_valid_encoded_literal_percent_remains_valid_metadata(fake):
    app, state, _ = fake
    response = asyncio.run(invoke(app, {**headers(), "x-file-name": "map%25name.zip"}))
    assert response.status == 202
    assert state["reserved"][0][2] == "map%name.zip"


@pytest.mark.parametrize(
    "failure,status,detail",
    [
        (BusyError(), 409, "Coordinator busy"),
        (
            archives.ImportInProgress("foreign-job"),
            409,
            {"code": "IMPORT_IN_PROGRESS", "job_id": "foreign-job"},
        ),
        (ValueError("REQUEST_ID_CONFLICT"), 409, {"code": "REQUEST_ID_CONFLICT"}),
        (OSError(), 503, "Storage unavailable"),
    ],
)
def test_admission_rejection_never_receives_or_cancels_foreign_job(
    fake, monkeypatch, failure, status, detail
):
    app, state, _ = fake

    def reject(*args):
        raise failure

    monkeypatch.setattr(archives, "reserve_project_import", reject)
    response = asyncio.run(invoke(app, headers()))
    assert response.status == status and response.received == 0
    assert json.loads(response.body)["detail"] == detail
    assert not state["cancelled"]


@pytest.mark.parametrize(
    "body,size,status", [(b"zip!", 3, 422), (b"zi", 3, 422), (b"123456789", 8, 413)]
)
def test_actual_counts_reject_overrun_and_truncation_and_cancel_owned_job(
    fake, monkeypatch, body, size, status
):
    app, state, _ = fake
    if status == 413:
        monkeypatch.setattr(archives, "MAX_SIZE", 8)
    response = asyncio.run(
        invoke(app, headers(size), [{"type": "http.request", "body": body, "more_body": False}])
    )
    assert response.status == status and response.received == 1
    assert state["cancelled"] == ["a" * 32] and not state["started"]
    assert state["stream"].closed and not state["writing"]


def test_disconnect_task_cancellation_and_disk_failure_release_owned_writer(fake):
    app, state, _ = fake
    response = asyncio.run(invoke(app, headers(), [{"type": "http.disconnect"}]))
    assert response.status == 400 and state["stream"].closed
    response = asyncio.run(invoke(app, headers(), receive_failure=asyncio.CancelledError()))
    assert isinstance(response.error, asyncio.CancelledError) and state["stream"].closed
    state["disk_error"] = True
    response = asyncio.run(invoke(app, headers()))
    assert response.status == 503 and state["stream"].closed
    assert state["cancelled"] == ["a" * 32] * 3 and not state["writing"]


def test_sixty_second_idle_timeout_maps_to_408_and_cancels(fake, monkeypatch):
    app, state, _ = fake
    original = asyncio.wait_for

    async def fast_timeout(awaitable, timeout):
        assert timeout == 60
        return await original(awaitable, 0.001)

    monkeypatch.setattr(archives.asyncio, "wait_for", fast_timeout)
    response = asyncio.run(invoke(app, headers(), delay=0.02))
    assert response.status == 408 and state["cancelled"] == ["a" * 32]
    assert state["stream"].closed and not state["writing"]


def test_cancellation_storage_failure_does_not_escape_as_server_error(fake):
    app, state, _ = fake
    state["disk_error"] = state["cancel_error"] = True
    response = asyncio.run(invoke(app, headers()))
    assert response.status == 503, response.error
    assert response.error is None
    assert state["cancelled"] == ["a" * 32] and state["stream"].closed


def test_large_received_chunk_is_written_in_one_mib_pieces_without_body_copy(fake):
    app, state, _ = fake
    chunk = b"x" * (12 * 1024 * 1024)
    request_headers = headers(len(chunk))
    tracemalloc.start()
    try:
        response = asyncio.run(
            invoke(
                app, request_headers, [{"type": "http.request", "body": chunk, "more_body": False}]
            )
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.status == 202 and response.received == 1
    assert max(state["writes"]) <= 1024 * 1024 and sum(state["writes"]) == len(chunk)
    assert peak < 4 * 1024 * 1024
    assert state["reserved"][0][2] == "map name.zip"
    assert state["started"] == ["a" * 32] and not state["cancelled"]


def wait_job(coordinator, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = coordinator.get(job_id)
        if job["state"] in ("completed", "failed", "cancelled"):
            assert job["state"] == "completed", job
            return job
        time.sleep(0.05)
    raise AssertionError("import job did not complete")


@pytest.mark.parametrize("kind", ["native", "game"])
def test_real_router_upload_and_completed_retry_always_upload_new_bytes(tmp_path, request, kind):
    upload = request.getfixturevalue("native_upload" if kind == "native" else "game_upload")
    body = (upload[0] / "source.zip").read_bytes()
    OwnerAuth.setup(tmp_path / "owner.json", "secret")
    auth = OwnerAuth(tmp_path / "owner.json")
    with ProjectStore(tmp_path / "published") as store:
        service = ApplicationService(store)
        coordinator = WorkCoordinator(tmp_path / "config", make_import_handlers(store))
        try:
            app = FastAPI()
            app.include_router(archives.create_archive_router(service, auth, coordinator))
            client = TestClient(app)
            client.cookies.set("ai_edda_session", auth.login("secret"))
            request_headers = {
                key: value for key, value in headers(len(body)).items() if key != "cookie"
            }
            request_headers["x-archive-kind"] = kind
            first = client.post("/api/projects/imports", content=body, headers=request_headers)
            assert first.status_code == 202, first.text
            original = wait_job(coordinator, first.json()["job_id"])
            receipt = original["result"]["receipt"]
            second = client.post("/api/projects/imports", content=body, headers=request_headers)
            assert second.status_code == 202, second.text
            assert second.json()["upload_required"] is True and second.json()["verifying"] is True
            assert second.json()["job_id"] != first.json()["job_id"]
            retry = wait_job(coordinator, second.json()["job_id"])
            assert retry["result"]["receipt"] == receipt
            assert retry["publication_result"] == original["publication_result"]
            assert (
                store.load(receipt["project_id"]).project.revision
                == receipt["public_result"]["revision"]
            )
        finally:
            coordinator.close()


@pytest.mark.parametrize("mode", ["active", "busy", "conflict"])
def test_real_admission_rejects_before_receive_and_preserves_active_job(
    tmp_path, monkeypatch, mode
):
    request_headers = headers()
    request_context = {
        "request_id": request_headers["x-request-id"],
        "archive_kind": "native",
        "file_name": "map name.zip",
        "file_size": 3,
    }
    with ProjectStore(tmp_path / "published") as store:
        coordinator = WorkCoordinator(tmp_path / "config", make_import_handlers(store))
        try:
            active = coordinator.reserve("project_import", request_context)
            if mode == "busy":
                request_headers["x-request-id"] = str(uuid4())
            elif mode == "conflict":
                request_headers["x-file-name"] = "other.zip"
            cancelled = []
            original = coordinator.cancel
            monkeypatch.setattr(
                coordinator, "cancel", lambda job: (cancelled.append(job), original(job))[1]
            )
            app = FastAPI()
            app.include_router(
                archives.create_archive_router(
                    SimpleNamespace(store=store),
                    SimpleNamespace(authenticate=lambda token: token == "owner"),
                    coordinator,
                )
            )
            before = copy.deepcopy(coordinator.get(active))
            response = asyncio.run(invoke(app, request_headers))
            assert response.status == 409 and response.received == 0
            if mode == "active":
                assert json.loads(response.body)["detail"] == {
                    "code": "IMPORT_IN_PROGRESS",
                    "job_id": active,
                }
            elif mode == "conflict":
                assert json.loads(response.body)["detail"] == {"code": "REQUEST_ID_CONFLICT"}
            else:
                assert json.loads(response.body)["detail"] == "Coordinator busy"
            assert not cancelled and coordinator.get(active) == before
            assert list(coordinator.jobs.jobs) == [active] and coordinator.jobs.current == active
        finally:
            coordinator.close()


def test_real_cancel_persistence_failure_returns_controlled_storage_error(tmp_path, monkeypatch):
    with ProjectStore(tmp_path / "published") as store:
        coordinator = WorkCoordinator(tmp_path / "config", make_import_handlers(store))
        app = FastAPI()
        app.include_router(
            archives.create_archive_router(
                SimpleNamespace(store=store),
                SimpleNamespace(authenticate=lambda token: token == "owner"),
                coordinator,
            )
        )
        original = coordinator.jobs._save

        def fail_cancel(job):
            if job["state"] == "cancelled":
                raise OSError("cancel persistence unavailable")
            return original(job)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(coordinator.jobs, "_save", fail_cancel)
                response = asyncio.run(
                    invoke(
                        app,
                        headers(),
                        [{"type": "http.request", "body": b"overrun", "more_body": False}],
                    )
                )
            assert response.status == 503, response.error
            assert response.error is None
            assert len(coordinator.jobs.jobs) == 1
            assert next(iter(coordinator.jobs.jobs.values()))["state"] == "cancelled"
        finally:
            coordinator.close()


def test_start_failure_cleanup_never_cancels_a_new_foreign_job(tmp_path, monkeypatch):
    with ProjectStore(tmp_path / "published") as store:
        coordinator = WorkCoordinator(tmp_path / "config", make_import_handlers(store))
        app = FastAPI()
        app.include_router(
            archives.create_archive_router(
                SimpleNamespace(store=store),
                SimpleNamespace(authenticate=lambda token: token == "owner"),
                coordinator,
            )
        )
        cancelled, foreign = [], []
        original_cancel = coordinator.cancel

        def cancel_and_reserve(job_id):
            cancelled.append(job_id)
            result = original_cancel(job_id)
            if not foreign:
                foreign.append(
                    coordinator.reserve(
                        "project_import",
                        {
                            "request_id": str(uuid4()),
                            "archive_kind": "game",
                            "file_name": "foreign.zip",
                            "file_size": 3,
                        },
                    )
                )
            return result

        def fail_start(*args):
            assert not any(coordinator.writers.values()), "upload writer must close before startup"
            raise OSError("worker process could not start")

        try:
            with monkeypatch.context() as patch:
                patch.setattr(coordinator, "start", fail_start)
                patch.setattr(coordinator, "cancel", cancel_and_reserve)
                response = asyncio.run(invoke(app, headers()))
            assert response.status == 503
            assert cancelled and len(set(cancelled)) == 1
            assert foreign[0] not in cancelled
            assert coordinator.get(foreign[0])["state"] == "reserved"
            assert coordinator.jobs.current == foreign[0]
        finally:
            coordinator.close()
