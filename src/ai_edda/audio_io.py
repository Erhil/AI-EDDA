import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

import soundfile as sf

from .domain import AudioAsset


def _validate_ogg_pages(path: Path) -> None:
    """Check page boundaries and EOS markers without loading the file into memory."""
    size = path.stat().st_size
    ended = set()
    seen = set()
    with path.open("rb") as stream:
        offset = 0
        while offset < size:
            header = stream.read(27)
            if len(header) != 27 or header[:4] != b"OggS" or header[4] != 0:
                raise ValueError("Truncated or invalid OGG page header")
            segments = header[26]
            lacing = stream.read(segments)
            if len(lacing) != segments:
                raise ValueError("Truncated OGG lacing table")
            body_size = sum(lacing)
            body = stream.read(body_size)
            if len(body) != body_size:
                raise ValueError("Truncated OGG page body")
            serial = int.from_bytes(header[14:18], "little")
            seen.add(serial)
            if serial in ended:
                raise ValueError("OGG stream has pages after EOS")
            if header[5] & 0x04:
                ended.add(serial)
            offset += 27 + segments + body_size
    if not seen or ended != seen:
        raise ValueError("OGG stream has a serial without an EOS page")


def _validate_wav_chunks(path: Path) -> None:
    size = path.stat().st_size
    with path.open("rb") as stream:
        head = stream.read(12)
        if len(head) != 12 or head[:4] != b"RIFF" or head[8:] != b"WAVE":
            raise ValueError("Invalid RIFF/WAVE header")
        if int.from_bytes(head[4:8], "little") + 8 > size:
            raise ValueError("Truncated RIFF/WAVE container")
        end = int.from_bytes(head[4:8], "little") + 8
        offset = 12
        while offset < end:
            if end - offset < 8:
                raise ValueError("Truncated WAV chunk header")
            stream.seek(offset)
            chunk = stream.read(8)
            chunk_end = offset + 8 + int.from_bytes(chunk[4:8], "little")
            padded_end = chunk_end + (int.from_bytes(chunk[4:8], "little") & 1)
            if padded_end > end or padded_end > size:
                raise ValueError("Truncated WAV chunk")
            offset = padded_end
        if offset != end:
            raise ValueError("Invalid RIFF/WAVE chunk boundary")


def import_audio(source: Path, project_dir: Path) -> AudioAsset:
    """Import audio into a new asset directory and decode its final Vorbis stream."""
    source, project_dir = Path(source), Path(project_dir)
    info = sf.info(source)
    if not (
        (info.format == "OGG" and info.subtype == "VORBIS") or info.format in {"WAV", "FLAC", "MP3"}
    ):
        raise ValueError(f"Unsupported audio codec: {info.format}/{info.subtype}")
    if info.format == "OGG":
        _validate_ogg_pages(source)
    elif info.format == "WAV":
        _validate_wav_chunks(source)
    asset_dir = project_dir / "audio" / str(uuid4())
    asset_dir.mkdir(parents=True, exist_ok=False)
    ogg = asset_dir / "song.ogg"
    wav = asset_dir / "decoded.wav"
    try:
        if info.format == "OGG":
            shutil.copyfile(source, ogg)
        else:
            with (
                sf.SoundFile(source) as reader,
                sf.SoundFile(
                    ogg,
                    mode="w",
                    samplerate=info.samplerate,
                    channels=info.channels,
                    format="OGG",
                    subtype="VORBIS",
                ) as writer,
            ):
                for block in reader.blocks(blocksize=4096, dtype="float32", always_2d=True):
                    writer.write(block)
        with (
            sf.SoundFile(ogg) as reader,
            sf.SoundFile(
                wav,
                mode="w",
                samplerate=reader.samplerate,
                channels=reader.channels,
                format="WAV",
                subtype="FLOAT",
            ) as writer,
        ):
            rate, channels = reader.samplerate, reader.channels
            frames = 0
            for block in reader.blocks(blocksize=4096, dtype="float32", always_2d=True):
                writer.write(block)
                frames += len(block)
        if frames == 0:
            raise ValueError("Audio contains no frames")
        digest = hashlib.sha256()
        with ogg.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return AudioAsset(
            path=ogg.relative_to(project_dir).as_posix(),
            sha256=digest.hexdigest(),
            sample_rate=rate,
            channels=channels,
            frames=frames,
            duration_sec=frames / rate,
            subtype="VORBIS",
        )
    except BaseException:
        shutil.rmtree(asset_dir)
        raise
