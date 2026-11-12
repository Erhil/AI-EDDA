import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from test_game_import import package as package
from test_native_media_review import media as media

from ai_edda import import_worker as worker
from ai_edda.import_receipts import build_import_receipt, publish_import_receipt
from ai_edda.native_capture import capture_inventory, hash_inventory
from ai_edda.native_envelope import serialize_native_envelope
from ai_edda.native_project import validate_native_project
from ai_edda.native_zip import write_native_zip
from ai_edda.project_store import _json


def files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def payload(workspace, kind="native", size=None):
    return {
        "workspace": str(workspace),
        "job_id": uuid4().hex,
        "request_context": {
            "request_id": str(uuid4()),
            "archive_kind": kind,
            "file_name": "upload.zip",
            "file_size": size if size is not None else (workspace / "source.zip").stat().st_size,
        },
    }


@pytest.fixture
def native_upload(tmp_path, media):
    root = media["root"]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inventory = capture_inventory(
        root,
        media["history"].materialize_current().project.id,
        (root / "project.json").read_bytes(),
    )
    envelope = hash_inventory(inventory)
    write_native_zip(workspace / "source.zip", inventory, envelope)
    return workspace, payload(workspace), root, envelope


@pytest.fixture
def game_upload(tmp_path, package):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copyfile(package[0].parent / "song.zip", workspace / "source.zip")
    return workspace, payload(workspace, "game")


def test_native_import_preserves_real_history_media_and_adds_exactly_one_bound_receipt(
    native_upload,
):
    workspace, data, source, envelope = native_upload
    before = files(source)
    source_zip = (workspace / "source.zip").read_bytes()
    progress = []
    result = worker.import_worker(data, progress.append, Event())
    assert set(result) == {"receipt"}
    record = result["receipt"]
    assert record["job_id"] == data["job_id"]
    assert record["request_id"] == data["request_context"]["request_id"]
    assert record["request_sha256"] == hashlib.sha256(_json(data["request_context"])).hexdigest()
    assert record["archive_sha256"] == hashlib.sha256(source_zip).hexdigest()
    assert record["initial_head_sha256"] == hashlib.sha256(before["project.json"]).hexdigest()
    assert (
        record["initial_inventory_sha256"]
        == hashlib.sha256(serialize_native_envelope(envelope)).hexdigest()
    )
    prepared = workspace / "prepared" / "project"
    after = files(prepared)
    expected_receipt = "imports/" + record["request_id"] + ".json"
    assert set(after) == set(before) | {expected_receipt}
    assert all(after[name] == contents for name, contents in before.items())
    assert json.loads(after[expected_receipt]) == record
    validated = validate_native_project(prepared, record["project_id"])
    assert validated.project.revision == record["public_result"]["revision"]
    assert files(source) == before
    assert (workspace / "source.zip").read_bytes() == source_zip
    assert progress == [1.0]


def test_actual_game_builder_import_makes_new_ids_and_a_complete_native_project(
    game_upload, tmp_path
):
    workspace, data = game_upload
    before = (workspace / "source.zip").read_bytes()
    first = worker.import_worker(data, lambda _: None, Event())["receipt"]
    initial = validate_native_project(workspace / "prepared" / "project", first["project_id"])
    assert initial.project.revision == initial.grid.revision == 0
    assert len(initial.charts) == 3
    assert initial.project.provenance.created_by == "imported"
    other = tmp_path / "other-workspace"
    other.mkdir()
    (other / "source.zip").write_bytes(before)
    second = worker.import_worker(payload(other, "game"), lambda _: None, Event())["receipt"]
    assert first["project_id"] != second["project_id"]
    assert first["initial_head_sha256"] != second["initial_head_sha256"]
    assert first["archive_sha256"] == second["archive_sha256"]
    assert (workspace / "source.zip").read_bytes() == before


