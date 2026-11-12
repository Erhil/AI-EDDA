"""Read-only export verification against real ArtifactBuilder output."""

import base64
import hashlib
import json
import random
import stat
import zipfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from test_package_builder import snapshot as snapshot

from ai_edda.game_export_catalog import verify_game_export
from ai_edda.packaging import ArtifactBuilder


@pytest.fixture
def exported(tmp_path, snapshot):
    value = replace(snapshot(2), snapshot_id=str(uuid4()))
    directory = tmp_path / "out"
    receipt = ArtifactBuilder(tmp_path).build(value, directory, "song")
    return directory, receipt


def read_receipt(directory):
    return json.loads((directory / "song.receipt.json").read_text(encoding="utf-8"))


def write_receipt(directory, receipt):
    (directory / "song.receipt.json").write_text(json.dumps(receipt), encoding="utf-8")


def zip_members(directory, transform=None):
    with zipfile.ZipFile(directory / "song.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        for path in (directory / "song").iterdir():
            name = path.name
            names = transform(name) if transform else [name]
            for entry in names:
                archive.writestr(entry, path.read_bytes())


@pytest.mark.parametrize("count", [1, 2, 3])
def test_real_builder_one_to_three_charts_and_no_writes(tmp_path, snapshot, count):
    directory = tmp_path / "out"
    receipt = ArtifactBuilder(tmp_path).build(
        replace(snapshot(count), snapshot_id=str(uuid4())), directory, "song"
    )
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    result = verify_game_export(directory, "song")
    assert result["receipt"] == receipt
    assert result["zip_sha256"] == hashlib.sha256((directory / "song.zip").read_bytes()).hexdigest()
    assert (
        result["receipt_sha256"]
        == hashlib.sha256((directory / "song.receipt.json").read_bytes()).hexdigest()
    )
    assert len(receipt["selection"]) == count
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize(
    "field, value",
    [
        ("project_revision", True),
        ("grid_revision", 0.0),
        ("snapshot_id", str(uuid4()).upper()),
        ("audio_sha256", "A" * 64),
        ("profile", "unknown"),
        ("local_check", "failed"),
        ("selection", []),
        ("members", None),
        ("extra", 1),
    ],
)
def test_receipt_schema_is_strict(exported, field, value):
    directory, receipt = exported
    receipt[field] = value
    write_receipt(directory, receipt)
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


@pytest.mark.parametrize(
    "field, value", [("revision", True), ("filename", "Hard.dat"), ("chart_id", "bad")]
)
def test_selection_schema_is_strict(exported, field, value):
    directory, receipt = exported
    receipt["selection"][0][field] = value
    write_receipt(directory, receipt)
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


@pytest.mark.parametrize(
    "field, value", [("size", True), ("size", "1"), ("sha256", "bad"), ("extra", 1)]
)
def test_member_record_schema_is_strict(exported, field, value):
    directory, receipt = exported
    receipt["members"]["info.dat"][field] = value
    write_receipt(directory, receipt)
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


@pytest.mark.parametrize("location", ["folder", "zip", "receipt_hash", "audio_binding"])
def test_independent_content_and_digest_corruption_is_rejected(exported, location):
    directory, receipt = exported
    if location == "folder":
        with (directory / "song" / "info.dat").open("ab") as stream:
            stream.write(b" ")
    elif location == "zip":
        with zipfile.ZipFile(directory / "song.zip", "a") as archive:
            archive.writestr("info.dat", b"bad")
    elif location == "receipt_hash":
        receipt["members"]["info.dat"]["sha256"] = "f" * 64
        write_receipt(directory, receipt)
    else:
        receipt["audio_sha256"] = "f" * 64
        write_receipt(directory, receipt)
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


@pytest.mark.parametrize(
    "replacement",
    [
        "../info.dat",
        "dir/info.dat",
        "INFO.DAT",
        "info.dat ",
        "C:info.dat",
        "dir\\info.dat",
        "info.dat/",
        "info.datXjunk",
    ],
)
def test_zip_paths_aliases_and_case_are_rejected(exported, replacement):
    directory, _ = exported
    zip_members(directory, lambda name: [replacement] if name == "info.dat" else [name])
    if replacement == "info.datXjunk":
        path = directory / "song.zip"
        path.write_bytes(path.read_bytes().replace(b"info.datXjunk", b"info.dat\0junk"))
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO])
def test_zip_special_file_types_are_rejected(exported, kind):
    directory, _ = exported

    def names(name):
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = (kind | 0o600) << 16
        return [info] if name == "info.dat" else [name]

    zip_members(directory, names)
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


@pytest.mark.parametrize("location", ["folder_member", "archive", "receipt", "publication"])
def test_required_or_optional_files_cannot_be_directories(exported, location):
    directory, _ = exported
    path = {
        "folder_member": directory / "song" / "info.dat",
        "archive": directory / "song.zip",
        "receipt": directory / "song.receipt.json",
        "publication": directory / "publication.json",
    }[location]
    if path.exists():
        path.unlink()
    path.mkdir()
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


