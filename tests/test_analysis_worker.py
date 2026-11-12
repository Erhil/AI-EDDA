import hashlib
import os
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ai_edda.analysis_worker import analysis_worker

CHECKPOINT = Path(
    os.environ.get(
        "AI_EDDA_TEST_CHECKPOINT",
        str(Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "AI-EDDA/models/beat-this/final0.ckpt"),
    )
)


def test_analysis_worker_rejects_pre_cancelled_job():
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        analysis_worker({}, lambda _: None, cancelled)


@pytest.mark.skipif(not CHECKPOINT.is_file(), reason="Beat This checkpoint is absent")
def test_combined_analysis(tmp_path):
    audio = tmp_path / "final.ogg"
    sr = 22050
    samples = np.sin(2 * np.pi * 440 * np.arange(sr * 2) / sr).astype("float32") * 0.2
    sf.write(audio, samples, sr, format="OGG", subtype="VORBIS")
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    payload = {
        "workspace": str(tmp_path / "work"),
        "canonical_ogg": str(audio.resolve()),
        "audio": {
            "path": "final.ogg",
            "sha256": digest,
            "sample_rate": sr,
            "channels": 1,
            "frames": len(samples),
            "duration_sec": 2.0,
            "subtype": "VORBIS",
        },
        "audio_asset_id": "asset",
        "checkpoint": str(CHECKPOINT),
        "device": "cpu",
        "float16": False,
    }
    cancelled = threading.Event()
    progress = []
    result = analysis_worker(payload, progress.append, cancelled)
    assert result["audio"]["sha256"] == digest
    assert progress == sorted(progress) and progress[-1] == 1
    for role in result["roles"].values():
        for kind in ("manifest", "npz"):
            path = Path(payload["workspace"]) / role[f"{kind}_path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == role[f"{kind}_sha256"]
        with np.load(Path(payload["workspace"]) / role["npz_path"], allow_pickle=False) as data:
            assert all(np.isfinite(data[name]).all() for name in data.files)
