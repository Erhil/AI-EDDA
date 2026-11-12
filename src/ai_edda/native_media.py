"""Validate every persisted media catalog before native import."""

import re
from pathlib import Path
from uuid import UUID

from .analysis_catalog import resolve_analysis
from .asset_catalog import _safe, resolve_audio
from .cover_catalog import resolve_cover
from .native_arrays import AnalysisBudget, preflight_analysis_npz
from .native_history import MAX_NODES, _read, _uuid

MAX_MEDIA_JSON_BYTES = 64 * 1024 * 1024
MAX_ANALYSIS_EXPANSION_BYTES = 256 * 1024 * 1024


def validate_native_media(project_dir, history, cancel=None):
    root = _safe(Path(project_dir))
    json_budget = [MAX_MEDIA_JSON_BYTES, MAX_NODES]
    arrays_budget = AnalysisBudget(MAX_ANALYSIS_EXPANSION_BYTES)
    audio, covers, analyses = {}, {}, {}

    def check():
        if cancel is not None:
            cancel()

    def entries(directory, expected):
        directory = _safe(directory)
        if not directory.is_dir() or {p.name for p in directory.iterdir()} != expected:
            raise ValueError("Invalid media catalog files")
        for name in expected:
            item = _safe(directory / name)
            if name not in ("beat", "rhythm") and not item.is_file():
                raise ValueError("Invalid media catalog file")

    def catalogs(name):
        directory = _safe(root / name)
        if not directory.exists():
            return ()
        if not directory.is_dir():
            raise ValueError("Invalid media catalog")
        result = list(directory.iterdir())
        for item in result:
            check()
            _safe(item)
            _uuid(item.name)
            if not item.is_dir():
                raise ValueError("Invalid media catalog entry")
        return result

    for directory in catalogs("audio"):
        entries(directory, {"manifest.json", "song.ogg", "decoded.wav"})
        value = _read(directory / "manifest.json", json_budget)
        if not isinstance(value, dict) or set(value) != {"audio", "decoded_sha256"}:
            raise ValueError("Invalid audio manifest")
        metadata = value["audio"]
        if not isinstance(metadata, dict) or any(
            type(metadata.get(key)) is not int for key in ("sample_rate", "channels", "frames")
        ):
            raise ValueError("Invalid audio manifest")
        audio[directory.name] = resolve_audio(root, directory.name)
        check()
    for directory in catalogs("assets"):
        entries(directory, {"manifest.json", "cover.jpg"})
        value = _read(directory / "manifest.json", json_budget)
        if not isinstance(value, dict) or set(value) not in (
            {"asset_id", "cover"},
            {"asset_id", "cover", "publication"},
        ):
            raise ValueError("Invalid cover manifest")
        if value["asset_id"] != directory.name:
            raise ValueError("Invalid cover identity")
        cover = value["cover"]
        if not isinstance(cover, dict) or any(
            type(cover.get(key)) is not int for key in ("width", "height")
        ):
            raise ValueError("Invalid cover manifest")
        if "publication" in value:
            publication = value["publication"]
            if not isinstance(publication, dict) or set(publication) != {
                "job_id",
                "context_sha256",
                "result_sha256",
            }:
                raise ValueError("Invalid cover publication")
            try:
                if str(UUID(publication["job_id"])) != publication["job_id"]:
                    raise ValueError("Invalid cover publication")
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Invalid cover publication")
            if any(
                not isinstance(publication[key], str)
                or re.fullmatch(r"[0-9a-f]{64}", publication[key]) is None
                for key in ("context_sha256", "result_sha256")
            ):
                raise ValueError("Invalid cover publication")
        covers[directory.name] = resolve_cover(root, directory.name)
        check()
    for directory in catalogs("analysis"):
        entries(
            directory, {"analysis.json", "descriptor.json", "publication.json", "beat", "rhythm"}
        )
        entries(directory / "beat", {"manifest.json", "beats.npz"})
        entries(directory / "rhythm", {"manifest.json", "features.npz"})
        values = [
            _read(directory / name, json_budget)
            for name in (
                "analysis.json",
                "descriptor.json",
                "publication.json",
                "beat/manifest.json",
                "rhythm/manifest.json",
            )
        ]
        raw = values[0]
        if (
            not isinstance(raw, dict)
            or raw.get("id") != directory.name
            or type(raw.get("revision")) is not int
            or raw["revision"] != 0
        ):
            raise ValueError("Invalid analysis identity")
        _uuid(raw.get("audio_asset_id"))
        if raw["audio_asset_id"] not in audio:
            raise ValueError("Missing analysis audio")
        descriptor, _, beat, rhythm = values[1:]

        def keys(value, expected):
            if not isinstance(value, dict) or set(value) != set(expected.split()):
                raise ValueError("Invalid analysis schema")

        def integers(value, names):
            if not isinstance(value, dict) or any(
                type(value.get(name)) is not int for name in names
            ):
                raise ValueError("Invalid analysis metadata")

        keys(descriptor, "audio_asset_id audio roles")
        keys(descriptor["roles"], "beat rhythm")
        for role in ("beat", "rhythm"):
            keys(
                descriptor["roles"][role],
                "extractor_version manifest_path manifest_sha256 npz_path npz_sha256",
            )
        keys(beat, "config source npz_sha256")
        keys(rhythm, "config npz_sha256")
        keys(
            beat["config"],
            "adapter_extractor_version audio_sha256 model_sha256 versions sample_rate hop_length n_fft mel_bands log_multiplier chunk_size border_size batch_size overlap_mode timing postprocessor device float16",
        )
        keys(
            rhythm["config"],
            "version audio_sha256 sample_rate hop_length n_fft center frame_time versions",
        )
        keys(beat["source"], "sample_rate channels decoded_frames duration_sec codec_path")
        integers(raw.get("audio"), ("sample_rate", "channels", "frames"))
        integers(descriptor["audio"], ("sample_rate", "channels", "frames"))
        integers(beat["source"], ("sample_rate", "channels", "decoded_frames"))
        source = beat["source"]
        verified_audio = audio[raw["audio_asset_id"]]
        if (
            source["sample_rate"] != verified_audio.sample_rate
            or source["channels"] != verified_audio.channels
            or source["decoded_frames"] != verified_audio.frames
            or type(source.get("duration_sec")) not in (int, float)
            or source["duration_sec"] != verified_audio.duration_sec
            or source.get("codec_path") != "SoundFile:OGG/VORBIS"
        ):
            raise ValueError("Invalid beat source audio")
        for role, filename in (("beat", "beats.npz"), ("rhythm", "features.npz")):
            preflight_analysis_npz(
                directory / role / filename,
                role,
                audio[raw["audio_asset_id"]],
                arrays_budget,
                cancel,
            )
        analyses[directory.name] = resolve_analysis(root, directory.name)[0]
        check()
    for snapshot in history.heads:
        project = snapshot.project
        for value, catalog, prefix, filename in (
            (project.audio, audio, "audio", "song.ogg"),
            (project.cover, covers, "assets", "cover.jpg"),
        ):
            if value is not None:
                parts = value.path.split("/")
                if (
                    len(parts) != 3
                    or parts[0] != prefix
                    or parts[2] != filename
                    or catalog.get(parts[1]) != value
                ):
                    raise ValueError("Missing historical media")
        if project.analysis is not None:
            analysis = analyses.get(project.analysis.id)
            if (
                analysis is None
                or analysis.revision != project.analysis.revision
                or analysis.audio != project.audio
            ):
                raise ValueError("Invalid historical analysis")
        check()
    return audio, covers, analyses
