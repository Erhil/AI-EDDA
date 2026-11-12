import hashlib
import json
import os
import subprocess
from uuid import uuid4

import pytest
from test_game_import import package as package
from test_native_generation_review import generation as generation
from test_native_generation_review import inputs as inputs
from test_native_generation_review import media as media

from ai_edda import native_project
from ai_edda.application import ApplicationService
from ai_edda.game_archive import prepare_game_archive
from ai_edda.game_export_catalog import publish_game_export, resolve_game_export, verify_game_export
from ai_edda.import_receipts import build_import_receipt, publish_import_receipt
from ai_edda.native_capture import capture_inventory, hash_inventory
from ai_edda.native_project import validate_native_project
from ai_edda.native_zip import read_native_zip, write_native_zip
from ai_edda.packaging import ArtifactBuilder, ExportSnapshot
from ai_edda.project_store import ProjectStore, _json


def files(root):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture
def complete(tmp_path, package):
    prepared = tmp_path / "prepared"
    snapshot = prepare_game_archive(package[0].parent / "song.zip", prepared)
    root = tmp_path / "projects" / snapshot.project.id
    root.parent.mkdir()
    prepared.rename(root)
    return root, snapshot


@pytest.fixture
def exported(complete, tmp_path):
    root, snapshot = complete
    export_id = str(uuid4())
    value = ExportSnapshot(
        snapshot.project.metadata,
        snapshot.grid,
        snapshot.charts,
        snapshot.project.audio,
        snapshot.project.cover.path,
        snapshot.project.cover.sha256,
        snapshot.project.revision,
        export_id,
    )
    prepared = tmp_path / "export"
    ArtifactBuilder(root).build(value, prepared, "song")
    result = verify_game_export(prepared, "song")
    context = {
        "project_id": snapshot.project.id,
        "export_id": export_id,
        "project_revision": snapshot.project.revision,
    }
    publish_game_export(root, export_id, "export-job", context, result, prepared)
    return root, snapshot, export_id


def test_unknown_file_is_rejected(tmp_path):
    (tmp_path / "unknown").write_text("x")
    with pytest.raises(ValueError, match="Unknown native file"):
        validate_native_project(tmp_path, "00000000-0000-0000-0000-000000000000")


def test_unknown_empty_directory_is_rejected(tmp_path):
    (tmp_path / "unknown").mkdir()
    with pytest.raises(ValueError, match="Unknown empty directory"):
        validate_native_project(tmp_path, "00000000-0000-0000-0000-000000000000")


def test_cancellation_during_enumeration(tmp_path):
    (tmp_path / "project.json").write_text("{}")

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        validate_native_project(tmp_path, "00000000-0000-0000-0000-000000000000", cancel)


def test_complete_game_project_and_native_capture_roundtrip_are_read_only(complete, tmp_path):
    root, snapshot = complete
    before = files(root)
    assert validate_native_project(root, snapshot.project.id) == snapshot
    inventory = capture_inventory(root, snapshot.project.id, (root / "project.json").read_bytes())
    envelope = hash_inventory(inventory)
    archive = tmp_path / "backup.zip"
    write_native_zip(archive, inventory, envelope)
    read_envelope, restored = read_native_zip(archive, tmp_path / "restored")
    assert read_envelope == envelope
    restored_before = files(restored)
    assert validate_native_project(restored, snapshot.project.id) == snapshot
    assert restored_before == files(restored) == before
    assert files(root) == before


def test_all_real_native_layers_and_import_receipt_survive_capture_roundtrip(generation, tmp_path):
    root = generation["root"]
    snapshot = generation["source"]
    before = files(root)
    assert validate_native_project(root, snapshot.project.id) == snapshot
    assert files(root) == before
    inventory = capture_inventory(root, snapshot.project.id, (root / "project.json").read_bytes())
    envelope = hash_inventory(inventory)
    first_archive = tmp_path / "before-receipt.zip"
    write_native_zip(first_archive, inventory, envelope)
    context = {
        "request_id": str(uuid4()),
        "archive_kind": "native",
        "file_name": "before-receipt.zip",
        "file_size": first_archive.stat().st_size,
    }
    receipt = build_import_receipt(
        context,
        "import-job",
        hashlib.sha256(first_archive.read_bytes()).hexdigest(),
        snapshot.project.id,
        snapshot.project.revision,
        hashlib.sha256(inventory.head).hexdigest(),
        hashlib.sha256(_json(envelope.model_dump(mode="json"))).hexdigest(),
    )
    publish_import_receipt(root, receipt)
    before = files(root)
    assert validate_native_project(root, snapshot.project.id) == snapshot
    inventory = capture_inventory(root, snapshot.project.id, (root / "project.json").read_bytes())
    archive = tmp_path / "all-layers.zip"
    write_native_zip(archive, inventory, hash_inventory(inventory))
    _, restored = read_native_zip(archive, tmp_path / "all-layers-restored")
    assert validate_native_project(restored, snapshot.project.id) == snapshot
    assert files(restored) == files(root) == before


