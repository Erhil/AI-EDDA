"""Audio activity features. Centered 2048-sample windows use time frame * 441 / 22050."""

import hashlib
import json
import os
import shutil
import tempfile
from importlib import metadata
from pathlib import Path

from .beat_analysis import _hash


def analyze_features(payload, progress, cancel):
    def check():
        if cancel.is_set():
            raise RuntimeError("Rhythm feature analysis cancelled")

    check()
    audio = Path(payload["canonical_ogg"])
    digest = _hash(audio)
    if digest != payload["expected_audio_sha256"]:
        raise ValueError("Audio SHA-256 mismatch")
    import librosa
    import numpy as np
    import soundfile as sf

    from .audio_io import _validate_ogg_pages

    info = sf.info(str(audio))
    if info.format != "OGG" or info.subtype != "VORBIS":
        raise ValueError("Audio must be OGG Vorbis")
    _validate_ogg_pages(audio)
    config = {
        "version": 1,
        "audio_sha256": digest,
        "sample_rate": 22050,
        "hop_length": 441,
        "n_fft": 2048,
        "center": True,
        "frame_time": "frame * hop_length / sample_rate",
        "versions": {
            n: metadata.version(n) for n in ("numpy", "librosa", "scipy", "soundfile", "soxr")
        },
    }
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    root = Path(payload["output_dir"])
    root.mkdir(parents=True, exist_ok=True)
    target = root / key

    def result():
        manifest_path = target / "manifest.json"
        npz_path = target / "features.npz"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["config"] != config or _hash(npz_path) != manifest["npz_sha256"]:
            raise ValueError("Invalid rhythm feature cache")
        with np.load(npz_path, allow_pickle=False) as data:
            for name in ("times", "onset_envelope", "onset_times", "rms", "activity"):
                if not np.isfinite(data[name]).all():
                    raise ValueError("Invalid rhythm feature cache arrays")
        return {
            "manifest_path": str(manifest_path),
            "manifest_sha256": _hash(manifest_path),
            "npz_path": str(npz_path),
            "npz_sha256": manifest["npz_sha256"],
            "cache_key": key,
        }

    if target.exists():
        check()
        return result()
    stage = Path(tempfile.mkdtemp(prefix=".rhythm-", dir=root))
    try:
        progress(0.1)
        check()
        signal, sr = sf.read(str(audio), dtype="float32", always_2d=True)
        signal = signal.mean(axis=1)
        if not len(signal) or not np.isfinite(signal).all():
            raise ValueError("Invalid audio samples")
        if sr != 22050:
            signal = librosa.resample(signal, orig_sr=sr, target_sr=22050, res_type="soxr_hq")
        check()
        progress(0.4)
        onset = librosa.onset.onset_strength(
            y=signal, sr=22050, hop_length=441, n_fft=2048, center=True
        )
        rms = librosa.feature.rms(y=signal, frame_length=2048, hop_length=441, center=True)[0]
        count = min(len(onset), len(rms))
        onset, rms = onset[:count], rms[:count]
        times = np.arange(count, dtype=np.float64) * 441 / 22050
        peaks = librosa.onset.onset_detect(
            onset_envelope=onset, sr=22050, hop_length=441, backtrack=False
        )
        arrays = {
            "times": times,
            "onset_envelope": onset,
            "onset_times": times[peaks],
            "rms": rms,
            "activity": rms.copy(),
        }
        if not all(np.isfinite(a).all() for a in arrays.values()):
            raise ValueError("Non-finite rhythm features")
        check()
        progress(0.8)
        np.savez_compressed(stage / "features.npz", **arrays)
        manifest = {"config": config, "npz_sha256": _hash(stage / "features.npz")}
        (stage / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
        )
        check()
        try:
            os.rename(stage, target)
        except FileExistsError:
            pass
        progress(1.0)
        return result()
    finally:
        if stage.exists():
            shutil.rmtree(stage)
