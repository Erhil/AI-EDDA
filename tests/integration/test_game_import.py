import hashlib
import json

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from ai_edda.audio_io import import_audio
from ai_edda.domain import (
    AudioTimeRef,
    Chart,
    ChartMetadata,
    ChartNote,
    HitEvent,
    SongMetadata,
    TempoSegment,
    TimingGrid,
)
from ai_edda.game_assets import import_game_folder
from ai_edda.packaging import ArtifactBuilder, ExportSnapshot


@pytest.fixture
def package(tmp_path):
    wav = tmp_path / "input.wav"
    sf.write(wav, np.zeros(48000), 48000)
    audio = import_audio(wav, tmp_path)
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (512, 512), "red").save(cover)
    grid = TimingGrid(
        segments=(
            TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),
            TempoSegment(start_time_sec=2, start_beat_q=4, bpm_quarter=150, grid_division=4),
        )
    )
    charts = tuple(
        Chart(
            metadata=ChartMetadata(rank=i + 1, speed=15 + i),
            events=(
                HitEvent(
                    time_ref=AudioTimeRef(kind="audio", time_sec=0.5 + i * 0.1),
                    notes=(ChartNote(lane=0), ChartNote(lane=3)),
                ),
            ),
        )
        for i in range(3)
    )
    snapshot = ExportSnapshot(
        SongMetadata(
            title="Title", subtitle="Subtitle", artist="Artist", mapper="Mapper", explicit=True
        ),
        grid,
        charts,
        audio,
        "cover.jpg",
        hashlib.sha256(cover.read_bytes()).hexdigest(),
        0,
        "snapshot",
    )
    ArtifactBuilder(tmp_path).build(snapshot, tmp_path / "out", "song")
    return tmp_path / "out" / "song", snapshot


def test_roundtrip(tmp_path, package):
    folder, original = package
    imported = import_game_folder(folder, tmp_path / "private")
    project = imported.project
    assert project.audio.sha256 == original.audio.sha256
    assert project.metadata == original.metadata
    assert [(c.metadata.rank, c.metadata.speed) for c in imported.charts] == [
        (c.metadata.rank, c.metadata.speed) for c in original.selected_charts
    ]
    for old, new in zip(original.selected_charts, imported.charts):
        assert old.id != new.id
        assert new.provenance.source_origin == "unknown"
        assert old.events[0].id != new.events[0].id
        assert new.events[0].time_ref.time_sec == pytest.approx(
            old.events[0].time_ref.time_sec, abs=1e-6
        )
        assert [n.lane for n in new.events[0].notes] == [0, 3]
        assert all(n.provenance.source_origin == "unknown" for n in new.events[0].notes)
    for old, new in zip(original.grid.segments, imported.grid.segments):
        assert new.start_time_sec == pytest.approx(old.start_time_sec, abs=1e-6)
        assert new.bpm_quarter == old.bpm_quarter
    snap = ExportSnapshot(
        project.metadata,
        imported.grid,
        imported.charts,
        project.audio,
        project.cover.path,
        project.cover.sha256,
        0,
        "imported",
    )
    ArtifactBuilder(tmp_path / "private").build(snap, tmp_path / "again", "song")
    again = import_game_folder(tmp_path / "again" / "song", tmp_path / "private2")
    assert again.project.audio.sha256 == original.audio.sha256
    assert again.project.metadata == original.metadata
    assert [
        [(e.time_ref.time_sec, [n.lane for n in e.notes]) for e in c.events] for c in again.charts
    ] == [
        [(e.time_ref.time_sec, [n.lane for n in e.notes]) for e in c.events]
        for c in imported.charts
    ]


def test_bad_references(tmp_path, package):
    folder, _ = package
    path = folder / "info.dat"
    info = json.loads(path.read_text(encoding="utf-8"))
    info["_songFilename"] = "../outside.dat"
    path.write_text(json.dumps(info), encoding="utf-8")
    with pytest.raises(ValueError):
        import_game_folder(folder, tmp_path / "private")
    info["_songFilename"] = info["_coverImageFilename"].upper()
    path.write_text(json.dumps(info), encoding="utf-8")
    with pytest.raises(ValueError, match="collision"):
        import_game_folder(folder, tmp_path / "private")


def test_invalid_cover_and_copy_failure_cleanup(tmp_path, package, monkeypatch):
    folder, _ = package
    destination = tmp_path / "private"
    destination.mkdir()
    sentinel = destination / "sentinel"
    sentinel.write_text("keep")
    cover = folder / "cover.jpg"
    saved = cover.read_bytes()
    cover.write_bytes(b"bad")
    with pytest.raises(Exception):
        import_game_folder(folder, destination)
    cover.write_bytes(saved)
    import ai_edda.game_assets as assets

    def fail(*args):
        raise OSError("copy failed")

    monkeypatch.setattr(assets.shutil, "copyfile", fail)
    with pytest.raises(OSError):
        import_game_folder(folder, destination)
    assert sentinel.read_text() == "keep"
    assert not any(p.is_file() for p in destination.rglob("*") if p != sentinel)


def test_cover_uuid_collision_preserves_existing_directory(tmp_path, package, monkeypatch):
    folder, _ = package
    destination = tmp_path / "private"
    existing = destination / "cover" / "fixed-id"
    existing.mkdir(parents=True)
    sentinel = existing / "sentinel"
    sentinel.write_text("keep")
    import ai_edda.game_assets as assets

    monkeypatch.setattr(assets, "uuid4", lambda: "fixed-id")
    with pytest.raises(FileExistsError):
        import_game_folder(folder, destination)
    assert sentinel.read_text() == "keep"
