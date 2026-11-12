import re
import shutil
from pathlib import Path

import soundfile as sf

from .asset_catalog import _safe, _source
from .beat_analysis import _hash, analyze_beats
from .domain import AudioAsset
from .rhythm_features import analyze_features


def analysis_worker(payload, progress, cancel):
    """Run both extractors in an isolated, parent-assigned workspace."""

    def check():
        if cancel.is_set():
            raise RuntimeError("Analysis cancelled")

    check()
    workspace = Path(payload["workspace"]).resolve()
    cache_root = Path(payload["cache_root"]).resolve() if payload.get("cache_root") else None
    audio_path = Path(payload["canonical_ogg"])
    if not audio_path.is_absolute():
        raise ValueError("Canonical audio path must be absolute")
    audio = AudioAsset.model_validate(payload["audio"])
    info = sf.info(str(audio_path))
    actual = (info.format, info.subtype, info.samplerate, info.channels, info.frames)
    expected = ("OGG", audio.subtype, audio.sample_rate, audio.channels, audio.frames)
    if actual != expected or abs(audio.duration_sec - info.duration) > 1e-6:
        raise ValueError("Audio metadata does not match canonical OGG")
    common = {"canonical_ogg": str(audio_path), "expected_audio_sha256": audio.sha256}
    last = 0.0

    def report(value):
        nonlocal last
        last = max(last, min(1.0, float(value)))
        progress(last)

    def record(result, role):
        output = {"extractor_version": 1}
        cache_key = result.get("cache_key")
        if not isinstance(cache_key, str) or re.fullmatch(r"[0-9a-f]{64}", cache_key) is None:
            raise ValueError("Invalid analysis cache key")
        for kind in ("manifest", "npz"):
            raw_path = Path(result[f"{kind}_path"])
            path = _safe(raw_path)
            if cache_root is not None:
                cache_role_root = _safe(cache_root / role)
                cache_entry = _safe(cache_role_root / cache_key)
                if not path.is_relative_to(cache_entry):
                    raise ValueError("Analysis output outside assigned cache entry")
                relative_path = path.relative_to(cache_entry)
                expected = _source(cache_entry, relative_path.as_posix())
                if path != expected:
                    raise ValueError("Analysis output outside assigned cache entry")
                if _hash(path) != result[f"{kind}_sha256"]:
                    raise ValueError("Analysis output hash mismatch")
                destination = workspace / role / cache_key / path.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
                path = destination.resolve()
            try:
                relative = path.relative_to(workspace)
            except ValueError as exc:
                raise ValueError("Analysis output outside workspace") from exc
            digest = _hash(path)
            if digest != result[f"{kind}_sha256"]:
                raise ValueError("Analysis output hash mismatch")
            output[f"{kind}_path"] = relative.as_posix()
            output[f"{kind}_sha256"] = digest
        return output

    beat = analyze_beats(
        {
            **common,
            "checkpoint": payload["checkpoint"],
            "device": payload["device"],
            "float16": payload["float16"],
            "output_dir": str((cache_root or workspace) / "beat"),
        },
        lambda value: report(value * 0.8),
        cancel,
    )
    check()
    beat_record = record(beat, "beat")
    report(0.8)
    rhythm = analyze_features(
        {**common, "output_dir": str((cache_root or workspace) / "rhythm")},
        lambda value: report(0.8 + value * 0.2),
        cancel,
    )
    check()
    rhythm_record = record(rhythm, "rhythm")
    check()
    report(1.0)
    check()
    return {
        "audio_asset_id": payload["audio_asset_id"],
        "audio": audio.model_dump(mode="json"),
        "roles": {"beat": beat_record, "rhythm": rhythm_record},
    }
