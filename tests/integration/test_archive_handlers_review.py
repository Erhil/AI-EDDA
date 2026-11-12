import copy
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
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
from test_project_import_publication_review import prepared as prepared

from ai_edda import archive_handlers, project_imports
from ai_edda.application import ApplicationService
from ai_edda.archive_handlers import make_import_handlers
from ai_edda.native_project import validate_native_project
from ai_edda.project_store import ProjectStore
from ai_edda.work_coordinator import WorkCoordinator


def files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".writer.lock"
    }


def wait_for(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("coordinator did not reach the expected state")


def run_job(coordinator, source, context):
    job_id = coordinator.reserve("project_import", context)
    with coordinator.writer(job_id) as workspace:
        shutil.copyfile(source, workspace / "source.zip")
    payload = {
        "workspace": str(workspace.resolve()),
        "job_id": job_id,
        "request_context": {
            key: context[key] for key in ("request_id", "archive_kind", "file_name", "file_size")
        },
    }
    if "prior_receipt" in context:
        payload["prior_receipt"] = context["prior_receipt"]
    coordinator.start(job_id, payload)
    return job_id


def terminal(coordinator, job_id):
    job = wait_for(
        lambda: (
            snapshot
            if (snapshot := coordinator.get(job_id))["state"]
            in ("completed", "failed", "cancelled")
            else None
        )
    )
    assert job["state"] == "completed", job
    wait_for(lambda: not coordinator._workspace(job_id).exists())
    assert coordinator.jobs.current is None and not coordinator.blocked
    return job


@pytest.mark.parametrize("kind", ["native", "game"])
def test_actual_windows_spawn_import_publishes_complete_project_and_survives_restart(
    tmp_path, request, kind
):
    upload = request.getfixturevalue("native_upload" if kind == "native" else "game_upload")
    source = upload[0] / "source.zip"
    context = copy.deepcopy(upload[1]["request_context"])
    context_before, source_before = copy.deepcopy(context), source.read_bytes()
    native_before = files(upload[2]) if kind == "native" else None
    config = tmp_path / "config"
    with ProjectStore(tmp_path / "published") as store:
        coordinator = WorkCoordinator(config, make_import_handlers(store))
        try:
            assert coordinator.jobs.ctx.get_start_method() == "spawn"
            job_id = run_job(coordinator, source, context)
            job = terminal(coordinator, job_id)
            assert set(job["result"]) == {"receipt"}
            receipt = job["result"]["receipt"]
            assert receipt["job_id"] == job_id
            assert job["publication_result"] == receipt["public_result"]
            assert job["context"] == context_before
            root = store._dir(receipt["project_id"])
            view = store.load(receipt["project_id"])
            assert validate_native_project(root, receipt["project_id"]) == view
            assert view.project.revision == receipt["public_result"]["revision"]
            if kind == "native":
                imported = files(root)
                assert set(imported) == set(native_before) | {
                    f"imports/{context['request_id']}.json"
                }
                assert all(imported[path] == data for path, data in native_before.items())
                assert files(upload[2]) == native_before
            else:
                assert view.project.revision == 0 and len(view.charts) == 3
                assert view.project.provenance.created_by == "imported"
                with zipfile.ZipFile(source) as archive:
                    assert (root / view.project.audio.path).read_bytes() == archive.read("song.ogg")
                    assert (root / view.project.cover.path).read_bytes() == archive.read(
                        "cover.jpg"
                    )
            published_before = files(root)
        finally:
            coordinator.close()
        restarted = WorkCoordinator(config, make_import_handlers(store))
        try:
            assert restarted.get(job_id)["state"] == "completed"
            assert restarted.get(job_id)["publication_result"] == receipt["public_result"]
            assert not restarted.blocked and files(root) == published_before
        finally:
            restarted.close()
    assert context == context_before and source.read_bytes() == source_before


def test_actual_spawn_prior_verification_keeps_original_receipt_and_later_head(
    native_upload, tmp_path
):
    workspace, payload, _, _ = native_upload
    source, context = workspace / "source.zip", payload["request_context"]
    with ProjectStore(tmp_path / "published") as store:
        coordinator = WorkCoordinator(tmp_path / "config", make_import_handlers(store))
        try:
            original = terminal(coordinator, run_job(coordinator, source, context))
            receipt = original["result"]["receipt"]
            service = ApplicationService(store)
            view = service.get(receipt["project_id"])
            service.execute(
                view.project.id,
                {
                    "command_id": str(uuid4()),
                    "kind": "update_song_metadata",
                    "payload": {"title": "Later"},
                    "base_project_revision": view.project.revision,
                    "expected_grid_revision": view.grid.revision,
                    "expected_chart_revisions": {chart.id: chart.revision for chart in view.charts},
                },
            )
            before = files(store._dir(receipt["project_id"]))
            retry_context = {**context, "prior_receipt": receipt}
            context_before = copy.deepcopy(retry_context)
            retry = terminal(coordinator, run_job(coordinator, source, retry_context))
            assert retry["id"] != receipt["job_id"]
            assert retry["result"]["receipt"] == receipt
            assert retry["publication_result"] == original["publication_result"]
            assert files(store._dir(receipt["project_id"])) == before
            assert retry_context == context_before
        finally:
            coordinator.close()


def persist_pending(config, value, context, state="publication_pending"):
    directory = config / "jobs"
    directory.mkdir(parents=True)
    record = {
        "id": value["job_id"],
        "kind": "project_import",
        "state": state,
        "context": context,
        "result": value["result"],
        "publication_error": "interrupted publication",
    }
    (directory / (value["job_id"] + ".json")).write_text(json.dumps(record), encoding="utf-8")


@pytest.mark.parametrize("renamed", [False, True])
@pytest.mark.parametrize("state", ["publication_pending", "interrupted"])
def test_registered_adapter_recovers_pending_publication_without_workspace(
    prepared, tmp_path, monkeypatch, renamed, state
):
    value = prepared
    original_rename = project_imports.os.rename

    def interrupted(source, target):
        if renamed:
            original_rename(source, target)
        raise OSError("interrupted publication")

    with monkeypatch.context() as patch:
        patch.setattr(project_imports.os, "rename", interrupted)
        patch.setattr(project_imports, "_cleanup_owned_wrapper", lambda *args: None)
        with pytest.raises(OSError):
            project_imports.publish_project_import(
                value["store"], value["context"], value["result"], value["workspace"]
            )
    flat = copy.deepcopy(value["context"]["request"])
    if renamed:
        service = ApplicationService(value["store"])
        view = service.get(value["receipt"]["project_id"])
        service.execute(
            view.project.id,
            {
                "command_id": str(uuid4()),
                "kind": "update_song_metadata",
                "payload": {"title": "Changed before recovery"},
                "base_project_revision": view.project.revision,
                "expected_grid_revision": view.grid.revision,
                "expected_chart_revisions": {chart.id: chart.revision for chart in view.charts},
            },
        )
        before = files(value["store"]._dir(view.project.id))
    shutil.rmtree(value["workspace"])
    config = tmp_path / "recovery-config"
    persist_pending(config, value, flat, state)
    coordinator = WorkCoordinator(config, make_import_handlers(value["store"]))
    try:
        job = coordinator.get(value["job_id"])
        assert job["state"] == ("completed" if renamed else "failed"), job
        assert not coordinator.blocked and coordinator.jobs.current is None
        assert not (value["store"].root / (".import-" + value["job_id"])).exists()
        assert job["context"] == flat and "request" not in job["context"]
        if renamed:
            assert job["publication_result"] == value["receipt"]["public_result"]
            assert files(value["store"]._dir(value["receipt"]["project_id"])) == before
        else:
            assert not value["store"]._dir(value["receipt"]["project_id"]).exists()
    finally:
        coordinator.close()


def test_real_spawn_terminal_save_failure_recovers_after_restart_and_later_edit(
    game_upload, tmp_path, monkeypatch
):
    workspace, payload = game_upload
    config = tmp_path / "config"
    with ProjectStore(tmp_path / "published") as store:
        coordinator = WorkCoordinator(config, make_import_handlers(store))
        original_save = coordinator.jobs._save

        def fail_terminal(job):
            if job["state"] == "completed":
                raise OSError("terminal persistence unavailable")
            return original_save(job)

        with monkeypatch.context() as patch:
            patch.setattr(coordinator.jobs, "_save", fail_terminal)
            try:
                job_id = run_job(coordinator, workspace / "source.zip", payload["request_context"])
                job = wait_for(
                    lambda: (
                        current
                        if (current := coordinator.get(job_id))["state"] == "publication_pending"
                        and coordinator.blocked
                        else None
                    )
                )
                assert store.load(job["result"]["receipt"]["project_id"])
            finally:
                coordinator.close()
        service = ApplicationService(store)
        receipt = job["result"]["receipt"]
        view = service.get(receipt["project_id"])
        later = service.execute(
            view.project.id,
            {
                "command_id": str(uuid4()),
                "kind": "update_song_metadata",
                "payload": {"title": "Edited after publication"},
                "base_project_revision": view.project.revision,
                "expected_grid_revision": view.grid.revision,
                "expected_chart_revisions": {chart.id: chart.revision for chart in view.charts},
            },
        )
        abandoned = config / "workspaces" / job_id
        assert abandoned.parent.resolve() == (config / "workspaces").resolve()
        shutil.rmtree(abandoned)
        before = files(store._dir(receipt["project_id"]))
        restarted = WorkCoordinator(config, make_import_handlers(store))
        try:
            assert restarted.get(job_id)["state"] == "completed"
            assert restarted.get(job_id)["publication_result"] == receipt["public_result"]
            assert not restarted.blocked
            assert service.get(later.project.id) == later
            assert files(store._dir(receipt["project_id"])) == before
        finally:
            restarted.close()


@pytest.mark.parametrize("operation", ["publish", "reconcile"])
def test_prior_context_projection_preserves_flat_input_and_exact_receipt(
    monkeypatch, operation, tmp_path
):
    context = {
        "request_id": "request",
        "archive_kind": "native",
        "file_name": "x.zip",
        "file_size": 1,
        "prior_receipt": {"job_id": "original-job", "nested": {"value": 1}},
    }
    before = copy.deepcopy(context)
    calls = []
    name = "publish_project_import" if operation == "publish" else "reconcile_project_import"
    monkeypatch.setattr(archive_handlers, name, lambda *args: calls.append(args))
    handler = make_import_handlers("store")["project_import"][operation]
    if operation == "publish":
        handler(context, {"receipt": "result"}, tmp_path)
    else:
        handler("job", context, {"receipt": "result"})
    projected = calls[0][1 if operation == "publish" else 2]
    assert set(projected) == {"request", "prior_receipt"}
    assert projected["request"] == {
        key: context[key] for key in ("request_id", "archive_kind", "file_name", "file_size")
    }
    assert projected["prior_receipt"] == context["prior_receipt"]
    assert context == before


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"request": {}},
        {
            "request_id": "request",
            "archive_kind": "native",
            "file_name": "a.zip",
            "file_size": 1,
            "extra": True,
        },
    ],
)
def test_invalid_recovery_context_is_rejected_before_destination_access(monkeypatch, context):
    def forbidden(*args):
        raise AssertionError("invalid adapter context reached destination recovery")

    monkeypatch.setattr(archive_handlers, "reconcile_project_import", forbidden)
    before = copy.deepcopy(context)
    with pytest.raises(ValueError, match="Invalid import context"):
        make_import_handlers(None)["project_import"]["reconcile"]("job", context, {"receipt": {}})
    assert context == before


def test_actual_spawn_cross_volume_store_publication(game_upload, tmp_path):
    workspace, payload = game_upload
    scratch = Path(__file__).resolve().parents[2] / "src" / "scratch" / "github-preparation"
    scratch.mkdir(parents=True, exist_ok=True)
    assert scratch.resolve().is_relative_to(Path(__file__).resolve().parents[2])
    if os.name != "nt" or scratch.drive.lower() == workspace.drive.lower():
        pytest.skip("no second Windows test volume is available")
    with tempfile.TemporaryDirectory(prefix="archive-adapter-volume-", dir=scratch) as directory:
        assert Path(directory).resolve().is_relative_to(scratch.resolve())
        with ProjectStore(Path(directory) / "store") as store:
            coordinator = WorkCoordinator(tmp_path / "config", make_import_handlers(store))
            try:
                job = terminal(
                    coordinator,
                    run_job(coordinator, workspace / "source.zip", payload["request_context"]),
                )
                result = job["publication_result"]
                root = store._dir(result["project_id"])
                assert root.drive.lower() != workspace.drive.lower()
                assert (
                    validate_native_project(root, result["project_id"]).project.revision
                    == result["revision"]
                )
                assert not list(store.root.glob(".import-*"))
            finally:
                coordinator.close()