def test_export_binds_to_historical_state_after_current_edit(exported):
    root, original, _ = exported
    assert validate_native_project(root, original.project.id) == original
    with ProjectStore(root.parent) as store:
        service = ApplicationService(store)
        changed = service.execute(
            original.project.id,
            {
                "command_id": str(uuid4()),
                "kind": "update_song_metadata",
                "payload": {"title": "Later edit"},
                "base_project_revision": original.project.revision,
                "expected_grid_revision": original.grid.revision,
                "expected_chart_revisions": {chart.id: chart.revision for chart in original.charts},
            },
        )
    before = files(root)
    result = validate_native_project(root, original.project.id)
    assert result.project == changed.project and result.charts == changed.charts
    assert files(root) == before


@pytest.mark.parametrize("field", ["project", "grid", "selection", "audio", "cover"])
def test_export_requires_one_coherent_historical_snapshot(exported, monkeypatch, field):
    root, snapshot, export_id = exported
    publication = resolve_game_export(root, export_id)
    path = root / "exports" / export_id / "song.receipt.json"
    receipt = json.loads(path.read_bytes())
    if field == "project":
        receipt["project_revision"] += 1
    elif field == "grid":
        receipt["grid_revision"] += 1
    elif field == "selection":
        receipt["selection"][0]["revision"] += 1
    elif field == "audio":
        receipt["audio_sha256"] = "f" * 64
    else:
        receipt["members"]["cover.jpg"]["sha256"] = "f" * 64
    path.write_text(json.dumps(receipt), encoding="utf-8")
    # Isolate the glue binding after an accepted export-resolver result.
    monkeypatch.setattr(native_project, "resolve_game_export", lambda *args: publication)
    before = files(root)
    with pytest.raises(ValueError, match="matching historical snapshot"):
        validate_native_project(root, snapshot.project.id)
    assert files(root) == before


@pytest.mark.parametrize("missing", ["grid", "chart", "audio", "cover"])
def test_missing_required_reference_is_rejected_without_writes(complete, missing):
    root, snapshot = complete
    if missing == "grid":
        path = root / "grid-0.json"
    elif missing == "chart":
        path = root / f"chart-{snapshot.charts[0].id}-0.json"
    elif missing == "audio":
        path = root / snapshot.project.audio.path
    else:
        path = root / snapshot.project.cover.path
    path.unlink()
    before = files(root)
    with pytest.raises((ValueError, FileNotFoundError)):
        validate_native_project(root, snapshot.project.id)
    assert files(root) == before


@pytest.mark.parametrize("bound", ["MAX_FILES", "MAX_DECLARED_BYTES"])
def test_tree_limits_reject_before_layer_reads(complete, monkeypatch, bound):
    root, snapshot = complete
    monkeypatch.setattr(native_project, bound, 1)

    def forbidden(*args):
        raise AssertionError("tree budget must fail before semantic layer reads")

    monkeypatch.setattr(native_project, "validate_native_history", forbidden)
    before = files(root)
    with pytest.raises(ValueError, match="exceeds limit"):
        validate_native_project(root, snapshot.project.id)
    assert files(root) == before


@pytest.mark.parametrize("stage", ["validate_native_plans", "validate_native_generation"])
@pytest.mark.parametrize(
    "exception_type", [TypeError, KeyError, RecursionError, OverflowError, AttributeError, OSError]
)
def test_composed_cancellation_preserves_identity_inside_adapter_wrappers(
    complete, monkeypatch, stage, exception_type
):
    root, snapshot = complete
    original = getattr(native_project, stage)
    active = False
    error = exception_type("cancel inside adapter")

    def entered(*args, **kwargs):
        nonlocal active
        active = True
        try:
            return original(*args, **kwargs)
        finally:
            active = False

    monkeypatch.setattr(native_project, stage, entered)

    def cancel():
        if active:
            raise error

    before = files(root)
    with pytest.raises(exception_type) as caught:
        validate_native_project(root, snapshot.project.id, cancel)
    assert caught.value is error
    assert files(root) == before


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
def test_reparse_ancestry_is_rejected_without_inspecting_external_tree(complete, tmp_path):
    root, snapshot = complete
    link = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(root)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    before = files(root)
    try:
        with pytest.raises(ValueError, match="Unsafe path"):
            validate_native_project(link, snapshot.project.id)
        assert files(root) == before
    finally:
        link.rmdir()


@pytest.mark.parametrize(
    "exception_type", [RuntimeError, ValueError, TypeError, KeyError, OverflowError, AttributeError]
)
def test_cancellation_exception_is_preserved_inside_composed_validation(complete, exception_type):
    root, snapshot = complete
    enumeration_count = len(list(root.rglob("*")))
    calls = 0
    error = exception_type("cancelled inside media validation")

    def cancel():
        nonlocal calls
        calls += 1
        if calls > enumeration_count:
            raise error

    before = files(root)
    with pytest.raises(BaseException) as caught:
        validate_native_project(root, snapshot.project.id, cancel)
    assert caught.value is error
    assert files(root) == before


def test_cancellation_during_last_export_resolution_is_observed_before_return(
    exported, monkeypatch
):
    root, snapshot, _ = exported
    original = native_project.resolve_game_export
    stop = False

    def resolve(*args):
        nonlocal stop
        result = original(*args)
        stop = True
        return result

    def cancel():
        if stop:
            raise InterruptedError("cancelled during export verification")

    monkeypatch.setattr(native_project, "resolve_game_export", resolve)
    before = files(root)
    with pytest.raises(InterruptedError, match="cancelled during export"):
        validate_native_project(root, snapshot.project.id, cancel)
    assert files(root) == before
