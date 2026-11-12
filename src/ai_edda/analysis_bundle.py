"""Verify extractor outputs before exposing their arrays to downstream code."""

import json

import numpy as np

from .asset_catalog import _digest, _safe, _source
from .beat_analysis import MODEL_SHA256
from .domain import AudioAsset

# One 50 Hz frame covers endpoint rounding and extractor boundary differences.
_TOL = 0.021


def load_analysis_bundle(
    root, descriptor, expected_audio: AudioAsset, expected_audio_asset_id: str
):
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("audio_asset_id") != expected_audio_asset_id
    ):
        raise ValueError("Audio asset ID mismatch")
    if AudioAsset.model_validate(descriptor.get("audio")) != expected_audio:
        raise ValueError("Audio metadata mismatch")
    roles = descriptor.get("roles")
    if not isinstance(roles, dict) or set(roles) != {"beat", "rhythm"}:
        raise ValueError("Invalid analysis roles")
    root = _safe(root)
    result = {}
    for role in ("beat", "rhythm"):
        record = roles[role]
        if (
            not isinstance(record, dict)
            or type(record.get("extractor_version")) is not int
            or record["extractor_version"] != 1
        ):
            raise ValueError("Unsupported extractor version")
        paths = {}
        for kind in ("manifest", "npz"):
            name = record.get(kind + "_path")
            if not isinstance(name, str) or not name:
                raise ValueError("Invalid path")
            paths[kind] = _source(root, name)
            if _digest(paths[kind]) != record.get(kind + "_sha256"):
                raise ValueError("Analysis hash mismatch")
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("npz_sha256") != record["npz_sha256"]:
            raise ValueError("Manifest hash mismatch")
        config = manifest.get("config")
        common = {"audio_sha256": expected_audio.sha256, "sample_rate": 22050, "hop_length": 441}
        required = (
            {
                "adapter_extractor_version": 1,
                "model_sha256": MODEL_SHA256,
                "n_fft": 1024,
                "mel_bands": 128,
                "log_multiplier": 1000,
                "chunk_size": 1500,
                "border_size": 6,
                "batch_size": 1,
                "overlap_mode": "keep_first",
                "timing": "frame/50",
                "postprocessor": "minimal",
            }
            if role == "beat"
            else {
                "version": 1,
                "n_fft": 2048,
                "center": True,
                "frame_time": "frame * hop_length / sample_rate",
            }
        )
        if not isinstance(config, dict) or any(
            config.get(k) != v or type(config.get(k)) is not type(v)
            for k, v in {**common, **required}.items()
        ):
            raise ValueError("Unsupported preprocessing")
        version_keys = (
            {"beat-this", "torch", "torchaudio", "numpy", "soundfile", "soxr"}
            if role == "beat"
            else {"numpy", "librosa", "scipy", "soundfile", "soxr"}
        )
        versions = config.get("versions")
        if (
            not isinstance(versions, dict)
            or not version_keys.issubset(versions)
            or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in versions.items())
        ):
            raise ValueError("Invalid versions")
        if role == "beat" and (
            config.get("device") not in ("cpu", "cuda")
            or type(config.get("float16")) is not bool
            or (config["device"] == "cpu" and config["float16"])
        ):
            raise ValueError("Invalid beat configuration")
        names = (
            ("beat_logits", "downbeat_logits", "beat_times", "downbeat_times")
            if role == "beat"
            else ("times", "onset_envelope", "onset_times", "rms", "activity")
        )
        with np.load(paths["npz"], allow_pickle=False) as data:
            arrays = {}
            for name in names:
                a = data[name]
                if a.ndim != 1 or a.dtype.kind not in "iuf" or not np.isfinite(a).all():
                    raise ValueError("Invalid analysis array")
                arrays[name] = a.copy()
        duration = expected_audio.duration_sec
        time_names = (
            ("beat_times", "downbeat_times") if role == "beat" else ("times", "onset_times")
        )
        for name in time_names:
            a = arrays[name]
            if np.any(a < 0) or np.any(a[1:] < a[:-1]) or np.any(a > duration + _TOL):
                raise ValueError("Invalid time coordinates")
        if role == "beat":
            count = len(arrays["beat_logits"])
            if (
                count != len(arrays["downbeat_logits"])
                or not count
                or abs((count - 1) / 50 - duration) > _TOL
            ):
                raise ValueError("Invalid beat frame grid")
        else:
            times = arrays["times"]
            count = len(times)
            if (
                not count
                or any(len(arrays[n]) != count for n in ("onset_envelope", "rms", "activity"))
                or not np.allclose(times, np.arange(count) / 50, rtol=0, atol=1e-7)
                or abs(times[-1] - duration) > _TOL
            ):
                raise ValueError("Invalid rhythm frame grid")
        result[role] = {"arrays": arrays, "manifest": manifest}
    return result