@pytest.mark.parametrize("corruption", ["header", "crc"])
def test_invalid_zip_is_a_validation_error(exported, corruption):
    directory, _ = exported
    path = directory / "song.zip"
    if corruption == "header":
        path.write_bytes(b"not-a-zip")
    else:
        zip_members(directory)
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("info.dat")
        data = bytearray(path.read_bytes())
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        data[offset] ^= 1
        path.write_bytes(data)
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


@pytest.mark.parametrize("failure", ["zip", "member"])
def test_site_limit_failure_preserves_valid_local_export(tmp_path, snapshot, failure):
    original = replace(snapshot(1), snapshot_id=str(uuid4()))
    title = (
        base64.b64encode(random.Random(7).randbytes(16 * 1024**2)).decode("ascii")
        if failure == "zip"
        else "x" * (50 * 1024**2)
    )
    value = replace(original, metadata=original.metadata.model_copy(update={"title": title}))
    directory = tmp_path / "out"
    receipt = ArtifactBuilder(tmp_path).build(value, directory, "song")
    expected = "ZIP exceeds 15 MiB" if failure == "zip" else "member exceeds 50 MiB"
    assert receipt["site_check"] == {"passed": False, "errors": [expected]}
    result = verify_game_export(directory, "song")
    assert result["receipt"]["local_check"] == "passed"
    assert result["receipt"]["site_check"] == receipt["site_check"]


def test_site_check_cannot_claim_a_different_result(exported):
    directory, receipt = exported
    receipt["site_check"] = {"passed": False, "errors": []}
    write_receipt(directory, receipt)
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


def test_duplicate_receipt_json_keys_are_rejected(exported):
    directory, _ = exported
    path = directory / "song.receipt.json"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw[:-1] + ',"project_revision":0}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        verify_game_export(directory, "song")


def test_reads_stay_within_one_mib(exported, monkeypatch):
    directory, _ = exported
    original = zipfile.ZipExtFile.read
    sizes = []

    def bounded(stream, size=-1):
        assert 0 < size <= 1024**2
        sizes.append(size)
        return original(stream, size)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", bounded)
    assert verify_game_export(directory, "song")["receipt"]["site_check"]["passed"]
    assert sizes


def test_cancel_callback_raises_during_streaming(exported):
    directory, _ = exported
    calls = []

    def cancel():
        calls.append(1)
        if len(calls) == 4:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        verify_game_export(directory, "song", cancel)


def test_aggregate_budget_accepts_exact_size_and_rejects_one_byte_more(exported, monkeypatch):
    import ai_edda.game_export_catalog as module

    directory, receipt = exported
    total = sum(entry["size"] for entry in receipt["members"].values())
    monkeypatch.setattr(module, "MAX_GAME_EXPORT_BYTES", total)
    assert verify_game_export(directory, "song")["receipt"] == receipt
    monkeypatch.setattr(module, "MAX_GAME_EXPORT_BYTES", total - 1)
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")


def test_local_streaming_stops_at_remaining_aggregate_budget(exported, monkeypatch):
    import ai_edda.game_export_catalog as module

    directory, receipt = exported
    total = sum(entry["size"] for entry in receipt["members"].values())
    last_size = list(receipt["members"].values())[-1]["size"]
    limit = total - last_size // 2
    monkeypatch.setattr(module, "MAX_GAME_EXPORT_BYTES", limit)
    original = Path.open
    consumed = []

    class Tracked:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size=-1):
            data = self.stream.read(size)
            consumed.append(len(data))
            return data

    def opened(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        return Tracked(stream) if path.parent == directory / "song" else stream

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(ValueError):
        verify_game_export(directory, "song")
    assert sum(consumed) <= limit + 1


def test_zip_expansion_stops_at_remaining_aggregate_budget(exported, monkeypatch):
    import ai_edda.game_export_catalog as module

    directory, receipt = exported
    total = sum(entry["size"] for entry in receipt["members"].values())
    names = list(receipt["members"])
    with zipfile.ZipFile(directory / "song.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            data = (directory / "song" / name).read_bytes()
            if name == names[-1]:
                data += b"x" * (1024 * 1024)
            archive.writestr(name, data)
    monkeypatch.setattr(module, "MAX_GAME_EXPORT_BYTES", total)
    original = zipfile.ZipExtFile.read
    consumed = []

    def read(stream, size=-1):
        data = original(stream, size)
        consumed.append(len(data))
        return data

    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)
    with pytest.raises(ValueError, match="limit"):
        verify_game_export(directory, "song")
    assert sum(consumed) <= total + 1


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, zipfile.BadZipFile])
def test_zip_guard_preserves_cancel_exception(exported, monkeypatch, error_type):
    directory, _ = exported
    original = zipfile.ZipFile.open
    started = []
    error = error_type("callback cancellation")

    def opened(archive, *args, **kwargs):
        result = original(archive, *args, **kwargs)
        started.append(True)
        return result

    def cancel():
        if started:
            raise error

    monkeypatch.setattr(zipfile.ZipFile, "open", opened)
    with pytest.raises(error_type) as caught:
        verify_game_export(directory, "song", cancel)
    assert caught.value is error
