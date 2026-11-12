"""Verify worker capture isolation, complete validation, and owned cleanup."""

import hashlib
import json
import multiprocessing as mp
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from test_native_generation_review import generation as generation
from test_native_generation_review import inputs as inputs
from test_native_generation_review import media as media

from ai_edda import native_export_worker as module
from ai_edda.application import ApplicationService
from ai_edda.native_capture import capture_inventory
from ai_edda.native_export_worker import native_export_worker
from ai_edda.project_store import ProjectStore


def files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def payload(root, snapshot, workspace):
    workspace.mkdir()
    return {
        "inventory": capture_inventory(
            root, snapshot.project.id, (root / "project.json").read_bytes()
        ),
        "project_revision": snapshot.project.revision,
        "workspace": str(workspace),
    }


@pytest.fixture
def case(tmp_path):
    with ProjectStore(tmp_path / "store") as store:
        view = ApplicationService(store).create_project(str(uuid4()), {})
        snapshot = store.load(view.project.id)
        root = store._dir(snapshot.project.id)
    return root, snapshot, payload(root, snapshot, tmp_path / "workspace")


def assert_clean(data):
    workspace = Path(data["workspace"])
    assert not (workspace / "prepared").exists()
    assert not (workspace / "verification").exists()


def assert_result(data, result, expected_files):
    workspace = Path(data["workspace"])
    archive = workspace / "prepared/backup.zip"
    assert set(result) == {
        "project_id",
        "project_revision",
        "archive_sha256",
        "archive_size",
        "envelope_sha256",
        "head_sha256",
    }
    assert json.loads(json.dumps(result)) == result
    assert result["project_id"] == data["inventory"].project_id
    assert (
        type(result["project_revision"]) is int
        and result["project_revision"] == data["project_revision"]
    )
    assert type(result["archive_size"]) is int and result["archive_size"] == archive.stat().st_size
    assert result["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert result["head_sha256"] == hashlib.sha256(data["inventory"].head).hexdigest()
    assert (
        result["envelope_sha256"]
        == hashlib.sha256((workspace / "verification/archive.json").read_bytes()).hexdigest()
    )
    assert files(workspace / "verification/project") == expected_files


def test_historical_multi_catalog_capture_is_preserved(generation, tmp_path):
    root, snapshot = generation["root"], generation["source"]
    before = files(root)
    assert {"audio", "assets", "analysis", "targets", "plan-inputs", "plans", "generation"} <= {
        name.split("/")[0] for name in before
    }
    assert len([name for name in before if name.startswith("grid-")]) > 1
    data = payload(root, snapshot, tmp_path / "workspace")
    progress = []
    result = native_export_worker(data, progress.append, Event())
    assert progress == [0.2, 0.6, 0.9, 1.0]
    assert_result(data, result, before)
    assert files(root) == before


def test_pinned_head_survives_later_real_commit(case):
    root, snapshot, data = case
    captured = files(root)
    with ProjectStore(root.parent) as store:
        app = ApplicationService(store)
        app.execute(
            snapshot.project.id,
            {
                "command_id": str(uuid4()),
                "kind": "update_song_metadata",
                "payload": {"title": "Later title"},
                "base_project_revision": snapshot.project.revision,
                "expected_grid_revision": snapshot.grid.revision,
                "expected_chart_revisions": {chart.id: chart.revision for chart in snapshot.charts},
            },
        )
    later = files(root)
    assert later["project.json"] != captured["project.json"]
    result = native_export_worker(data, lambda _: None, Event())
    assert_result(data, result, captured)
    assert files(root) == later


def test_changed_immutable_file_rejects_without_source_writes(case):
    root, snapshot, data = case
    path = root / f"grid-{snapshot.grid.revision}.json"
    path.write_bytes(path.read_bytes() + b" ")
    before = files(root)
    with pytest.raises(ValueError, match="Captured file changed"):
        native_export_worker(data, lambda _: None, Event())
    assert_clean(data)
    assert files(root) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("inventory", {}),
        ("inventory", None),
        ("project_revision", True),
        ("project_revision", -1),
        ("project_revision", 0.0),
        ("project_revision", "0"),
        ("workspace", "relative"),
        ("workspace", None),
    ],
)
def test_invalid_payload_fields_do_not_create_outputs(case, field, value):
    root, _, data = case
    original = dict(data)
    data[field] = value
    before = files(root)
    with pytest.raises((ValueError, TypeError)):
        native_export_worker(data, lambda _: None, Event())
    assert_clean(original)
    assert files(root) == before


@pytest.mark.parametrize("value", [None, [], {}, {"inventory": None}])
def test_malformed_payload_shape_has_no_side_effects(case, value):
    root, _, data = case
    before = files(root)
    with pytest.raises((ValueError, TypeError, KeyError)):
        native_export_worker(value, lambda _: None, Event())
    assert_clean(data)
    assert files(root) == before


@pytest.mark.parametrize(
    "stage", ["hash_inventory", "write_native_zip", "read_native_zip", "validate_native_project"]
)
def test_failure_at_each_stage_removes_only_owned_directories(case, monkeypatch, stage):
    root, _, data = case
    workspace = Path(data["workspace"])
    (workspace / "unrelated").mkdir()
    (workspace / "unrelated/sentinel").write_bytes(b"preserve")
    before = files(root)
    error = RuntimeError("injected stage failure")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(module, stage, fail)
    with pytest.raises(RuntimeError) as caught:
        native_export_worker(data, lambda _: None, Event())
    assert caught.value is error
    assert_clean(data)
    assert (workspace / "unrelated/sentinel").read_bytes() == b"preserve"
    assert files(root) == before


