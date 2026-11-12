"""Independent native export record and resolver boundary checks."""

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from test_native_export_catalog import EXPORT, PROJECT
from test_native_export_catalog import published as published
from test_native_export_worker import case as case

from ai_edda.native_export_catalog import (
    NativeExportResult,
    make_native_export_record,
    resolve_native_export,
)
from ai_edda.native_export_worker import native_export_worker
from ai_edda.project_store import _json


def store_record(directory, record):
    (directory / "publication.json").write_bytes(_json(record))


def test_record_accepts_actual_verified_worker_output(case, tmp_path):
    source, _, payload = case
    source_bytes = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    result = native_export_worker(payload, lambda _: None, Event())
    export_id = str(uuid4())
    context = {
        "project_id": result["project_id"],
        "export_id": export_id,
        "project_revision": result["project_revision"],
        "request": {"title": "История", "flags": [True, False], "nullable": None},
    }
    record = make_native_export_record(export_id, "native-job", context, result)
    config = tmp_path / "config"
    directory = config / "exports" / result["project_id"] / export_id
    directory.mkdir(parents=True)
    archive_bytes = (Path(payload["workspace"]) / "prepared/backup.zip").read_bytes()
    (directory / "backup.zip").write_bytes(archive_bytes)
    store_record(directory, record)
    before = {p: p.read_bytes() for p in directory.iterdir()}
    assert resolve_native_export(config, result["project_id"], export_id) == record
    assert record["context_sha256"] == hashlib.sha256(_json(context)).hexdigest()
    assert record["result_sha256"] == hashlib.sha256(_json(result)).hexdigest()
    assert {p: p.read_bytes() for p in before} == before
    assert {p: p.read_bytes() for p in source_bytes} == source_bytes


@pytest.mark.parametrize(
    "mode", ["valid_instance", "copy_instance", "construct_instance", "copy_dump", "construct_dump"]
)
def test_result_model_bypass_is_rejected(published, mode):
    _, _, _, context, result = published
    model = NativeExportResult.model_validate(result)
    if mode.startswith("copy"):
        value = model.model_copy(update={"project_revision": True})
    elif mode.startswith("construct"):
        value = NativeExportResult.model_construct(**{**result, "archive_size": True})
    else:
        value = model
    if mode.endswith("dump"):
        value = value.model_dump(warnings=False)
    with pytest.raises(ValueError):
        make_native_export_record(EXPORT, "job", context, value)


def test_context_extras_are_fully_hashed_and_result_is_detached(published):
    _, _, _, context, result = published
    context = {**context, "metadata": {"nested": ["Привет", 7, None, False], "v": 2}}
    before_context, before_result = copy.deepcopy(context), copy.deepcopy(result)
    record = make_native_export_record(EXPORT, "job", context, result)
    assert context == before_context and result == before_result
    assert record["context_sha256"] == hashlib.sha256(_json(context)).hexdigest()
    context["metadata"]["nested"][1] = 8
    assert (
        make_native_export_record(EXPORT, "job", context, result)["context_sha256"]
        != record["context_sha256"]
    )
    result["head_sha256"] = "f" * 64
    assert record["result"]["head_sha256"] == before_result["head_sha256"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", str(uuid4())),
        ("export_id", str(uuid4())),
        ("project_revision", True),
        ("project_revision", 2.0),
        ("project_revision", "2"),
        ("project_revision", -1),
        ("project_revision", 3),
        ("project_id", PROJECT.upper()),
        ("export_id", EXPORT.upper()),
    ],
)
def test_required_context_identity_is_strict(published, field, value):
    _, _, _, context, result = published
    with pytest.raises(ValueError):
        make_native_export_record(EXPORT, "job", {**context, field: value}, result)


def test_context_digest_is_opaque_in_local_resolver(published):
    config, directory, record, _, _ = published
    record["context_sha256"] = "0" * 64
    store_record(directory, record)
    assert resolve_native_export(config, PROJECT, EXPORT) == record


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_revision", True),
        ("project_revision", 2.0),
        ("project_id", PROJECT.upper()),
        ("export_id", EXPORT.upper()),
        ("kind", "game"),
        ("file_name", "../backup.zip"),
        ("extra", 1),
    ],
)
def test_public_result_exact_types_and_fields(published, field, value):
    config, directory, record, _, _ = published
    record["public_result"][field] = value
    store_record(directory, record)
    with pytest.raises(ValueError):
        resolve_native_export(config, PROJECT, EXPORT)


@pytest.mark.parametrize(
    "raw",
    [b"[]", b"null", b"{", b'{"version":1,"version":1}', b" " * 65537, b"\xff", b'{"version":NaN}'],
)
def test_malformed_publication_json(published, raw):
    config, directory, _, _, _ = published
    (directory / "publication.json").write_bytes(raw)
    with pytest.raises(ValueError):
        resolve_native_export(config, PROJECT, EXPORT)


def test_nested_duplicate_key_is_rejected(published):
    config, directory, record, _, _ = published
    raw = _json(record).replace(b'"archive_size":', b'"archive_size":1,"archive_size":')
    (directory / "publication.json").write_bytes(raw)
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        resolve_native_export(config, PROJECT, EXPORT)


@pytest.mark.parametrize("new_size", [0, 1, 100])
def test_archive_size_mismatch_rejects_before_archive_open(published, monkeypatch, new_size):
    config, directory, _, _, _ = published
    path = directory / "backup.zip"
    path.write_bytes(b"x" * new_size)
    original = Path.open

    def open_without_archive(item, *args, **kwargs):
        if item == path:
            pytest.fail("Mismatched archive was opened before stat-size rejection")
        return original(item, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_without_archive)
    with pytest.raises(ValueError):
        resolve_native_export(config, PROJECT, EXPORT)


def test_stream_hash_requests_are_at_most_one_mib(published, monkeypatch):
    config, directory, record, context, result = published
    content = b"archive bytes" * 200000
    result = {
        **result,
        "archive_size": len(content),
        "archive_sha256": hashlib.sha256(content).hexdigest(),
    }
    record = make_native_export_record(EXPORT, "job", context, result)
    (directory / "backup.zip").write_bytes(content)
    store_record(directory, record)
    reads = []
    original = Path.open

    class Track:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            reads.append(size)
            return self.stream.read(size)

    def track(item, *args, **kwargs):
        return Track(original(item, *args, **kwargs))

    monkeypatch.setattr(Path, "open", track)
    assert resolve_native_export(config, PROJECT, EXPORT) == record
    assert max(reads) == 1024 * 1024 and min(reads) > 0


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
@pytest.mark.parametrize("where", ["config", "export", "archive", "publication"])
def test_windows_junctions_reject_and_preserve_targets(published, tmp_path, where):
    config, directory, record, _, _ = published
    if where == "config":
        link, target = tmp_path / "config-alias", config
    elif where == "export":
        link, target = directory.parent / str(uuid4()), directory
    else:
        link = directory / ("backup.zip" if where == "archive" else "publication.json")
        link.unlink()
        target = tmp_path / "external-target"
        target.mkdir()
        (target / "sentinel").write_bytes(b"preserve")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert link.is_junction()
    try:
        selected_config = link if where == "config" else config
        selected_export = link.name if where == "export" else EXPORT
        with pytest.raises(ValueError, match="Unsafe path"):
            resolve_native_export(selected_config, PROJECT, selected_export)
        assert link.is_junction()
        if where in ("archive", "publication"):
            assert (target / "sentinel").read_bytes() == b"preserve"
        else:
            assert json.loads((directory / "publication.json").read_bytes()) == record
    finally:
        link.rmdir()
