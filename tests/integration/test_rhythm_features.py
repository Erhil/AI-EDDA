import hashlib
import threading

import numpy as np
import soundfile as sf

from ai_edda.audio_io import import_audio
from ai_edda.rhythm_features import analyze_features


def test_pulses_silence_and_cache(tmp_path):
    sr = 22050
    for silent in (False, True):
        signal = np.zeros(sr * 4, dtype=np.float32)
        if not silent:
            for t in (1, 2, 3):
                signal[t * sr : t * sr + 100] = np.hanning(200)[:100]
        source = tmp_path / ("silence.wav" if silent else "pulses.wav")
        sf.write(source, signal, sr)
        asset = import_audio(source, tmp_path)
        audio = tmp_path / asset.path
        before = hashlib.sha256(audio.read_bytes()).hexdigest()
        payload = {
            "canonical_ogg": str(audio),
            "expected_audio_sha256": before,
            "output_dir": str(tmp_path / "features"),
        }
        result = analyze_features(payload, lambda _: None, threading.Event())
        assert result == analyze_features(payload, lambda _: None, threading.Event())
        assert hashlib.sha256(audio.read_bytes()).hexdigest() == before
        with np.load(result["npz_path"]) as data:
            if silent:
                assert not np.any(data["activity"])
                assert len(data["onset_times"]) == 0
            else:
                for t in (1, 2, 3):
                    # Centered FFT plus onset differencing can delay a peak by two hops.
                    assert np.min(np.abs(data["onset_times"] - t)) <= 0.06
