import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from test_import_worker import media as media
from test_import_worker import native_upload as native_upload

from ai_edda import project_imports as publisher
from ai_edda.application import ApplicationService
from ai_edda.import_receipts import ImportReceipt, build_import_receipt, publish_import_receipt
from ai_edda.import_worker import import_worker
from ai_edda.native_capture import capture_inventory, hash_inventory
from ai_edda.native_envelope import NativeEnvelope, serialize_native_envelope
from ai_edda.native_project import validate_native_project
from ai_edda.native_zip import write_native_zip
from ai_edda.project_store import ProjectStore, _json


def files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def prepared(native_upload, tmp_path):
    workspace, payload, source, envelope = native_upload
    old = build_import_receipt(
        {
            "request_id": str(uuid4()),
            "archive_kind": "native",
            "file_name": "earlier.zip",
            "file_size": 1,
        },
        uuid4().hex,
        "a" * 64,
        envelope.project_id,
        0,
        "b" * 64,
        "c" * 64,
    )
    publish_import_receipt(source, old)
    (workspace / "source.zip").unlink()
    capture = capture_inventory(source, envelope.project_id, (source / "project.json").read_bytes())
    write_native_zip(workspace / "source.zip", capture, hash_inventory(capture))
    job_id = uuid4().hex
    job_workspace = tmp_path / "jobs" / job_id
    job_workspace.parent.mkdir()
    workspace.rename(job_workspace)
    payload.update(workspace=str(job_workspace), job_id=job_id)
    payload["request_context"]["file_size"] = (job_workspace / "source.zip").stat().st_size
    result = import_worker(payload, lambda _: None, Event())
    with ProjectStore(tmp_path / "destination") as store:
        yield {
            "store": store,
            "context": {"request": payload["request_context"]},
            "result": result,
            "workspace": job_workspace,
            "source": job_workspace / "prepared" / "project",
            "old": old,
            "receipt": result["receipt"],
            "job_id": job_id,
        }


def publish(value):
    return publisher.publish_project_import(
        value["store"], value["context"], value["result"], value["workspace"]
    )


def wrapper(value):
    return value["store"].root.resolve() / (".import-" + value["job_id"])


def destination(value):
    return value["store"]._dir(value["receipt"]["project_id"])


def test_full_real_worker_tree_and_old_receipt_are_copied_exactly(prepared):
    value = prepared
    before = files(value["source"])
    assert publish(value) == value["receipt"]["public_result"]
    assert files(destination(value)) == before == files(value["source"])
    assert f"imports/{value['old'].request_id}.json" in before
    assert f"imports/{value['receipt']['request_id']}.json" in before
    assert not wrapper(value).exists()
    assert (
        validate_native_project(destination(value), value["receipt"]["project_id"]).project.revision
        == value["receipt"]["public_result"]["revision"]
    )


def test_initial_inventory_must_exclude_only_the_new_receipt(prepared):
    value = prepared
    source = value["source"]
    head = (source / "project.json").read_bytes()
    full = hash_inventory(capture_inventory(source, value["receipt"]["project_id"], head))
    incorrect = NativeEnvelope(
        archive_version=1,
        schema_version=1,
        project_id=full.project_id,
        files=tuple(entry for entry in full.files if not entry.path.startswith("imports/")),
    )
    value["receipt"]["initial_inventory_sha256"] = hashlib.sha256(
        serialize_native_envelope(incorrect)
    ).hexdigest()
    (source / "imports" / (value["receipt"]["request_id"] + ".json")).write_bytes(
        _json(value["receipt"])
    )
    before = files(source)
    with pytest.raises(ValueError, match="Prepared inventory mismatch"):
        publish(value)
    assert not destination(value).exists() and not wrapper(value).exists()
    assert files(source) == before


