import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path, PureWindowsPath

import soundfile as sf

from .audio_io import _validate_ogg_pages
from .domain import AudioAsset
from .project_store import _id, _json


def _safe(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Unsafe path")
    return path


def _source(root, name):
    p = Path(name)
    win = PureWindowsPath(name)
    if (
        p.is_absolute()
        or win.is_absolute()
        or win.root
        or win.drive
        or ".." in p.parts
        or ".." in win.parts
    ):
        raise ValueError("Unsafe path")
    return _safe(root / p)


def _digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _check(ogg, wav, audio):
    _validate_ogg_pages(ogg)
    if _digest(ogg) != audio.sha256:
        raise ValueError("Audio hash mismatch")
    info = sf.info(ogg)
    if (
        (info.format, info.subtype, info.samplerate, info.channels, info.frames)
        != ("OGG", "VORBIS", audio.sample_rate, audio.channels, audio.frames)
        or audio.subtype != "VORBIS"
        or abs(audio.duration_sec - info.frames / info.samplerate) > 1e-9
    ):
        raise ValueError("Audio metadata mismatch")
    decoded = sf.info(wav)
    if (decoded.format, decoded.subtype, decoded.samplerate, decoded.channels, decoded.frames) != (
        "WAV",
        "FLOAT",
        info.samplerate,
        info.channels,
        info.frames,
    ):
        raise ValueError("Decoded audio metadata mismatch")
    import numpy as np

    with sf.SoundFile(ogg) as expected, sf.SoundFile(wav) as actual:
        while True:
            left = expected.read(4096, dtype="float32", always_2d=True)
            right = actual.read(4096, dtype="float32", always_2d=True)
            if not np.array_equal(left, right):
                raise ValueError("Decoded audio samples mismatch")
            if len(left) < 4096:
                break


def _decoded_hash(path):
    return _digest(path)


def resolve_audio(project_dir, asset_id):
    asset_id = _id(asset_id)
    directory = _safe(Path(project_dir) / "audio" / asset_id)
    manifest = json.loads(_safe(directory / "manifest.json").read_text(encoding="utf-8"))
    audio = AudioAsset.model_validate(manifest["audio"])
    if audio.path != f"audio/{asset_id}/song.ogg":
        raise ValueError("Invalid asset path")
    _check(_safe(directory / "song.ogg"), _safe(directory / "decoded.wav"), audio)
    if manifest.get("decoded_sha256") != _decoded_hash(directory / "decoded.wav"):
        raise ValueError("Decoded audio hash mismatch")
    return audio


def publish_audio(project_dir, asset_id, staging_dir, audio):
    asset_id = _id(asset_id)
    project = _safe(project_dir)
    source_root = _safe(staging_dir)
    source = _source(source_root, audio.path)
    _safe(source.parent / "decoded.wav")
    audio_dir = _safe(project / "audio")
    audio_dir.mkdir(exist_ok=True)
    final = _safe(audio_dir / asset_id)
    result_audio = audio.model_copy(update={"path": f"audio/{asset_id}/song.ogg"})
    manifest = {"audio": result_audio.model_dump(mode="json")}
    temp = Path(tempfile.mkdtemp(prefix=".staging-", dir=audio_dir))
    try:
        shutil.copyfile(source, temp / "song.ogg")
        # Always derive canonical PCM from the copied final OGG.
        with (
            sf.SoundFile(temp / "song.ogg") as source_audio,
            sf.SoundFile(
                temp / "decoded.wav",
                mode="w",
                samplerate=sf.info(temp / "song.ogg").samplerate,
                channels=sf.info(temp / "song.ogg").channels,
                format="WAV",
                subtype="FLOAT",
            ) as decoded_audio,
        ):
            while True:
                samples = source_audio.read(4096, dtype="float32", always_2d=True)
                if not len(samples):
                    break
                decoded_audio.write(samples)
        _check(temp / "song.ogg", temp / "decoded.wav", audio)
        manifest["decoded_sha256"] = _decoded_hash(temp / "decoded.wav")
        (temp / "manifest.json").write_bytes(_json(manifest))
        try:
            os.rename(temp, final)
        except OSError:
            if final.exists():
                existing = resolve_audio(project, asset_id)
                if existing == result_audio and _digest(final / "decoded.wav") == _digest(
                    temp / "decoded.wav"
                ):
                    return manifest
                raise ValueError("Asset ID conflict")
            raise
        return manifest
    finally:
        shutil.rmtree(temp, ignore_errors=True)
