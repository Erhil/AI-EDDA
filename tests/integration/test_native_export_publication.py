"""Publication tests use actual verified native export worker output."""

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from test_native_export_worker import case as case

from ai_edda import native_export_catalog as catalog
from ai_edda.native_export_worker import native_export_worker
from ai_edda.project_store import _json


@pytest.fixture
def publication(case, tmp_path):
    source, _, payload = case
    result = native_export_worker(payload, lambda _: None, Event())
    export_id = str(uuid4())
    context = {
        "project_id": result["project_id"],
        "project_revision": result["project_revision"],
        "export_id": export_id,
        "request": {"mode": "native", "title": "Original"},
    }
    config = tmp_path / "config"
    prepared = Path(payload["workspace"]) / "prepared"
    record = catalog.make_native_export_record(export_id, "native-job", context, result)
    parent = config / "exports" / result["project_id"]
    stage_name = (
        ".native-export-"
        + hashlib.sha256(_json([result["project_id"], export_id, "native-job"])).hexdigest()
    )
    return {
        "source": source,
        "config": config,
        "prepared": prepared,
        "record": record,
        "context": context,
        "result": result,
        "id": export_id,
        "parent": parent,
        "final": parent / export_id,
        "wrapper": parent / stage_name,
    }


def publish(value, prepared=None):
    return catalog.publish_native_export(
        value["config"],
        value["id"],
        "native-job",
        value["context"],
        value["result"],
        prepared if prepared is not None else value["prepared"],
    )


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_actual_worker_publication_is_one_rename_and_source_free_replay(publication, monkeypatch):
    value = publication
    source, prepared = snapshot(value["source"]), snapshot(value["prepared"])
    renames, syncs = [], []
    rename, fsync = catalog.os.rename, catalog.os.fsync

    def rename_once(old, new):
        old, new = Path(old), Path(new)
        assert old == value["wrapper"] / "export" and new == value["final"]
        assert set(p.name for p in old.iterdir()) == {"backup.zip", "publication.json"}
        assert (old.parent / "marker.json").is_file()
        renames.append((old, new))
        return rename(old, new)

    def sync(descriptor):
        syncs.append(descriptor)
        return fsync(descriptor)

    monkeypatch.setattr(catalog.os, "rename", rename_once)
    monkeypatch.setattr(catalog.os, "fsync", sync)
    assert publish(value) == value["record"]["public_result"]
    assert len(renames) == 1 and len(syncs) == 3
    assert (
        catalog.resolve_native_export(value["config"], value["result"]["project_id"], value["id"])
        == value["record"]
    )
    assert not value["wrapper"].exists()
    assert snapshot(value["prepared"]) == prepared and snapshot(value["source"]) == source
    assert (
        publish(value, prepared=value["prepared"] / "missing") == value["record"]["public_result"]
    )
    assert len(renames) == 1


@pytest.mark.parametrize("extra", ["extra.json", "unknown/extra"])
def test_prepared_requires_exact_backup_file(publication, extra):
    path = publication["prepared"] / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"extra")
    with pytest.raises(ValueError):
        publish(publication)
    assert not publication["final"].exists()


def test_staged_archive_is_rehashed_before_rename(publication, monkeypatch):
    original = catalog._read
    corrupted = []

    def read_after_bad_copy(path, budget):
        if Path(path) == publication["wrapper"] / "export/publication.json":
            copied = Path(path).parent / "backup.zip"
            data = copied.read_bytes()
            copied.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            corrupted.append(True)
        return original(path, budget)

    monkeypatch.setattr(catalog, "_read", read_after_bad_copy)
    with pytest.raises(ValueError):
        publish(publication)
    assert corrupted and not publication["final"].exists() and not publication["wrapper"].exists()


def test_named_limit_matches_native_upload():
    from ai_edda.api.archives import MAX_SIZE

    assert catalog.MAX_NATIVE_BACKUP_BYTES == MAX_SIZE == 1024**3


def test_actual_zip_limit_includes_overhead_and_accepts_exact_boundary(publication, monkeypatch):
    value = publication
    size = value["result"]["archive_size"]
    # The archive includes ZIP overhead, not only the native head bytes.
    assert size > (value["source"] / "project.json").stat().st_size
    monkeypatch.setattr(catalog, "MAX_NATIVE_BACKUP_BYTES", size - 1)
    with pytest.raises(ValueError, match="exceeds allowed"):
        publish(value)
    assert not value["final"].exists() and not value["wrapper"].exists()
    monkeypatch.setattr(catalog, "MAX_NATIVE_BACKUP_BYTES", size)
    assert publish(value) == value["record"]["public_result"]