def test_retry_returns_original_receipt_without_extraction_or_new_ids(
    native_upload, tmp_path, monkeypatch
):
    workspace, data, _, _ = native_upload
    original = worker.import_worker(data, lambda _: None, Event())["receipt"]
    retry = tmp_path / "retry"
    retry.mkdir()
    shutil.copyfile(workspace / "source.zip", retry / "source.zip")
    data = {**data, "workspace": str(retry), "job_id": uuid4().hex, "prior_receipt": original}

    def forbidden(*args, **kwargs):
        raise AssertionError("receipt retry must not extract, validate a project, or publish")

    for name in (
        "read_native_zip",
        "prepare_game_archive",
        "validate_native_project",
        "publish_import_receipt",
    ):
        monkeypatch.setattr(worker, name, forbidden)
    before = files(retry)
    result = worker.import_worker(data, lambda _: None, Event())
    assert result["receipt"] is original
    assert result["receipt"]["job_id"] != data["job_id"]
    assert files(retry) == before


@pytest.mark.parametrize("change", ["request", "bytes", "strict-type", "extra-field"])
def test_retry_conflicts_and_invalid_receipts_fail_before_extraction(tmp_path, monkeypatch, change):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.zip").write_bytes(b"not-a-zip-but-hashed")
    data = payload(workspace)
    context = data["request_context"]
    original = build_import_receipt(
        context,
        "original-job",
        hashlib.sha256((workspace / "source.zip").read_bytes()).hexdigest(),
        str(uuid4()),
        0,
        "a" * 64,
        "b" * 64,
    ).model_dump(mode="json")
    if change == "request":
        data["request_context"] = {**context, "file_name": "different.zip"}
    elif change == "bytes":
        (workspace / "source.zip").write_bytes(b"X" * context["file_size"])
    elif change == "strict-type":
        original["public_result"]["revision"] = True
    else:
        original["extra"] = "bad"
    data["prior_receipt"] = original

    def forbidden(*args, **kwargs):
        raise AssertionError("conflicting retry must not extract")

    monkeypatch.setattr(worker, "read_native_zip", forbidden)
    before = files(workspace)
    with pytest.raises(ValueError):
        worker.import_worker(data, lambda _: None, Event())
    assert files(workspace) == before


@pytest.mark.parametrize(
    "change",
    [
        {"workspace": "relative"},
        {"job_id": "not-a-job"},
        {"job_id": "A" * 32},
        {"request_context": {}},
        {"request_context": {"extra": True}},
    ],
)
def test_malformed_payload_is_rejected_before_opening_source(tmp_path, change):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = {**payload(workspace, size=1), **change}
    with pytest.raises(ValueError):
        worker.import_worker(data, lambda _: None, Event())
    assert not list(workspace.iterdir())


@pytest.mark.parametrize(
    "changes",
    [
        {"file_size": True},
        {"file_size": 0},
        {"file_size": 1024**3 + 1},
        {"file_name": "../source.zip"},
        {"archive_kind": "unsupported"},
        {"request_id": "not-a-uuid"},
        {"extra": 1},
    ],
)
def test_request_metadata_is_strict_and_exact(tmp_path, changes):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = payload(workspace, size=1)
    data["request_context"].update(changes)
    with pytest.raises(ValueError):
        worker.import_worker(data, lambda _: None, Event())
    assert not list(workspace.iterdir())


def test_incoming_same_request_receipt_is_rejected_and_owned_preparation_removed(native_upload):
    workspace, data, source, envelope = native_upload
    record = build_import_receipt(
        {**data["request_context"], "file_size": 1},
        "previous-job",
        "a" * 64,
        envelope.project_id,
        0,
        "b" * 64,
        "c" * 64,
    )
    publish_import_receipt(source, record)
    (workspace / "source.zip").unlink()
    inventory = capture_inventory(
        source, envelope.project_id, (source / "project.json").read_bytes()
    )
    write_native_zip(workspace / "source.zip", inventory, hash_inventory(inventory))
    data["request_context"]["file_size"] = (workspace / "source.zip").stat().st_size
    before = files(workspace)
    with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
        worker.import_worker(data, lambda _: None, Event())
    assert files(workspace) == before


def test_declared_size_mismatch_rejects_without_preparation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.zip").write_bytes(b"data")
    with pytest.raises(ValueError, match="size mismatch"):
        worker.import_worker(payload(workspace, size=3), lambda _: None, Event())
    assert {path.name for path in workspace.iterdir()} == {"source.zip"}


