import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


def _source(asset, root, audio_hash):
    root = Path(root).resolve()
    path = (root / asset.path).resolve()
    if not path.is_relative_to(root) or audio_hash != asset.sha256:
        raise ValueError("Invalid audio path or stale hash")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != audio_hash:
        raise ValueError("Stale audio hash")
    return path


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Bounds must be nonnegative integers")


def _positive_integer(value):
    _integer(value)
    if value == 0:
        raise ValueError("Count must be positive")


def _vorbis(path):
    with sf.SoundFile(path) as reader:
        if reader.format != "OGG" or reader.subtype != "VORBIS":
            raise ValueError("Audio source must be OGG/VORBIS")


def read_pcm_fragment(asset, root, audio_hash, start_sample, frame_count, *, decoded_path=None):
    _integer(start_sample)
    _positive_integer(frame_count)
    if frame_count > asset.sample_rate * 30:
        raise ValueError("Fragment exceeds 30 seconds")
    path = _source(asset, root, audio_hash)
    _vorbis(path)
    read_path = path if decoded_path is None else Path(decoded_path).resolve()
    with sf.SoundFile(read_path) as reader:
        if decoded_path is not None and (reader.format != "WAV" or reader.subtype != "FLOAT"):
            raise ValueError("Decoded audio must be WAV/FLOAT")
        if (reader.samplerate, reader.channels, reader.frames) != (
            asset.sample_rate,
            asset.channels,
            asset.frames,
        ):
            raise ValueError("Audio metadata mismatch")
        start = min(start_sample, reader.frames)
        reader.seek(start)
        data = reader.read(frame_count, dtype="float32", always_2d=True)
    return {
        "pcm": data.astype("<f4", copy=False).tobytes(),
        "source_start_sample": start,
        "sample_rate": asset.sample_rate,
        "channels": asset.channels,
        "frames": len(data),
        "hash": audio_hash,
    }


def build_waveform(asset, root, audio_hash):
    path = _source(asset, root, audio_hash)
    _vorbis(path)
    directory = Path(root) / "waveforms" / audio_hash
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            with np.load(directory / "peaks.npz", allow_pickle=False) as archive:
                valid = (
                    manifest["hash"] == audio_hash
                    and manifest["sample_rate"] == asset.sample_rate
                    and manifest["channels"] == asset.channels
                    and manifest["frames"] == asset.frames
                    and len(manifest["levels"]) > 0
                    and all(str(i) in archive for i in range(len(manifest["levels"])))
                )
            if valid:
                return manifest
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass
        # Never replace a published directory, even when it is incomplete.
        raise FileExistsError(f"Invalid waveform cache already exists: {directory}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{audio_hash}.", dir=directory.parent))
    try:
        return _build_waveform_files(asset, path, audio_hash, directory, temporary)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _build_waveform_files(asset, path, audio_hash, directory, temporary):
    base = 256
    peaks = []
    with sf.SoundFile(path) as reader:
        if (reader.samplerate, reader.channels, reader.frames) != (
            asset.sample_rate,
            asset.channels,
            asset.frames,
        ):
            raise ValueError("Audio metadata mismatch")
        for block in reader.blocks(blocksize=base, dtype="float32", always_2d=True):
            peaks.append(np.stack((block.min(axis=0), block.max(axis=0)), axis=-1))
    levels = [np.asarray(peaks, dtype=np.float32).reshape(-1, asset.channels, 2)]
    while len(levels[-1]) > 1:
        prev = levels[-1]
        pairs = []
        for i in range(0, len(prev), 2):
            chunk = prev[i : i + 2]
            pairs.append(
                np.stack((chunk[:, :, 0].min(axis=0), chunk[:, :, 1].max(axis=0)), axis=-1)
            )
        levels.append(np.asarray(pairs, dtype=np.float32))
    manifest = {
        "hash": audio_hash,
        "sample_rate": asset.sample_rate,
        "channels": asset.channels,
        "frames": asset.frames,
        "levels": [
            {"samples_per_peak": base * 2**i, "count": len(level)} for i, level in enumerate(levels)
        ],
    }
    np.savez_compressed(
        temporary / "peaks.npz", **{str(i): level for i, level in enumerate(levels)}
    )
    (temporary / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    temporary.rename(directory)
    return manifest


def read_waveform(asset, root, audio_hash, level, start, count):
    for value in (level, start):
        _integer(value)
    _positive_integer(count)
    path = _source(asset, root, audio_hash)
    _vorbis(path)
    directory = Path(root) / "waveforms" / audio_hash
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["hash"] != audio_hash
        or (manifest["sample_rate"], manifest["channels"], manifest["frames"])
        != (asset.sample_rate, asset.channels, asset.frames)
        or level >= len(manifest["levels"])
    ):
        raise ValueError("Invalid waveform cache or level")
    with np.load(directory / "peaks.npz", allow_pickle=False) as archive:
        peaks = archive[str(level)][start : start + count].copy()
    return {"manifest": manifest, "level": level, "start": start, "peaks": peaks}
