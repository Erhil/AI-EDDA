import json
import zipfile
from dataclasses import replace

import pytest
from test_game_import import package as package

from ai_edda import game_archive, native_history
from ai_edda.asset_catalog import resolve_audio
from ai_edda.cover_catalog import resolve_cover
from ai_edda.game_archive import prepare_game_archive
from ai_edda.native_history import validate_native_history
from ai_edda.native_media import validate_native_media
from ai_edda.packaging import ArtifactBuilder


def archive(folder, path, changes=None, omit=()):
    files = {p.name: p.read_bytes() for p in folder.iterdir()}
    files.update(changes or {})
    with zipfile.ZipFile(path, "w") as output:
        for name, data in files.items():
            if name not in omit:
                output.writestr(name, data)
    return path


def test_roundtrip(tmp_path, package):
    folder, original = package
    source = archive(folder, tmp_path / "game.zip")
    before = source.read_bytes()
    first = prepare_game_archive(source, tmp_path / "one")
    second = prepare_game_archive(source, tmp_path / "two")
    assert source.read_bytes() == before
    assert first.project.id != second.project.id
    assert first.charts[0].id != second.charts[0].id
    assert first.project.audio.path != second.project.audio.path
    assert first.project.cover.path != second.project.cover.path
    assert first.project.metadata == original.metadata
    assert (
        resolve_audio(tmp_path / "one", first.project.audio.path.split("/")[1])
        == first.project.audio
    )
    assert (
        resolve_cover(tmp_path / "one", first.project.cover.path.split("/")[1])
        == first.project.cover
    )
    info = json.loads((folder / "info.dat").read_text(encoding="utf-8"))
    assert (tmp_path / "one" / first.project.audio.path).read_bytes() == (
        folder / info["_songFilename"]
    ).read_bytes()
    assert (tmp_path / "one" / first.project.cover.path).read_bytes() == (
        folder / info["_coverImageFilename"]
    ).read_bytes()


@pytest.mark.parametrize(
    "changes",
    [{"extra": b"x"}, {"sub/file": b"x"}, {"info.dat": b"{bad"}, {"info.dat": b'{"a":1,"a":2}'}],
)
def test_invalid_archive(tmp_path, package, changes):
    source = archive(package[0], tmp_path / "bad.zip", changes)
    with pytest.raises((ValueError, KeyError)):
        prepare_game_archive(source, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


def test_existing_and_cancel(tmp_path, package):
    source = archive(package[0], tmp_path / "game.zip")
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        prepare_game_archive(source, destination)

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError):
        prepare_game_archive(source, tmp_path / "cancelled", cancel)
    assert destination.is_dir()
    assert not (tmp_path / "cancelled").exists()


@pytest.mark.parametrize("stage", ["history", "materialize"])
def test_cancellation_reaches_index_validation_and_final_materialization(
    tmp_path, package, monkeypatch, stage
):
    from ai_edda.native_history_index import NativeHistoryIndex

    source = archive(package[0], tmp_path / "game.zip")
    before = source.read_bytes()
    destination = tmp_path / "cancelled-index"
    error = RuntimeError("cancel in " + stage)
    active = False
    target, name = (
        (game_archive, "validate_native_history")
        if stage == "history"
        else (NativeHistoryIndex, "materialize_current")
    )
    original = getattr(target, name)

    def entered(*args, **kwargs):
        nonlocal active
        active = True
        try:
            return original(*args, **kwargs)
        finally:
            active = False

    monkeypatch.setattr(target, name, entered)

    def cancel():
        if active:
            raise error

    with pytest.raises(RuntimeError) as caught:
        prepare_game_archive(source, destination, cancel)
    assert caught.value is error
    assert not destination.exists()
    assert source.read_bytes() == before


@pytest.mark.parametrize("count", [1, 2, 3])
def test_actual_artifact_builder_archive_becomes_valid_initial_native_project(
    tmp_path, package, count
):
    _, original = package
    exported = replace(original, selected_charts=original.selected_charts[:count])
    output = tmp_path / f"export-{count}"
    ArtifactBuilder(tmp_path).build(exported, output, "subset")
    source = output / "subset.zip"
    before = source.read_bytes()
    destination = tmp_path / f"prepared-{count}"
    prepared = prepare_game_archive(source, destination)
    assert source.read_bytes() == before
    assert (
        prepared.project.revision == prepared.project.grid_revision == prepared.grid.revision == 0
    )
    assert len(prepared.charts) == count
    assert prepared.project.metadata == original.metadata
    assert prepared.project.analysis is None and prepared.project.plan_inputs is None
    assert prepared.project.provenance.created_by == "imported"
    assert prepared.project.provenance.source_origin == "unknown"
    assert prepared.project.audio.sha256 == original.audio.sha256
    assert (destination / prepared.project.audio.path).read_bytes() == (
        output / "subset" / "song.ogg"
    ).read_bytes()
    assert (destination / prepared.project.cover.path).read_bytes() == (
        output / "subset" / "cover.jpg"
    ).read_bytes()
    for source_chart, chart in zip(exported.selected_charts, prepared.charts):
        assert chart.id != source_chart.id
        assert chart.revision == chart.grid_revision == 0
        assert (chart.metadata.rank, chart.metadata.speed) == (
            source_chart.metadata.rank,
            source_chart.metadata.speed,
        )
        assert (
            chart.provenance.created_by == "imported"
            and chart.provenance.source_origin == "unknown"
        )
        for old_event, event in zip(source_chart.events, chart.events):
            assert event.id != old_event.id
            assert event.time_ref.time_sec == pytest.approx(old_event.time_ref.time_sec, abs=1e-6)
            assert (
                event.provenance.created_by == "imported"
                and event.provenance.source_origin == "unknown"
            )
            assert [note.lane for note in event.notes] == [note.lane for note in old_event.notes]
            assert {note.id for note in event.notes}.isdisjoint(note.id for note in old_event.notes)
            assert all(
                note.provenance.created_by == "imported"
                and note.provenance.source_origin == "unknown"
                for note in event.notes
            )
    history = validate_native_history(destination, prepared.project.id)
    assert history.materialize_current() == prepared and history.receipts == {}
    validate_native_media(destination, history)
    assert json.loads((destination / "project.json").read_bytes())["receipt"] is None


