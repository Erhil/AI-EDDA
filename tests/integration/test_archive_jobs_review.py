"""Import admission identities, trusted retry evidence, and startup ordering."""

import copy
import hashlib
import json
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_import_worker import native_upload as native_upload
from test_native_media_review import media as media

from ai_edda.application import ApplicationService
from ai_edda.archive_jobs import ImportInProgress, reserve_project_import, start_project_import
from ai_edda.import_receipts import build_import_receipt, publish_import_receipt
from ai_edda.import_worker import import_worker
from ai_edda.project_store import ProjectStore
from ai_edda.work_coordinator import WorkCoordinator


class Coordinator:
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.jobs = SimpleNamespace(lock=threading.RLock(), jobs={})
        self.jobs.get = lambda jid: copy.deepcopy(self.jobs.jobs[jid])
        self.reserved, self.cancelled, self.started, self.trace = [], [], [], []

    def reserve(self, kind, context):
        assert self.jobs.lock._is_owned()
        jid = uuid4().hex
        self.reserved.append(jid)
        self.jobs.jobs[jid] = {
            "id": jid,
            "kind": kind,
            "context": copy.deepcopy(context),
            "state": "reserved",
        }
        (self.root / jid).mkdir()
        return jid

    @contextmanager
    def writer(self, jid):
        self.trace.append("writer-open")
        try:
            yield self.root / jid
        finally:
            self.trace.append("writer-closed")

    def start(self, jid, payload):
        assert not self.jobs.lock._is_owned()
        assert self.trace[-1] == "writer-closed"
        self.trace.append("started")
        self.started.append((jid, payload))

    def cancel(self, jid):
        self.cancelled.append(jid)


@pytest.fixture
def request_context():
    return {
        "request_id": str(uuid4()),
        "archive_kind": "native",
        "file_name": "source.zip",
        "file_size": 1,
    }


@pytest.fixture
def coordinator(tmp_path):
    return Coordinator(tmp_path / "coordinator")


def reserve(coordinator, request_context, store=None):
    return reserve_project_import(coordinator, store, **request_context)


@pytest.fixture
def completed(tmp_path, request_context, coordinator):
    with ProjectStore(tmp_path / "store") as store:
        app = ApplicationService(store)
        view = app.create_project(str(uuid4()), {})
        first = reserve(coordinator, request_context)
        record = build_import_receipt(
            request_context, first["job_id"], "a" * 64, view.project.id, 0, "b" * 64, "c" * 64
        )
        publish_import_receipt(store._dir(view.project.id), record)
        job = coordinator.jobs.jobs[first["job_id"]]
        job.update(
            state="completed",
            result={"receipt": record.model_dump(mode="json")},
            publication_result=record.public_result.model_dump(mode="json"),
        )
        yield store, coordinator, job, record


@pytest.mark.parametrize(
    "kind",
    ["audio_import", "cover_import", "analysis", "generation", "game_export", "native_backup"],
)
def test_request_uuid_collision_is_global(coordinator, request_context, kind):
    jid = uuid4().hex
    coordinator.jobs.jobs[jid] = {
        "id": jid,
        "kind": kind,
        "context": dict(request_context),
        "state": "failed",
    }
    with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
        reserve(coordinator, request_context)
    assert coordinator.reserved == []


@pytest.mark.parametrize(
    "state", ["reserved", "running", "ready", "publication_pending", "interrupted"]
)
def test_active_exact_retry_returns_existing_job_without_new_reservation(
    coordinator, request_context, state
):
    first = reserve(coordinator, request_context)
    job = coordinator.jobs.jobs[first["job_id"]]
    job["state"] = state
    if state == "interrupted":
        job["result"] = {"pending": True}
    with pytest.raises(ImportInProgress) as caught:
        reserve(coordinator, request_context)
    assert caught.value.job_id == first["job_id"]
    assert len(coordinator.reserved) == 1
    assert not coordinator.started


@pytest.mark.parametrize(
    "field, value", [("archive_kind", "game"), ("file_name", "different.zip"), ("file_size", 2)]
)
def test_same_uuid_with_changed_metadata_conflicts_before_reserve(
    coordinator, request_context, field, value
):
    reserve(coordinator, request_context)
    with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
        reserve(coordinator, {**request_context, field: value})
    assert len(coordinator.reserved) == 1


