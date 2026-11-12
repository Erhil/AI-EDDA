"""Prepare a private native project from a game archive."""

import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from .asset_catalog import _safe, publish_audio
from .cover_catalog import publish_prepared_cover
from .game_assets import import_game_folder
from .native_history import MAX_NODES, MAX_TOTAL_BYTES, _read, validate_native_history
from .native_media import validate_native_media
from .project_store import _json
from .safe_zip import extract_zip


def prepare_game_archive(source_zip, destination, cancel=None):
    def check():
        if cancel is not None:
            cancel()

    check()
    destination = _safe(Path(destination))
    destination.mkdir(mode=0o700)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extracted = root / "archive"
            names = extract_zip(source_zip, extracted, cancel=cancel)
            if any("/" in name for name in names) or any(p.is_dir() for p in extracted.iterdir()):
                raise ValueError("Archive must be flat")
            files = {name.casefold(): name for name in names}
            if "info.dat" not in files:
                raise ValueError("Missing info.dat")
            budget = [MAX_TOTAL_BYTES, MAX_NODES]
            info = _read(extracted / files["info.dat"], budget)
            difficulties = [
                entry["_beatmapFilename"]
                for group in info["_difficultyBeatmapSets"]
                for entry in group["_difficultyBeatmaps"]
            ]
            referenced = [
                "info.dat",
                info["_songFilename"],
                info["_coverImageFilename"],
                *difficulties,
            ]
            if any(
                not isinstance(name, str)
                or not name
                or ".." in name
                or any(c in name for c in "/\\:")
                for name in referenced
            ):
                raise ValueError("Invalid referenced filename")
            keys = [name.casefold() for name in referenced]
            if len(set(keys)) != len(keys) or set(keys) != set(files):
                raise ValueError("Archive files do not match references")
            for name in difficulties:
                _read(extracted / files[name.casefold()], budget)
            check()
            staging = root / "import"
            staging.mkdir()
            snapshot = import_game_folder(extracted, staging)
            check()
            audio_id, cover_id = str(uuid4()), str(uuid4())
            audio = publish_audio(destination, audio_id, staging, snapshot.project.audio)["audio"]
            cover_source = staging / snapshot.project.cover.path
            cover = snapshot.project.cover.model_copy(update={"path": "cover.jpg"})
            cover = publish_prepared_cover(destination, cover_id, cover_source.parent, cover)[
                "cover"
            ]
            raw_project = snapshot.project.model_dump(mode="json")
            raw_project.update(audio=audio, cover=cover)
            project = type(snapshot.project).model_validate(raw_project)
            (destination / "grid-0.json").write_bytes(_json(snapshot.grid.model_dump(mode="json")))
            for chart in snapshot.charts:
                (destination / f"chart-{chart.id}-0.json").write_bytes(
                    _json(chart.model_dump(mode="json"))
                )
            (destination / "project.json").write_bytes(
                _json({"project": project.model_dump(mode="json"), "receipt": None})
            )
            check()
            history = validate_native_history(destination, project.id, cancel=check)
            validate_native_media(destination, history, cancel=cancel)
            check()
            result = history.materialize_current()
            check()
            return result
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
