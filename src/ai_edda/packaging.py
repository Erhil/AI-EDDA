import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
from PIL import Image

from .domain import AudioAsset, Chart, SongMetadata, TimingGrid
from .serializer import serialize_difficulty, serialize_info


@dataclass(frozen=True)
class ExportSnapshot:
    metadata: SongMetadata
    grid: TimingGrid
    selected_charts: tuple[Chart, ...]
    audio: AudioAsset
    cover_path: str
    cover_sha256: str
    project_revision: int
    snapshot_id: str


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ArtifactBuilder:
    def __init__(self, project_root):
        self.root = Path(project_root).resolve()

    def _source(self, name):
        path = (self.root / name).resolve(strict=True)
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("asset outside project root")
        return path

    def build(self, snapshot: ExportSnapshot, destination, folder_name: str):
        if not re.fullmatch(r"[a-z]+", folder_name):
            raise ValueError("folder name must contain lowercase ASCII letters")
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        folder, archive, receipt_path = (
            destination / folder_name,
            destination / (folder_name + ".zip"),
            destination / (folder_name + ".receipt.json"),
        )
        if any(p.exists() for p in (folder, archive, receipt_path)):
            raise FileExistsError("destination already exists")
        audio = self._source(snapshot.audio.path)
        cover = self._source(snapshot.cover_path)
        if _hash(cover) != snapshot.cover_sha256:
            raise ValueError("cover hash mismatch")
        info = sf.info(audio)
        if (
            info.format != "OGG"
            or info.subtype != "VORBIS"
            or _hash(audio) != snapshot.audio.sha256
        ):
            raise ValueError("audio hash or codec mismatch")
        if (info.samplerate, info.channels, info.frames) != (
            snapshot.audio.sample_rate,
            snapshot.audio.channels,
            snapshot.audio.frames,
        ):
            raise ValueError("audio properties mismatch")
        if abs(snapshot.audio.duration_sec - info.frames / info.samplerate) > 1e-9:
            raise ValueError("audio duration mismatch")
        with Image.open(cover) as image:
            if image.format != "JPEG" or image.size != (512, 512):
                raise ValueError("cover must be 512x512 JPEG")
            image.load()
        if any(chart.grid_revision != snapshot.grid.revision for chart in snapshot.selected_charts):
            raise ValueError("stale chart grid revision")
        charts = sorted(
            snapshot.selected_charts,
            key=lambda c: c.metadata.rank if c.metadata.rank is not None else -1,
        )
        info_data = serialize_info(
            snapshot.metadata, snapshot.grid, snapshot.audio.duration_sec, charts
        )
        with tempfile.TemporaryDirectory(dir=destination) as temporary:
            stage = Path(temporary)
            game = stage / folder_name
            game.mkdir()

            def write_json(name, value):
                (game / name).write_text(
                    json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8"
                )

            write_json("info.dat", info_data)
            mapping = []
            for slot, chart in zip(("Easy", "Normal", "Hard"), charts):
                name = slot + ".dat"
                write_json(name, serialize_difficulty(chart, snapshot.grid))
                mapping.append({"chart_id": chart.id, "revision": chart.revision, "filename": name})
            shutil.copyfile(audio, game / "song.ogg")
            if _hash(game / "song.ogg") != snapshot.audio.sha256:
                raise ValueError("audio changed during export")
            shutil.copyfile(cover, game / "cover.jpg")
            staged_cover = game / "cover.jpg"
            if _hash(staged_cover) != snapshot.cover_sha256:
                raise ValueError("cover changed during export")
            with Image.open(staged_cover) as image:
                if image.format != "JPEG" or image.size != (512, 512):
                    raise ValueError("staged cover must be 512x512 JPEG")
                image.load()
            members = {
                p.name: {"size": p.stat().st_size, "sha256": _hash(p)} for p in game.iterdir()
            }
            zip_path = stage / "package.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for p in sorted(game.iterdir()):
                    z.write(p, p.name)
            with zipfile.ZipFile(zip_path) as z:
                if z.testzip() is not None:
                    raise ValueError("ZIP CRC failure")
                for name, entry in members.items():
                    digest = hashlib.sha256()
                    with z.open(name) as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                    if digest.hexdigest() != entry["sha256"]:
                        raise ValueError("ZIP content mismatch")
            site_errors = []
            if zip_path.stat().st_size > 15 * 1024 * 1024:
                site_errors.append("ZIP exceeds 15 MiB")
            if any(v["size"] > 50 * 1024 * 1024 for v in members.values()):
                site_errors.append("member exceeds 50 MiB")
            receipt = {
                "snapshot_id": snapshot.snapshot_id,
                "project_revision": snapshot.project_revision,
                "grid_revision": snapshot.grid.revision,
                "audio_sha256": snapshot.audio.sha256,
                "selection": mapping,
                "members": members,
                "profile": "ragnarock-ragnacustoms-v1",
                "local_check": "passed",
                "site_check": {"passed": not site_errors, "errors": site_errors},
            }
            staged_receipt = stage / "receipt.json"
            staged_receipt.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            published = []
            try:
                for source, target in (
                    (game, folder),
                    (zip_path, archive),
                    (staged_receipt, receipt_path),
                ):
                    if target.exists():
                        raise FileExistsError(target)
                    source.rename(target)
                    published.append(target)
            except BaseException:
                for target in reversed(published):
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                raise
        return receipt