def test_public_revision_must_match_current_prepared_head_not_only_an_old_history_state(prepared):
    value = prepared
    assert value["receipt"]["public_result"]["revision"] > 0
    value["receipt"]["public_result"]["revision"] = 0
    (value["source"] / "imports" / (value["receipt"]["request_id"] + ".json")).write_bytes(
        _json(value["receipt"])
    )
    before = files(value["source"])
    with pytest.raises(ValueError, match="Project revision mismatch"):
        publish(value)
    assert not destination(value).exists() and not wrapper(value).exists()
    assert files(value["source"]) == before


@pytest.mark.parametrize(
    "change",
    ["context-extra", "result-extra", "request-size", "job", "unchecked-model", "prior-mismatch"],
)
def test_strict_context_result_and_job_binding_rejects_before_source_reads(
    prepared, monkeypatch, change
):
    value = prepared
    if change == "context-extra":
        value["context"]["extra"] = 1
    elif change == "result-extra":
        value["result"]["extra"] = 1
    elif change == "request-size":
        value["context"]["request"]["file_size"] += 1
    elif change == "job":
        value["receipt"]["job_id"] = uuid4().hex
    elif change == "unchecked-model":
        value["result"]["receipt"] = ImportReceipt.model_validate(value["receipt"]).model_copy(
            update={"version": True}
        )
    else:
        value["context"]["prior_receipt"] = {**value["receipt"], "job_id": uuid4().hex}
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert not path.is_relative_to(value["workspace"]), (
            "invalid binding must not read prepared source"
        )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    with pytest.raises(ValueError):
        publish(value)
    assert not destination(value).exists() and not wrapper(value).exists()


def test_exact_retry_ignores_later_head_and_missing_workspace(prepared, monkeypatch):
    value = prepared
    original_public = publish(value)
    service = ApplicationService(value["store"])
    view = service.get(value["receipt"]["project_id"])
    service.execute(
        view.project.id,
        {
            "command_id": str(uuid4()),
            "kind": "update_song_metadata",
            "payload": {"title": "Later canonical edit"},
            "base_project_revision": view.project.revision,
            "expected_grid_revision": view.grid.revision,
            "expected_chart_revisions": {chart.id: chart.revision for chart in view.charts},
        },
    )
    shutil.rmtree(value["workspace"])
    before = files(destination(value))
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name != "project.json", "retry must not inspect the current head"
        assert not path.is_relative_to(value["workspace"]), (
            "retry must not inspect a missing workspace"
        )
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded)
        assert publish(value) == original_public
        value["context"]["prior_receipt"] = value["receipt"]
        value["workspace"] = value["workspace"].with_name(uuid4().hex)
        assert publish(value) == original_public
    assert files(destination(value)) == before


def test_prior_retry_never_recreates_a_missing_destination(prepared, monkeypatch):
    value = prepared
    value["context"]["prior_receipt"] = value["receipt"]
    value["workspace"] = value["workspace"].with_name(uuid4().hex)

    def forbidden(*args, **kwargs):
        raise AssertionError("prior retry must not inspect a prepared source")

    monkeypatch.setattr(publisher, "capture_inventory", forbidden)
    with pytest.raises(ValueError, match="Prior import destination missing"):
        publish(value)
    assert not destination(value).exists()


@pytest.mark.parametrize("corruption", ["head", "immutable", "stored-receipt"])
def test_corrupt_prepared_source_is_rejected_and_preserved(prepared, corruption):
    value = prepared
    if corruption == "head":
        path = value["source"] / "project.json"
    elif corruption == "immutable":
        path = next(value["source"].glob("grid-*.json"))
    else:
        path = value["source"] / "imports" / (value["receipt"]["request_id"] + ".json")
    path.write_bytes(path.read_bytes() + b" " if corruption != "stored-receipt" else b"{broken")
    before = files(value["source"])
    with pytest.raises(ValueError):
        publish(value)
    assert files(value["source"]) == before
    assert not destination(value).exists() and not wrapper(value).exists()


