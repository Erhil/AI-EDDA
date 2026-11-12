import hashlib
import json
import os
import shutil
import tempfile
from importlib import metadata
from pathlib import Path

MODEL_SHA256 = "8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331"


def _hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def analyze_beats(payload, progress, cancel):
    def check():
        if cancel.is_set():
            raise RuntimeError("Beat analysis cancelled")

    check()
    audio = Path(payload["canonical_ogg"])
    checkpoint = Path(payload["checkpoint"])
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Beat This model is missing: {checkpoint}")
    if _hash(checkpoint) != MODEL_SHA256:
        raise ValueError("Beat This checkpoint SHA-256 mismatch")
    audio_hash = _hash(audio)
    if audio_hash != payload["expected_audio_sha256"]:
        raise ValueError("Audio SHA-256 mismatch")
    device = payload.get("device", "cpu")
    half = payload.get("float16", False)
    if device not in ("cpu", "cuda") or not isinstance(half, bool):
        raise ValueError("Invalid device or precision")
    if device == "cpu" and half:
        raise ValueError("float16 is not supported on CPU")
    import numpy as np
    import soundfile as sf
    import torch
    from beat_this.inference import Audio2Frames, Postprocessor

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    info = sf.info(str(audio))
    if info.format != "OGG" or info.subtype != "VORBIS":
        raise ValueError("Audio must be OGG Vorbis")
    versions = {
        name: metadata.version(name)
        for name in ("beat-this", "torch", "torchaudio", "numpy", "soundfile", "soxr")
    }
    config = {
        "adapter_extractor_version": 1,
        "audio_sha256": audio_hash,
        "model_sha256": MODEL_SHA256,
        "versions": versions,
        "device": device,
        "float16": half,
        "sample_rate": 22050,
        "n_fft": 1024,
        "hop_length": 441,
        "mel_bands": 128,
        "log_multiplier": 1000,
        "chunk_size": 1500,
        "border_size": 6,
        "batch_size": 1,
        "overlap_mode": "keep_first",
        "timing": "frame/50",
        "postprocessor": "minimal",
    }
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    root = Path(payload["output_dir"])
    root.mkdir(parents=True, exist_ok=True)
    target = root / key

    def result():
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if manifest["config"] != config or _hash(target / "beats.npz") != manifest["npz_sha256"]:
            raise ValueError("Invalid Beat This cache")
        with np.load(target / "beats.npz", allow_pickle=False) as data:
            for name in ("beat_logits", "downbeat_logits", "beat_times", "downbeat_times"):
                if not np.isfinite(data[name]).all():
                    raise ValueError("Invalid Beat This cache arrays")
        return {
            "manifest_path": str(target / "manifest.json"),
            "manifest_sha256": _hash(target / "manifest.json"),
            "npz_path": str(target / "beats.npz"),
            "npz_sha256": manifest["npz_sha256"],
            "cache_key": key,
        }

    if target.exists():
        return result()
    stage = Path(tempfile.mkdtemp(prefix=".beat-", dir=root))
    try:
        progress(0.1)
        check()
        signal, sr = sf.read(str(audio), dtype="float32")
        source = {
            "sample_rate": sr,
            "channels": 1 if signal.ndim == 1 else signal.shape[1],
            "decoded_frames": len(signal),
            "duration_sec": len(signal) / sr,
            "codec_path": f"SoundFile:{info.format}/{info.subtype}",
        }
        model = Audio2Frames(checkpoint_path=str(checkpoint), device=device, float16=half)
        check()
        progress(0.3)
        beat, downbeat = model(signal, sr)
        check()
        progress(0.8)
        times, down_times = Postprocessor(type="minimal", fps=50)(beat, downbeat)
        arrays = {
            "beat_logits": beat.cpu().numpy(),
            "downbeat_logits": downbeat.cpu().numpy(),
            "beat_times": np.asarray(times),
            "downbeat_times": np.asarray(down_times),
        }
        if not all(np.isfinite(a).all() for a in arrays.values()):
            raise ValueError("Non-finite model output")
        check()
        np.savez_compressed(stage / "beats.npz", **arrays)
        manifest = {"config": config, "source": source, "npz_sha256": _hash(stage / "beats.npz")}
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
    except torch.cuda.OutOfMemoryError as exc:
        raise RuntimeError("CUDA out of memory during Beat This analysis") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)
