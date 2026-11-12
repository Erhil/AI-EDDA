import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

import soundfile as sf
from PIL import Image

from .audio_io import import_audio
from .domain import CoverAsset, ObjectRef, Provenance, SongProject
from .game_import import parse_game
from .project_store import ProjectSnapshot


def import_game_folder(source, private_destination):
    source, destination = Path(source), Path(private_destination)
    root = source.resolve(strict=True)
    if (
        source.is_symlink()
        or getattr(source.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
    ):
        raise ValueError("source is a link or reparse point")
    entries = {}
    for entry in source.iterdir():
        key = entry.name.casefold()
        if key in entries:
            raise ValueError("case-insensitive filename collision")
        entries[key] = entry

    def file(name):
        if not isinstance(name, str) or not name or ".." in name or any(c in name for c in "/\\:"):
            raise ValueError("invalid filename")
        path = entries.get(name.casefold())
        if path is None:
            raise FileNotFoundError(name)
        stat = path.stat(follow_symlinks=False)
        if (
            path.is_symlink()
            or getattr(stat, "st_file_attributes", 0) & 0x400
            or not path.is_file()
            or path.resolve(strict=True).parent != root
        ):
            raise ValueError("invalid source file")
        return path

    def read_json(path):
        return json.loads(path.read_text(encoding="utf-8-sig"))

    info = read_json(file("info.dat"))
    names = [
        e["_beatmapFilename"]
        for g in info["_difficultyBeatmapSets"]
        for e in g["_difficultyBeatmaps"]
    ]
    referenced = ["info.dat", info["_songFilename"], info["_coverImageFilename"], *names]
    if len({n.casefold() for n in referenced}) != len(referenced):
        raise ValueError("referenced filename collision")
    metadata, grid, charts = parse_game(info, {name: read_json(file(name)) for name in names})
    audio_source, cover_source = file(info["_songFilename"]), file(info["_coverImageFilename"])
    audio_info = sf.info(audio_source)
    if (audio_info.format, audio_info.subtype) != ("OGG", "VORBIS"):
        raise ValueError("audio must be Vorbis")
    with Image.open(cover_source) as image:
        if image.format != "JPEG" or image.size != (512, 512):
            raise ValueError("cover must be 512x512 JPEG")
        image.load()
    audio_dir = cover_dir = None
    try:
        audio = import_audio(audio_source, destination)
        audio_dir = (destination / audio.path).parent
        requested_cover_dir = destination / "cover" / str(uuid4())
        requested_cover_dir.mkdir(parents=True, exist_ok=False)
        cover_dir = requested_cover_dir
        cover_path = cover_dir / "cover.jpg"
        shutil.copyfile(cover_source, cover_path)
        digest = hashlib.sha256(cover_path.read_bytes()).hexdigest()
        cover = CoverAsset(
            path=cover_path.relative_to(destination).as_posix(),
            sha256=digest,
            width=512,
            height=512,
        )
        project = SongProject(
            metadata=metadata,
            audio=audio,
            cover=cover,
            grid_revision=grid.revision,
            chart_heads=tuple(ObjectRef(id=c.id, revision=c.revision) for c in charts),
            provenance=Provenance(created_by="imported"),
        )
        return ProjectSnapshot(project=project, grid=grid, charts=charts)
    except BaseException:
        for path in (audio_dir, cover_dir):
            if path is not None:
                shutil.rmtree(path, ignore_errors=True)
        raise
