import hashlib
import zipfile

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from ai_edda.audio_io import import_audio
from ai_edda.domain import Chart, ChartMetadata, SongMetadata, TempoSegment, TimingGrid
from ai_edda.packaging import ArtifactBuilder, ExportSnapshot


@pytest.fixture
def snapshot(tmp_path):
    wav = tmp_path / "input.wav"
    sf.write(wav, np.zeros(4800), 48000)
    audio = import_audio(wav, tmp_path)
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (512, 512)).save(cover)
    cover_sha256 = hashlib.sha256(cover.read_bytes()).hexdigest()
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    charts = tuple(Chart(metadata=ChartMetadata(rank=i + 1, speed=15)) for i in range(4))

    def make(count):
        return ExportSnapshot(
            SongMetadata(), grid, charts[:count], audio, "cover.jpg", cover_sha256, 0, "snapshot"
        )

    return make


@pytest.mark.parametrize("count", [1, 2, 3])
def test_package(tmp_path, snapshot, count):
    receipt = ArtifactBuilder(tmp_path).build(snapshot(count), tmp_path / "out", "song")
    folder = tmp_path / "out" / "song"
    with zipfile.ZipFile(tmp_path / "out" / "song.zip") as archive:
        assert set(archive.namelist()) == {p.name for p in folder.iterdir()}
        assert len(archive.namelist()) == count + 3
        for path in folder.iterdir():
            assert archive.read(path.name) == path.read_bytes()
    assert (
        hashlib.sha256((folder / "song.ogg").read_bytes()).hexdigest()
        == snapshot(count).audio.sha256
    )
    assert receipt["local_check"] == "passed"
    with pytest.raises(FileExistsError):
        ArtifactBuilder(tmp_path).build(snapshot(count), tmp_path / "out", "song")


def test_bad_inputs(tmp_path, snapshot):
    builder = ArtifactBuilder(tmp_path)
    with pytest.raises(ValueError):
        builder.build(snapshot(4), tmp_path / "out", "song")
    (tmp_path / "cover.jpg").write_bytes(b"bad")
    with pytest.raises(Exception):
        builder.build(snapshot(1), tmp_path / "out", "song")
    (tmp_path / snapshot(1).audio.path).unlink()
    with pytest.raises(FileNotFoundError):
        builder.build(snapshot(1), tmp_path / "out", "song")


def test_rejects_replaced_cover_after_snapshot(tmp_path, snapshot):
    export = snapshot(1)
    Image.new("RGB", (512, 512), color="red").save(tmp_path / "cover.jpg")
    with pytest.raises(ValueError, match="cover hash mismatch"):
        ArtifactBuilder(tmp_path).build(export, tmp_path / "out", "song")
    assert not list((tmp_path / "out").iterdir())


def test_rejects_invalid_cover_during_copy(tmp_path, snapshot, monkeypatch):
    export = snapshot(1)
    import ai_edda.packaging as packaging

    original_copyfile = packaging.shutil.copyfile

    def substitute_cover(source, destination):
        if str(source).endswith("cover.jpg"):
            with open(destination, "wb") as stream:
                stream.write(b"invalid cover bytes")
            return destination
        return original_copyfile(source, destination)

    monkeypatch.setattr(packaging.shutil, "copyfile", substitute_cover)
    with pytest.raises(ValueError, match="cover changed during export"):
        ArtifactBuilder(tmp_path).build(export, tmp_path / "out", "song")
    assert not list((tmp_path / "out").iterdir())