def test_already_cancelled_import_never_opens_source(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cancel = Event()
    cancel.set()
    with pytest.raises(RuntimeError, match="Import cancelled"):
        worker.import_worker(payload(workspace, size=1), lambda _: None, cancel)
    assert not list(workspace.iterdir())


def test_existing_prepared_directory_is_preserved(game_upload):
    workspace, data = game_upload
    prepared = workspace / "prepared"
    prepared.mkdir()
    (prepared / "sentinel").write_bytes(b"keep")
    before = files(workspace)
    with pytest.raises(FileExistsError):
        worker.import_worker(data, lambda _: None, Event())
    assert files(workspace) == before


@pytest.mark.parametrize("stage", ["publish_import_receipt", "progress"])
def test_failure_removes_only_owned_preparation(game_upload, monkeypatch, stage):
    workspace, data = game_upload
    before = files(workspace)

    def fail(*args, **kwargs):
        raise OSError("injected failure")

    if stage == "publish_import_receipt":
        monkeypatch.setattr(worker, stage, fail)
    with pytest.raises(OSError, match="injected failure"):
        worker.import_worker(data, fail if stage == "progress" else lambda _: None, Event())
    assert files(workspace) == before


def test_cancellation_after_game_preparation_cleans_owned_directory(game_upload, monkeypatch):
    workspace, data = game_upload
    original = worker.prepare_game_archive
    cancel = Event()

    def prepare(*args, **kwargs):
        result = original(*args, **kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(worker, "prepare_game_archive", prepare)
    before = files(workspace)
    with pytest.raises(RuntimeError, match="Import cancelled"):
        worker.import_worker(data, lambda _: None, cancel)
    assert files(workspace) == before


def test_native_archive_payload_hash_mismatch_cleans_preparation(native_upload):
    workspace, data, source, _ = native_upload
    with zipfile.ZipFile(workspace / "source.zip") as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    grid_name = next(name for name in entries if name.startswith("project/grid-"))
    entries[grid_name] += b" "
    with zipfile.ZipFile(workspace / "source.zip", "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    data["request_context"]["file_size"] = (workspace / "source.zip").stat().st_size
    before, source_before = files(workspace), files(source)
    with pytest.raises(ValueError, match="Archive file mismatch"):
        worker.import_worker(data, lambda _: None, Event())
    assert files(workspace) == before and files(source) == source_before


def test_cancellation_while_hashing_closes_source_without_preparation(game_upload, monkeypatch):
    workspace, data = game_upload
    before = files(workspace)
    cancel = Event()

    class Trigger(io.BytesIO):
        def read(self, size):
            result = super().read(min(size, 1))
            cancel.set()
            return result

    response = Trigger((workspace / "source.zip").read_bytes())
    original = Path.open
    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "open",
            lambda path, *args, **kwargs: (
                response if path == workspace / "source.zip" else original(path, *args, **kwargs)
            ),
        )
        with pytest.raises(RuntimeError, match="Import cancelled"):
            worker.import_worker(data, lambda _: None, cancel)
    assert response.closed and files(workspace) == before


def test_cancellation_set_by_final_progress_is_observed(game_upload):
    workspace, data = game_upload
    cancel = Event()
    before = files(workspace)
    with pytest.raises(RuntimeError, match="Import cancelled"):
        worker.import_worker(data, lambda _: cancel.set(), cancel)
    assert files(workspace) == before


def test_actual_stream_size_is_bounded_even_if_source_never_ends(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = payload(workspace, size=1024**3)
    block = b"x" * 1024 * 1024

    class Endless(io.BytesIO):
        reads = 0

        def read(self, size):
            assert size <= 1024 * 1024
            self.reads += 1
            assert self.reads <= 1026, "worker kept reading beyond the 1 GiB actual-size ceiling"
            return block

    class Digest:
        def update(self, value):
            pass

        def hexdigest(self):
            return "0" * 64

    original = Path.open
    monkeypatch.setattr(
        Path,
        "open",
        lambda path, *args, **kwargs: (
            Endless() if path == workspace / "source.zip" else original(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(worker.hashlib, "sha256", lambda *args: Digest())
    with pytest.raises(ValueError):
        worker.import_worker(data, lambda _: None, Event())
    assert not (workspace / "prepared").exists()
