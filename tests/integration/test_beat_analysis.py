import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ai_edda.audio_io import import_audio
from ai_edda.beat_analysis import MODEL_SHA256, analyze_beats

CHECKPOINT = Path(
    os.environ.get(
        "AI_EDDA_TEST_CHECKPOINT",
        str(Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "AI-EDDA/models/beat-this/final0.ckpt"),
    )
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_import_does_not_load_torch():
    subprocess.run(
        [
            sys.executable,
            "-c",
            'import sys; import ai_edda.beat_analysis; assert "torch" not in sys.modules',
        ],
        check=True,
    )


def test_real_checkpoint_and_cache(tmp_path):
    if not CHECKPOINT.is_file():
        pytest.skip("Beat This checkpoint is absent")
    sr = 22050
    signal = np.zeros(sr * 5, dtype=np.float32)
    for start in range(0, len(signal), sr // 2):
        signal[start : start + 220] = np.hanning(220).astype(np.float32) * 0.8
    wav = tmp_path / "pulses.wav"
    sf.write(wav, signal, sr)
    asset = import_audio(wav, tmp_path)
    ogg = tmp_path / asset.path
    original_hash = digest(ogg)
    payload = {
        "canonical_ogg": str(ogg),
        "checkpoint": str(CHECKPOINT),
        "expected_audio_sha256": asset.sha256,
        "output_dir": str(tmp_path / "output"),
        "device": "cpu",
        "float16": False,
    }
    progress = []
    result = analyze_beats(payload, progress.append, threading.Event())
    assert progress == [0.1, 0.3, 0.8, 1.0]
    manifest_path, npz_path = Path(result["manifest_path"]), Path(result["npz_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["config"]["audio_sha256"] == original_hash == asset.sha256
    assert manifest["config"]["model_sha256"] == MODEL_SHA256 == digest(CHECKPOINT)
    assert manifest["npz_sha256"] == result["npz_sha256"] == digest(npz_path)
    assert result["manifest_sha256"] == digest(manifest_path)
    with np.load(npz_path, allow_pickle=False) as arrays:
        for name in ("beat_logits", "downbeat_logits", "beat_times", "downbeat_times"):
            assert np.issubdtype(arrays[name].dtype, np.number)
            assert np.isfinite(arrays[name]).all()
        assert arrays["beat_times"].size > 0
        for name in ("beat_times", "downbeat_times"):
            assert ((arrays[name] >= 0) & (arrays[name] <= asset.duration_sec)).all()
    before = [(p.stat().st_mtime_ns, digest(p)) for p in (manifest_path, npz_path)]
    assert (
        analyze_beats(payload, lambda _: pytest.fail("Cache emitted progress"), threading.Event())
        == result
    )
    assert before == [(p.stat().st_mtime_ns, digest(p)) for p in (manifest_path, npz_path)]
    assert digest(ogg) == original_hash
    with pytest.raises(ValueError, match="Audio SHA-256 mismatch"):
        analyze_beats(
            {**payload, "expected_audio_sha256": "0" * 64}, lambda _: None, threading.Event()
        )
    with pytest.raises(FileNotFoundError, match="model is missing"):
        analyze_beats(
            {**payload, "checkpoint": str(tmp_path / "missing.ckpt")},
            lambda _: None,
            threading.Event(),
        )