def test_foreign_wrapper_is_never_adopted_or_removed(prepared):
    value = prepared
    stage = wrapper(value)
    stage.mkdir()
    (stage / "marker.json").write_bytes(b"foreign marker")
    (stage / "sentinel").write_bytes(b"keep")
    before = files(stage)
    with pytest.raises(FileExistsError):
        publish(value)
    assert files(stage) == before and not destination(value).exists()


def test_foreign_destination_is_preserved_with_explicit_conflict(prepared):
    value = prepared
    dest = destination(value)
    dest.mkdir()
    (dest / "sentinel").write_bytes(b"keep")
    with pytest.raises(ValueError, match="PROJECT_EXISTS: " + value["receipt"]["project_id"]):
        publish(value)
    assert files(dest) == {"sentinel": b"keep"}
    assert not wrapper(value).exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows no-replacement directory rename")
def test_competing_destination_created_immediately_before_rename_is_not_replaced(
    prepared, monkeypatch
):
    value = prepared
    original = publisher.os.rename

    def competing(source, target):
        target.mkdir()
        (target / "sentinel").write_bytes(b"competing publication")
        return original(source, target)

    monkeypatch.setattr(publisher.os, "rename", competing)
    with pytest.raises(OSError):
        publish(value)
    assert files(destination(value)) == {"sentinel": b"competing publication"}
    assert not wrapper(value).exists()


@pytest.mark.parametrize("after_rename", [False, True])
def test_failures_before_and_after_rename_preserve_source_and_retry_semantics(
    prepared, monkeypatch, after_rename
):
    value = prepared
    original = publisher.os.rename

    def fail(source, target):
        if after_rename:
            original(source, target)
        raise OSError("injected rename failure")

    before = files(value["source"])
    with monkeypatch.context() as patch:
        patch.setattr(publisher.os, "rename", fail)
        with pytest.raises(OSError, match="injected rename failure"):
            publish(value)
    assert destination(value).exists() is after_rename
    assert not wrapper(value).exists()
    assert files(value["source"]) == before
    assert publish(value) == value["receipt"]["public_result"]


