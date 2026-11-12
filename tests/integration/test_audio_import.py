import hashlib

import numpy as np
import pytest
import soundfile as sf

from ai_edda.audio_io import import_audio


@pytest.mark.parametrize(
    "format,subtype,options",
    [
        ("OGG", "VORBIS", {}),
        ("WAV", "PCM_16", {}),
        ("FLAC", "PCM_16", {}),
        ("MP3", "MPEG_LAYER_III", {"compression_level": 0.5, "bitrate_mode": "CONSTANT"}),
        ("MP3", "MPEG_LAYER_III", {"compression_level": 0.5, "bitrate_mode": "VARIABLE"}),
    ],
)
def test_import(tmp_path, format, subtype, options):
    source = tmp_path / "source"
    rate = 44100
    with sf.SoundFile(
        source, "w", samplerate=rate, channels=2, format=format, subtype=subtype, **options
    ) as writer:
        block = np.zeros((4096, 2), dtype="float32")
        remaining = rate * 30
        while remaining:
            n = min(remaining, len(block))
            writer.write(block[:n])
            remaining -= n
    project = tmp_path / "project"
    asset = import_audio(source, project)
    ogg = project / asset.path
    pcm = sf.info(ogg.parent / "decoded.wav")
    assert (pcm.samplerate, pcm.channels, pcm.frames, pcm.subtype) == (
        asset.sample_rate,
        asset.channels,
        asset.frames,
        "FLOAT",
    )
    assert asset.subtype == "VORBIS"
    assert asset.sample_rate == rate and asset.channels == 2
    assert asset.sha256 == hashlib.sha256(ogg.read_bytes()).hexdigest()
    if format == "OGG":
        assert source.read_bytes() == ogg.read_bytes()


def test_corrupt_input_does_not_remove_existing_asset(tmp_path):
    project = tmp_path / "project"
    existing = project / "audio" / "existing"
    existing.mkdir(parents=True)
    (existing / "song.ogg").write_bytes(b"keep")
    source = tmp_path / "broken"
    source.write_bytes(b"not audio")
    with pytest.raises(Exception):
        import_audio(source, project)
    assert list((project / "audio").iterdir()) == [existing]
    assert (existing / "song.ogg").read_bytes() == b"keep"


def test_rejects_ogg_without_last_eos_page(tmp_path):
    source = tmp_path / "source.ogg"
    with sf.SoundFile(
        source, "w", samplerate=44100, channels=2, format="OGG", subtype="VORBIS"
    ) as writer:
        writer.write(np.zeros((44100 * 2, 2), dtype="float32"))
    data = source.read_bytes()
    pages = []
    offset = 0
    while offset < len(data):
        segments = data[offset + 26]
        length = 27 + segments + sum(data[offset + 27 : offset + 27 + segments])
        pages.append((offset, length))
        offset += length
    assert len(pages) > 1 and data[pages[-1][0] + 5] & 0x04
    source.write_bytes(data[: pages[-1][0]])
    with pytest.raises(ValueError, match="EOS"):
        import_audio(source, tmp_path / "project")


def test_rejects_ogg_truncated_by_twenty_bytes(tmp_path):
    source = tmp_path / "source.ogg"
    with sf.SoundFile(
        source, "w", samplerate=44100, channels=2, format="OGG", subtype="VORBIS"
    ) as writer:
        writer.write(np.zeros((44100 * 2, 2), dtype="float32"))
    source.write_bytes(source.read_bytes()[:-20])
    with pytest.raises(ValueError):
        import_audio(source, tmp_path / "project")


@pytest.mark.parametrize("truncate", [0.5, -2])
def test_rejects_truncated_wav_without_publishing_asset(tmp_path, truncate):
    source = tmp_path / "truncated.wav"
    sf.write(source, np.zeros((4096, 2), dtype="float32"), 44100, subtype="PCM_16")
    data = source.read_bytes()
    source.write_bytes(data[: int(len(data) * truncate)] if truncate > 0 else data[:truncate])
    project = tmp_path / "project"
    with pytest.raises(ValueError):
        import_audio(source, project)
    assert not (project / "audio").exists()


@pytest.mark.parametrize("subtype", ["PCM_16", "FLOAT"])
def test_imports_wav_subtypes(tmp_path, subtype):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((2048, 2), dtype="float32"), 44100, subtype=subtype)
    asset = import_audio(source, tmp_path / "project")
    assert asset.frames > 0 and asset.channels == 2