@pytest.mark.parametrize("missing", ["info.dat", "song.ogg", "cover.jpg", "Easy.dat"])
def test_missing_referenced_file_rejects_and_preserves_source(tmp_path, package, missing):
    source = archive(package[0], tmp_path / "missing.zip", omit=(missing,))
    before = source.read_bytes()
    destination = tmp_path / "destination"
    with pytest.raises(ValueError):
        prepare_game_archive(source, destination)
    assert not destination.exists()
    assert source.read_bytes() == before


@pytest.mark.parametrize("extra", ["empty/", "INFO.DAT", "unreferenced.dat"])
def test_empty_directories_case_collisions_and_unreferenced_files_are_rejected(
    tmp_path, package, extra
):
    source = archive(package[0], tmp_path / "extra.zip", {extra: b"{}"})
    with pytest.raises(ValueError):
        prepare_game_archive(source, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


def test_case_insensitive_archive_names_match_original_references(tmp_path, package):
    source = tmp_path / "uppercase.zip"
    with zipfile.ZipFile(source, "w") as output:
        for path in package[0].iterdir():
            output.writestr(path.name.upper(), path.read_bytes())
    assert len(prepare_game_archive(source, tmp_path / "destination").charts) == 3


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate-info",
        "duplicate-difficulty",
        "nonfinite",
        "depth",
        "file-bytes",
        "total-bytes",
        "nodes",
    ],
)
def test_strict_json_preflight_finishes_before_supported_parser(
    tmp_path, package, monkeypatch, kind
):
    folder = package[0]
    info = (folder / "info.dat").read_bytes()
    changes = {}
    if kind == "duplicate-info":
        changes["info.dat"] = b'{"duplicate":1,"duplicate":2,' + info.lstrip()[1:]
    elif kind == "duplicate-difficulty":
        difficulty = (folder / "Easy.dat").read_bytes()
        changes["Easy.dat"] = b'{"duplicate":1,"duplicate":2,' + difficulty.lstrip()[1:]
    elif kind in ("nonfinite", "depth"):
        data = json.loads(info)
        nested = 0
        for _ in range(70):
            nested = [nested]
        data["_extra"] = float("nan") if kind == "nonfinite" else nested
        changes["info.dat"] = json.dumps(data).encode()
    elif kind == "file-bytes":
        monkeypatch.setattr(native_history, "MAX_HISTORY_JSON_BYTES", len(info) - 1)
    elif kind == "total-bytes":
        monkeypatch.setattr(game_archive, "MAX_TOTAL_BYTES", len(info) - 1)
    else:
        monkeypatch.setattr(game_archive, "MAX_NODES", 1)

    def forbidden(*args):
        raise AssertionError("unsafe JSON reached the supported parser")

    monkeypatch.setattr(game_archive, "import_game_folder", forbidden)
    source = archive(folder, tmp_path / "strict.zip", changes)
    with pytest.raises(ValueError):
        prepare_game_archive(source, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


@pytest.mark.parametrize(
    "stage",
    [
        "import_game_folder",
        "publish_audio",
        "publish_prepared_cover",
        "validate_native_history",
        "validate_native_media",
    ],
)
def test_failure_at_each_stage_removes_only_owned_destination(
    tmp_path, package, monkeypatch, stage
):
    source = archive(package[0], tmp_path / "game.zip")
    original_bytes = source.read_bytes()
    sentinel = tmp_path / "unrelated"
    sentinel.write_bytes(b"keep")

    def fail(*args, **kwargs):
        raise OSError("injected stage failure")

    monkeypatch.setattr(game_archive, stage, fail)
    with pytest.raises(OSError, match="injected stage failure"):
        prepare_game_archive(source, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()
    assert source.read_bytes() == original_bytes and sentinel.read_bytes() == b"keep"


@pytest.mark.parametrize("stage", ["import_game_folder", "publish_audio", "publish_prepared_cover"])
def test_mid_stage_cancellation_removes_complete_owned_result(
    tmp_path, package, monkeypatch, stage
):
    source = archive(package[0], tmp_path / "game.zip")
    original = getattr(game_archive, stage)
    stop = False

    def complete_then_cancel(*args, **kwargs):
        nonlocal stop
        result = original(*args, **kwargs)
        stop = True
        return result

    def cancel():
        if stop:
            raise InterruptedError("cancelled after stage")

    monkeypatch.setattr(game_archive, stage, complete_then_cancel)
    before = source.read_bytes()
    with pytest.raises(InterruptedError, match="cancelled after stage"):
        prepare_game_archive(source, tmp_path / "destination", cancel)
    assert not (tmp_path / "destination").exists()
    assert source.read_bytes() == before


def test_existing_destination_contents_and_source_remain_untouched(tmp_path, package):
    source = archive(package[0], tmp_path / "game.zip")
    destination = tmp_path / "destination"
    destination.mkdir()
    sentinel = destination / "sentinel"
    sentinel.write_bytes(b"owned by someone else")
    before = source.read_bytes()
    with pytest.raises(FileExistsError):
        prepare_game_archive(source, destination)
    assert sentinel.read_bytes() == b"owned by someone else"
    assert source.read_bytes() == before
