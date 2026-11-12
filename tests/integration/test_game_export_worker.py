import hashlib
import threading
import zipfile
from dataclasses import asdict
from uuid import uuid4

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from ai_edda.audio_io import import_audio
from ai_edda.domain import Chart, ChartMetadata, SongMetadata, TempoSegment, TimingGrid
from ai_edda.game_export_worker import game_export_worker
from ai_edda.packaging import ArtifactBuilder, ExportSnapshot


@pytest.fixture
def payload(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    wav = project / "input.wav"
    sf.write(wav, np.zeros(4800), 48000)
    audio = import_audio(wav, project)
    cover = project / "cover.jpg"
    Image.new("RGB", (512, 512)).save(cover)
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    snapshot = ExportSnapshot(
        SongMetadata(title="Captured"),
        grid,
        (Chart(metadata=ChartMetadata(rank=1, speed=15)),),
        audio,
        "cover.jpg",
        hashlib.sha256(cover.read_bytes()).hexdigest(),
        2,
        str(uuid4()),
    )
    return {
        "project_dir": str(project),
        "workspace": str(workspace),
        "folder_name": "song",
        "snapshot": asdict(snapshot),
    }


def test_build_and_verify(payload, monkeypatch):
    from pathlib import Path

    project = Path(payload["project_dir"])
    (project / "project.json").write_text("invalid head", encoding="utf-8")
    original = ArtifactBuilder.build
    calls = []

    def build(self, *args):
        calls.append(args)
        return original(self, *args)

    monkeypatch.setattr(ArtifactBuilder, "build", build)
    result = game_export_worker(payload, lambda value: None, threading.Event())
    root = Path(payload["workspace"]) / "prepared"
    assert len(calls) == 1
    assert result["receipt"]["project_revision"] == 2
    assert b"Captured" in (root / "song" / "info.dat").read_bytes()
    with zipfile.ZipFile(root / "song.zip") as archive:
        for path in (root / "song").iterdir():
            assert archive.read(path.name) == path.read_bytes()
    assert (
        hashlib.sha256((root / "song.receipt.json").read_bytes()).hexdigest()
        == result["receipt_sha256"]
    )


def test_cancel_and_no_overwrite(payload):
    from pathlib import Path

    event = threading.Event()
    event.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        game_export_worker(payload, lambda value: None, event)
    root = Path(payload["workspace"]) / "prepared"
    assert not root.exists()
    root.mkdir()
    (root / "keep").write_text("safe")
    with pytest.raises(FileExistsError):
        game_export_worker(payload, lambda value: None, threading.Event())
    assert (root / "keep").read_text() == "safe"


def test_failure_and_final_cancel(payload):
    from pathlib import Path

    event = threading.Event()

    def progress(value):
        if value == 1.0:
            event.set()

    with pytest.raises(RuntimeError, match="cancelled"):
        game_export_worker(payload, progress, event)
    assert (Path(payload["workspace"]) / "prepared" / "song.zip").exists()