@pytest.mark.parametrize("state", ["failed", "cancelled", "interrupted"])
def test_terminal_attempt_without_result_can_retry(coordinator, request_context, state):
    first = reserve(coordinator, request_context)
    coordinator.jobs.jobs[first["job_id"]]["state"] = state
    second = reserve(coordinator, request_context)
    assert second["job_id"] != first["job_id"]
    assert second == {"job_id": second["job_id"], "upload_required": True, "verifying": False}
    assert coordinator.jobs.jobs[second["job_id"]]["context"] == request_context


def test_success_has_precedence_over_old_failure_and_requires_new_upload(
    completed, request_context
):
    store, coordinator, original, record = completed
    failed_id = uuid4().hex
    coordinator.jobs.jobs[failed_id] = {
        "id": failed_id,
        "kind": "project_import",
        "context": dict(request_context),
        "state": "failed",
    }
    second = reserve(coordinator, request_context, store)
    assert second["upload_required"] and second["verifying"]
    assert second["job_id"] not in (original["id"], failed_id)
    context = coordinator.jobs.jobs[second["job_id"]]["context"]
    assert set(context) == set(request_context) | {"prior_receipt"}
    assert context["prior_receipt"] == record.model_dump(mode="json")


def test_successful_verification_retry_preserves_original_receipt(completed, request_context):
    store, coordinator, original, record = completed
    second = reserve(coordinator, request_context, store)
    job = coordinator.jobs.jobs[second["job_id"]]
    job.update(
        state="completed",
        result=copy.deepcopy(original["result"]),
        publication_result=copy.deepcopy(original["publication_result"]),
    )
    third = reserve(coordinator, request_context, store)
    assert third["verifying"] and third["upload_required"]
    assert (
        coordinator.jobs.jobs[third["job_id"]]["context"]["prior_receipt"]["job_id"]
        == record.job_id
    )


def test_retry_after_edits_reads_receipt_without_loading_current_head(
    completed, request_context, monkeypatch
):
    store, coordinator, _, record = completed
    app = ApplicationService(store)
    view = app.get(record.project_id)
    app.execute(
        view.project.id,
        {
            "command_id": "edited-after-import",
            "kind": "update_song_metadata",
            "payload": {"title": "edited"},
            "base_project_revision": 0,
            "expected_grid_revision": 0,
            "expected_chart_revisions": {c.id: 0 for c in view.charts},
        },
    )
    original_open = Path.open

    def opened(path, *args, **kwargs):
        assert path.name != "project.json", "Admission reloaded the mutable current head"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(
        store, "load", lambda *args: pytest.fail("Admission called ProjectStore.load")
    )
    assert reserve(coordinator, request_context, store)["verifying"]


@pytest.mark.parametrize(
    "field", ["context_size", "public_revision", "prior_revision", "archive_kind"]
)
def test_corrupt_completed_evidence_cannot_authorize_verification(
    completed, request_context, field
):
    store, coordinator, job, record = completed
    if field == "context_size":
        job["context"]["file_size"] = True
    elif field == "public_revision":
        job["publication_result"]["revision"] = False
    elif field == "prior_revision":
        # A completed verification job points to its original publication receipt.
        old_id = job["id"]
        new_id = uuid4().hex
        coordinator.jobs.jobs[new_id] = coordinator.jobs.jobs.pop(old_id)
        job["id"] = new_id
        job["context"]["prior_receipt"] = copy.deepcopy(job["result"]["receipt"])
        job["context"]["prior_receipt"]["public_result"]["revision"] = False
    else:
        raw = copy.deepcopy(job["result"]["receipt"])
        raw["public_result"]["archive_kind"] = "game"
        job["result"]["receipt"] = raw
        job["publication_result"] = copy.deepcopy(raw["public_result"])
        path = store._dir(record.project_id) / "imports" / f"{record.request_id}.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
    count = len(coordinator.reserved)
    with pytest.raises(ValueError):
        reserve(coordinator, request_context, store)
    assert len(coordinator.reserved) == count


