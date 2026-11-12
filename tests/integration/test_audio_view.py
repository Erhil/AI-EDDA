import numpy as np
import pytest
import soundfile as sf

from ai_edda.audio_io import import_audio
from ai_edda.audio_view import build_waveform, read_pcm_fragment, read_waveform


@pytest.mark.parametrize("rate", [44100, 48000])
def test_fragments_and_peaks(tmp_path, rate):
    samples = np.zeros((rate + 123, 2), dtype=np.float32)
    samples[1000, 0] = 0.9
    samples[2000, 1] = -0.8
    wav = tmp_path / "source.wav"
    sf.write(wav, samples, rate, subtype="FLOAT")
    asset = import_audio(wav, tmp_path)
    decoded, _ = sf.read(tmp_path / asset.path, dtype="float32", always_2d=True)
    for start, count in [(100, 3000), (len(decoded) - 5, 100)]:
        result = read_pcm_fragment(asset, tmp_path, asset.sha256, start, count)
        np.testing.assert_array_equal(
            np.frombuffer(result["pcm"], "<f4").reshape(-1, 2), decoded[start : start + count]
        )
    for start, count in [(-1, 1), (0, rate * 30 + 1), (1.5, 1)]:
        with pytest.raises(ValueError):
            read_pcm_fragment(asset, tmp_path, asset.sha256, start, count)
    with pytest.raises(ValueError):
        read_pcm_fragment(asset, tmp_path, "bad", 0, 1)
    manifest = build_waveform(asset, tmp_path, asset.sha256)
    result = read_waveform(asset, tmp_path, asset.sha256, 0, 0, 10)
    with pytest.raises(ValueError):
        read_waveform(asset, tmp_path, asset.sha256, 0, 0, 0)
    assert manifest["hash"] == asset.sha256
    for i, peak in enumerate(result["peaks"]):
        block = decoded[i * 256 : (i + 1) * 256]
        np.testing.assert_array_equal(peak[:, 0], block.min(axis=0))
        np.testing.assert_array_equal(peak[:, 1], block.max(axis=0))