def test_source_free_replay_enforces_current_actual_size_limit(publication, monkeypatch):
    value = publication
    publish(value)
    before = snapshot(value["final"])
    monkeypatch.setattr(catalog, "MAX_NATIVE_BACKUP_BYTES", value["result"]["archive_size"] - 1)
    with pytest.raises(ValueError, match="exceeds allowed"):
        publish(value, value["prepared"] / "missing")
    assert snapshot(value["final"]) == before


def test_existing_conflicting_publication_is_not_overwritten(publication):
    value = publication
    publish(value)
    before = snapshot(value["final"])
    value["context"]["request"]["title"] = "Different request"
    with pytest.raises(ValueError, match="conflict"):
        publish(value)
    assert snapshot(value["final"]) == before


def test_competing_destination_before_rename_is_preserved(publication, monkeypatch):
    value = publication
    original = catalog._read

    def competing(path, budget):
        result = original(path, budget)
        if Path(path) == value["wrapper"] / "export/publication.json":
            value["final"].mkdir()
            (value["final"] / "sentinel").write_bytes(b"competing owner")
        return result

    monkeypatch.setattr(catalog, "_read", competing)
    with pytest.raises(ValueError):
        publish(value)
    assert snapshot(value["final"]) == {"sentinel": b"competing owner"}
    assert not value["wrapper"].exists()


def test_foreign_deterministic_wrapper_is_not_deleted(publication):
    value = publication
    value["wrapper"].mkdir(parents=True)
    (value["wrapper"] / "marker.json").write_bytes(b"foreign marker")
    (value["wrapper"] / "sentinel").write_bytes(b"preserve")
    before = snapshot(value["wrapper"])
    with pytest.raises(FileExistsError):
        publish(value)
    assert snapshot(value["wrapper"]) == before and not value["final"].exists()


def test_marker_fsync_failure_does_not_delete_unconfirmed_stage(publication, monkeypatch):
    value = publication

    def fail(_):
        raise OSError("marker fsync failed")

    monkeypatch.setattr(catalog.os, "fsync", fail)
    with pytest.raises(OSError, match="marker fsync failed"):
        publish(value)
    assert not value["final"].exists()
    assert value["wrapper"].is_dir() and (value["wrapper"] / "marker.json").is_file()
    assert not (value["wrapper"] / "export").exists()


def test_copy_error_removes_exactly_marked_owned_stage(publication, monkeypatch):
    value = publication
    original = Path.open

    def open_source_error(path, mode="r", *args, **kwargs):
        if path == value["prepared"] / "backup.zip" and mode == "rb":
            raise OSError("source read failed")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_source_error)
    with pytest.raises(OSError, match="source read failed"):
        publish(value)
    assert not value["final"].exists() and not value["wrapper"].exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
def test_foreign_wrapper_junction_is_preserved(publication, tmp_path):
    value = publication
    target = tmp_path / "external-target"
    target.mkdir()
    (target / "sentinel").write_bytes(b"preserve")
    value["parent"].mkdir(parents=True)
    link = value["wrapper"]
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        with pytest.raises(FileExistsError):
            publish(value)
        assert link.is_junction() and (target / "sentinel").read_bytes() == b"preserve"
        assert not value["final"].exists()
    finally:
        link.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows drive layout")
def test_copy_from_c_workspace_to_d_config_if_available(publication):
    source = publication["prepared"].resolve()
    base = Path.cwd().resolve()
    if source.drive.casefold() == base.drive.casefold():
        pytest.skip("workspace and repository are on the same drive")
    with tempfile.TemporaryDirectory(prefix="x-", dir=base) as temporary:
        config = Path(temporary).resolve()
        assert config.is_relative_to(base.resolve())
        assert source.drive.casefold() != config.drive.casefold()
        value = {**publication, "config": config}
        result = publish(value)
        assert result == value["record"]["public_result"]
        assert (
            catalog.resolve_native_export(config, value["result"]["project_id"], value["id"])
            == value["record"]
        )