def test_disk_failure_cleans_owned_wrapper_without_touching_source(prepared, monkeypatch):
    value = prepared
    calls = 0
    original = publisher.os.fsync

    def fail(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk unavailable")
        return original(fd)

    before = files(value["source"])
    monkeypatch.setattr(publisher.os, "fsync", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        publish(value)
    assert not destination(value).exists() and not wrapper(value).exists()
    assert files(value["source"]) == before


def test_copy_chunks_fsync_marker_and_file_outputs_and_lock_boundary(prepared, monkeypatch):
    value = prepared
    expected_files = len(files(value["source"]))
    original_open, original_fsync = Path.open, publisher.os.fsync
    synchronized, reads, writes = [], [], []

    class Handle:
        def __init__(self, inner, path):
            self.inner, self.path = inner, path

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def read(self, size=-1):
            if self.path.is_relative_to(value["source"]) and wrapper(value).exists():
                assert 0 < size <= 1024 * 1024
                assert not value["store"]._mutex._is_owned()
                reads.append(size)
            return self.inner.read(size)

        def write(self, data):
            assert len(data) <= 1024 * 1024
            assert not value["store"]._mutex._is_owned()
            writes.append(len(data))
            return self.inner.write(data)

    def opened(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.is_relative_to(wrapper(value) / "project") and "x" in mode:
            assert synchronized, "ownership marker must be fsynced before the first copied file"
        return Handle(original_open(path, *args, **kwargs), path)

    def fsync(fd):
        synchronized.append(fd)
        return original_fsync(fd)

    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(publisher.os, "fsync", fsync)
    assert publish(value) == value["receipt"]["public_result"]
    assert reads and writes
    assert len(synchronized) == expected_files + 1


def test_source_changed_after_capture_is_detected_during_copy(prepared, monkeypatch):
    value = prepared
    original = publisher.hash_inventory
    path = next(value["source"].glob("grid-*.json"))
    before = path.read_bytes()
    changed = False

    def capture(inventory):
        nonlocal changed
        envelope = original(inventory)
        if not changed:
            changed = True
            path.write_bytes(before + b" ")
        return envelope

    monkeypatch.setattr(publisher, "hash_inventory", capture)
    with pytest.raises(ValueError, match="Source changed"):
        publish(value)
    assert not destination(value).exists() and not wrapper(value).exists()
    assert path.read_bytes() == before + b" "


def test_cleanup_failure_after_publication_leaves_stored_receipt_for_later_reconciliation(
    prepared, monkeypatch
):
    value = prepared
    original = publisher.shutil.rmtree

    def fail(path, *args, **kwargs):
        if Path(path) == wrapper(value):
            raise OSError("cleanup unavailable")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(publisher.shutil, "rmtree", fail)
        with pytest.raises(OSError, match="cleanup unavailable"):
            publish(value)
    assert destination(value).is_dir() and (wrapper(value) / "marker.json").is_file()
    assert publish(value) == value["receipt"]["public_result"]


def test_marker_changed_after_creation_is_not_authority_to_remove_wrapper(prepared, monkeypatch):
    value = prepared

    def conflict(*args):
        (wrapper(value) / "marker.json").write_bytes(b"foreign ownership")
        (wrapper(value) / "foreign-data").write_bytes(b"keep")
        raise OSError("stage ownership changed")

    monkeypatch.setattr(publisher, "validate_native_project", conflict)
    with pytest.raises((OSError, ValueError, RuntimeError)):
        publish(value)
    assert (wrapper(value) / "foreign-data").read_bytes() == b"keep"
    assert not destination(value).exists()


def test_actual_cross_volume_workspace_publishes_using_destination_volume_staging(prepared):
    value = prepared
    scratch = Path(__file__).resolve().parents[2] / "src" / "scratch" / "github-preparation"
    scratch.mkdir(parents=True, exist_ok=True)
    assert scratch.resolve().is_relative_to(Path(__file__).resolve().parents[2])
    if scratch.drive.lower() == value["workspace"].drive.lower():
        pytest.skip("no second test volume is available")
    with tempfile.TemporaryDirectory(prefix="import-publication-volume-", dir=scratch) as directory:
        assert Path(directory).resolve().is_relative_to(scratch.resolve())
        with ProjectStore(Path(directory) / "store") as store:
            before = files(value["source"])
            public = publisher.publish_project_import(
                store, value["context"], value["result"], value["workspace"]
            )
            assert public == value["receipt"]["public_result"]
            assert (
                files(store._dir(value["receipt"]["project_id"]))
                == before
                == files(value["source"])
            )


def test_cleanup_helper_rejects_wrapper_outside_resolved_store_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = b"expected marker"
    (outside / "marker.json").write_bytes(marker)
    (outside / "sentinel").write_bytes(b"keep")
    before = files(outside)
    with ProjectStore(tmp_path / "store") as store:
        with pytest.raises(RuntimeError, match="ownership"):
            publisher._cleanup_owned_wrapper(store, outside, marker)
    assert files(outside) == before


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
@pytest.mark.parametrize("junction_is_wrapper", [False, True])
def test_cleanup_helper_rejects_actual_windows_junctions(tmp_path, junction_is_wrapper):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = b"expected marker"
    (outside / "marker.json").write_bytes(marker)
    (outside / "sentinel").write_bytes(b"keep")
    before = files(outside)
    with ProjectStore(tmp_path / "store") as store:
        stage = store.root.resolve() / (".import-" + uuid4().hex)
        if junction_is_wrapper:
            junction = stage
        else:
            stage.mkdir()
            (stage / "marker.json").write_bytes(marker)
            junction = stage / "project"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        try:
            assert not junction.is_symlink(), "this probe requires a junction rather than a symlink"
            with pytest.raises(RuntimeError, match="ownership"):
                publisher._cleanup_owned_wrapper(store, stage, marker)
            assert junction.exists(), "ambiguous junction must remain for explicit recovery"
            assert files(outside) == before
        finally:
            if junction.exists():
                junction.rmdir()