@pytest.mark.parametrize("directory", ["prepared", "verification"])
def test_preexisting_directory_is_preserved(case, directory):
    root, _, data = case
    existing = Path(data["workspace"]) / directory
    existing.mkdir()
    (existing / "sentinel").write_bytes(b"preserve")
    before = files(root)
    with pytest.raises(FileExistsError):
        native_export_worker(data, lambda _: None, Event())
    assert files(existing) == {"sentinel": b"preserve"}
    if directory == "verification":
        assert not (Path(data["workspace"]) / "prepared").exists()
    assert files(root) == before


@pytest.mark.parametrize("stop_at", [0, 0.2, 0.6, 0.9, 1.0])
def test_cancel_before_work_and_at_every_progress_report(case, stop_at):
    root, _, data = case
    before = files(root)
    event = Event()
    if stop_at == 0:
        event.set()

    def progress(value):
        if value == stop_at:
            event.set()

    with pytest.raises(RuntimeError, match="Native export cancelled"):
        native_export_worker(data, progress, event)
    assert_clean(data)
    assert files(root) == before


def test_cancel_during_hash_is_observed_before_writing(case, monkeypatch):
    root, _, data = case
    event = Event()
    original = module.hash_inventory

    def hash_then_cancel(*args):
        result = original(*args)
        event.set()
        return result

    monkeypatch.setattr(module, "hash_inventory", hash_then_cancel)
    monkeypatch.setattr(
        module, "write_native_zip", lambda *a, **k: pytest.fail("write started after cancellation")
    )
    with pytest.raises(RuntimeError, match="cancelled"):
        native_export_worker(data, lambda _: None, event)
    assert_clean(data)


@pytest.mark.parametrize(
    "stage", ["write_native_zip", "read_native_zip", "validate_native_project"]
)
def test_cancel_after_completed_stage_removes_owned_outputs(case, monkeypatch, stage):
    root, _, data = case
    before = files(root)
    event = Event()
    original = getattr(module, stage)

    def finish_then_cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        event.set()
        return result

    monkeypatch.setattr(module, stage, finish_then_cancel)
    with pytest.raises(RuntimeError, match="Native export cancelled"):
        native_export_worker(data, lambda _: None, event)
    assert_clean(data)
    assert files(root) == before


@pytest.mark.parametrize("revision_delta", [1, 3])
def test_expected_revision_mismatch_rejects_verified_archive(case, revision_delta):
    _, _, data = case
    data["project_revision"] += revision_delta
    with pytest.raises(ValueError, match="Restored project does not match capture"):
        native_export_worker(data, lambda _: None, Event())
    assert_clean(data)


def test_expected_project_id_mismatch_rejects_verified_archive(case):
    root, _, data = case
    before = files(root)
    data["inventory"] = replace(data["inventory"], project_id=str(uuid4()))
    with pytest.raises(ValueError):
        native_export_worker(data, lambda _: None, Event())
    assert_clean(data)
    assert files(root) == before


@pytest.mark.parametrize("head", [b"[]", b'{"project":{}}'])
def test_malformed_pinned_head_is_rejected_by_real_validator(case, head):
    root, _, data = case
    before = files(root)
    data["inventory"] = replace(data["inventory"], head=head)
    with pytest.raises(ValueError):
        native_export_worker(data, lambda _: None, Event())
    assert_clean(data)
    assert files(root) == before


@pytest.mark.parametrize("nested", [False, True])
def test_workspace_inside_source_is_rejected_before_source_writes(case, nested):
    root, _, data = case
    workspace = root / "worker-output" if nested else root
    if nested:
        workspace.mkdir()
    data["workspace"] = str(workspace)
    before = files(root)
    with pytest.raises(ValueError):
        native_export_worker(data, lambda _: None, Event())
    assert_clean(data)
    assert files(root) == before


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
def test_workspace_junction_alias_to_source_is_rejected(case, tmp_path):
    root, _, data = case
    link = tmp_path / "source-alias"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(root)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert link.is_junction()
    before = files(root)
    try:
        data["workspace"] = str(link)
        with pytest.raises(ValueError, match="outside the source"):
            native_export_worker(data, lambda _: None, Event())
        assert_clean(data)
        assert files(root) == before
        assert link.is_junction()
    finally:
        link.rmdir()


def test_progress_callback_failure_cleans_verified_outputs(case):
    _, _, data = case

    def progress(value):
        if value == 1.0:
            raise RuntimeError("progress channel failed")

    with pytest.raises(RuntimeError, match="progress channel failed"):
        native_export_worker(data, progress, Event())
    assert_clean(data)


def _spawn_export(data, messages):
    try:
        result = native_export_worker(data, lambda _: None, Event())
        messages.put(("result", result))
    except BaseException as exc:
        messages.put(("error", type(exc).__name__, str(exc)))


def test_real_spawn_accepts_captured_dataclass(case):
    root, _, data = case
    before = files(root)
    context = mp.get_context("spawn")
    messages = context.Queue()
    process = context.Process(target=_spawn_export, args=(data, messages))
    try:
        process.start()
        process.join(30)
        assert not process.is_alive(), "spawn worker did not finish"
        assert process.exitcode == 0
        message = messages.get(timeout=5)
        assert message[0] == "result", message
        assert_result(data, message[1], before)
        assert files(root) == before
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        messages.close()
        messages.join_thread()
