import hashlib
import json

import pytest

from ai_edda.native_export_catalog import make_native_export_record, resolve_native_export

PROJECT = "12345678-1234-1234-1234-123456789abc"
EXPORT = "12345678-1234-1234-1234-123456789abd"


@pytest.fixture
def published(tmp_path):
    # These bytes test record transport, not native ZIP semantics.
    data = b"archive transport bytes"
    result = {
        "project_id": PROJECT,
        "project_revision": 2,
        "archive_sha256": hashlib.sha256(data).hexdigest(),
        "archive_size": len(data),
        "envelope_sha256": "a" * 64,
        "head_sha256": "b" * 64,
    }
    context = {
        "project_id": PROJECT,
        "export_id": EXPORT,
        "project_revision": 2,
        "extra": "request",
    }
    record = make_native_export_record(EXPORT, "job", context, result)
    root = tmp_path / "exports" / PROJECT / EXPORT
    root.mkdir(parents=True)
    (root / "backup.zip").write_bytes(data)
    (root / "publication.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path, root, record, context, result


def test_resolve_and_context_hash(published):
    root, _, record, context, result = published
    assert resolve_native_export(root, PROJECT, EXPORT) == record
    assert (
        make_native_export_record(EXPORT, "job", {**context, "extra": "other"}, result)[
            "context_sha256"
        ]
        != record["context_sha256"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_revision", True),
        ("archive_size", True),
        ("archive_size", 0),
        ("archive_sha256", "A" * 64),
        ("project_id", "bad"),
        ("extra", 1),
    ],
)
def test_bad_result(published, field, value):
    _, _, _, context, result = published
    result[field] = value
    with pytest.raises(ValueError):
        make_native_export_record(EXPORT, "job", context, result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("job_id", ""),
        ("export_id", "bad"),
        ("context_sha256", "x" * 64),
        ("result_sha256", "0" * 64),
        ("public_result", {}),
    ],
)
def test_bad_record(published, field, value):
    root, directory, record, _, _ = published
    record[field] = value
    (directory / "publication.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_native_export(root, PROJECT, EXPORT)


@pytest.mark.parametrize(
    "data",
    [b"", b"archive transport byte", b"archive transport bytes!", b"Archive transport bytes"],
)
def test_bad_archive(published, data):
    root, directory, _, _, _ = published
    (directory / "backup.zip").write_bytes(data)
    with pytest.raises(ValueError):
        resolve_native_export(root, PROJECT, EXPORT)


def test_extra_file_and_large_record(published):
    root, directory, _, _, _ = published
    (directory / "extra").touch()
    with pytest.raises(ValueError):
        resolve_native_export(root, PROJECT, EXPORT)
    (directory / "extra").unlink()
    (directory / "publication.json").write_bytes(b" " * 65537)
    with pytest.raises(ValueError):
        resolve_native_export(root, PROJECT, EXPORT)


def test_symlink(published, tmp_path):
    root, directory, _, _, _ = published
    archive = directory / "backup.zip"
    archive.unlink()
    target = tmp_path / "target"
    target.write_bytes(b"data")
    try:
        archive.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises(ValueError):
        resolve_native_export(root, PROJECT, EXPORT)