@pytest.mark.parametrize(
    "corruption", ["missing_result", "missing_receipt", "bad_shape", "bad_hash"]
)
def test_malformed_completed_result_is_validation_error(completed, request_context, corruption):
    store, coordinator, job, _ = completed
    if corruption == "missing_result":
        job.pop("result")
    elif corruption == "missing_receipt":
        job["result"] = {}
    elif corruption == "bad_shape":
        job["result"] = []
    else:
        job["result"]["receipt"]["request_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        reserve(coordinator, request_context, store)
    assert len(coordinator.reserved) == 1


def test_failed_job_prior_receipt_is_not_trusted(coordinator, request_context):
    first = reserve(coordinator, request_context)
    job = coordinator.jobs.jobs[first["job_id"]]
    job["state"] = "failed"
    job["context"]["prior_receipt"] = {"untrusted": "incoming archive receipt"}
    second = reserve(coordinator, request_context)
    assert not second["verifying"]
    assert coordinator.jobs.jobs[second["job_id"]]["context"] == request_context


def test_start_closes_writer_before_dispatch_and_preserves_flat_payload(
    coordinator, request_context
):
    job = reserve(coordinator, request_context)
    start_project_import(coordinator, job["job_id"])
    assert coordinator.trace == ["writer-open", "writer-closed", "started"]
    jid, payload = coordinator.started[0]
    assert payload == {
        "workspace": str((coordinator.root / jid).resolve()),
        "job_id": jid,
        "request_context": request_context,
    }
    assert not coordinator.cancelled


@pytest.mark.parametrize("failure", ["writer", "start"])
def test_start_failure_cancels_only_its_owned_reservation(
    coordinator, request_context, monkeypatch, failure
):
    job = reserve(coordinator, request_context)
    error = OSError("startup failed")
    if failure == "writer":

        @contextmanager
        def writer(*args):
            raise error
            yield

        monkeypatch.setattr(coordinator, "writer", writer)
    else:

        def start(*args):
            raise error

        monkeypatch.setattr(coordinator, "start", start)
    with pytest.raises(OSError) as caught:
        start_project_import(coordinator, job["job_id"])
    assert caught.value is error
    assert coordinator.cancelled == [job["job_id"]]


def test_start_rejects_other_job_kind_without_dispatch_or_cancel(coordinator, request_context):
    job = reserve(coordinator, request_context)
    coordinator.jobs.jobs[job["job_id"]]["kind"] = "audio_import"
    with pytest.raises(ValueError):
        start_project_import(coordinator, job["job_id"])
    assert not coordinator.started
    assert not coordinator.cancelled


def test_unknown_job_does_not_cancel_an_unowned_id(coordinator):
    with pytest.raises((ValueError, KeyError)):
        start_project_import(coordinator, uuid4().hex)
    assert not coordinator.cancelled


@pytest.mark.parametrize(
    "state",
    ["running", "ready", "publication_pending", "completed", "failed", "cancelled", "interrupted"],
)
def test_start_rejects_nonreserved_state_before_writer_or_cancel(
    coordinator, request_context, monkeypatch, state
):
    admitted = reserve(coordinator, request_context)
    coordinator.jobs.jobs[admitted["job_id"]]["state"] = state

    @contextmanager
    def forbidden_writer(*args):
        pytest.fail("A nonreserved job reached writer admission")
        yield

    monkeypatch.setattr(coordinator, "writer", forbidden_writer)
    with pytest.raises(ValueError):
        start_project_import(coordinator, admitted["job_id"])
    assert not coordinator.started
    assert not coordinator.cancelled


def test_completed_context_cannot_add_fields_outside_frozen_schema(completed, request_context):
    store, coordinator, job, _ = completed
    job["context"]["unexpected"] = "not part of the frozen request"
    count = len(coordinator.reserved)
    with pytest.raises(ValueError):
        reserve(coordinator, request_context, store)
    assert len(coordinator.reserved) == count


def test_real_coordinator_spawns_import_worker_with_uploaded_source_zip(tmp_path, native_upload):
    source_workspace, data, _, _ = native_upload

    def publish(context, result, workspace):
        return result["receipt"]["public_result"]

    handlers = {
        "project_import": {
            "worker": import_worker,
            "publish": publish,
            "reconcile": lambda *args: (False, None),
        }
    }
    coordinator = WorkCoordinator(tmp_path / "real-coordinator", handlers)
    try:
        admitted = reserve(coordinator, data["request_context"])
        jid = admitted["job_id"]
        with coordinator.writer(jid) as workspace:
            shutil.copyfile(source_workspace / "source.zip", workspace / "source.zip")
        start_project_import(coordinator, jid)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = coordinator.jobs.get(jid)
            if job["state"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.05)
        assert job["state"] == "completed", job
        assert job["context"] == data["request_context"]
        assert job["result"]["receipt"]["job_id"] == jid
        assert (
            job["result"]["receipt"]["archive_sha256"]
            == hashlib.sha256((source_workspace / "source.zip").read_bytes()).hexdigest()
        )
        assert job["publication_result"]["archive_kind"] == "native"
    finally:
        coordinator.close()
