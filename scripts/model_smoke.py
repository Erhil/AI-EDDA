import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf
import torch
from beat_this.inference import Audio2Frames
from beat_this.model.postprocessor import Postprocessor

from ai_edda.audio_io import import_audio

parser = argparse.ArgumentParser()
parser.add_argument("--require-cuda", action="store_true", help="fail if CUDA is unavailable")
args = parser.parse_args()

out = Path("docs/scratch/implement/model-smoke")
out.mkdir(parents=True, exist_ok=True)
checkpoint = Path(os.environ["LOCALAPPDATA"]) / "AI-EDDA/models/beat-this/final0.ckpt"
assert (
    hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    == "8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331"
)
sr = 44100
signal = np.zeros(sr * 30, dtype=np.float32)
for i in range(60):
    n = np.arange(int(sr * 0.08))
    signal[i * sr // 2 : i * sr // 2 + len(n)] += (
        0.7 * np.sin(2 * np.pi * (90 if i % 4 == 0 else 300) * n / sr) * np.exp(-n / (sr * 0.012))
    ).astype(np.float32)
labels = np.arange(60) / 2
np.save(out / "labels.npy", labels)
results = {"libsndfile": sf.__libsndfile_version__, "formats": sf.available_formats(), "codecs": {}}
for fmt, subtype, ext in [("WAV", "PCM_16", "wav"), ("FLAC", "PCM_16", "flac")]:
    path = out / ("synthetic." + ext)
    try:
        sf.write(path, signal, sr, format=fmt, subtype=subtype)
        decoded, rate = sf.read(path, dtype="float32")
        assert rate == sr and len(decoded) > 0
        results["codecs"][ext] = {"frames": len(decoded), "rate": rate}
    except Exception as e:
        results["codecs"][ext] = {"error": repr(e)}
source_wav = out / "synthetic.wav"
if "error" not in results["codecs"]["wav"]:
    project = out / "canonical-project"
    asset = import_audio(source_wav, project)
    ogg = project / asset.path
    signal, sr = sf.read(ogg, dtype="float32")
    results["canonical_ogg"] = {
        "path": str(ogg.resolve()),
        "sha256": asset.sha256,
        "frames": len(signal),
        "rate": sr,
    }
    for device in ("cpu", "cuda"):
        if device == "cuda" and not torch.cuda.is_available():
            results[device] = {"error": "CUDA unavailable"}
            continue
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        start = time.perf_counter()
        model = Audio2Frames(str(checkpoint), device=device)
        beat, downbeat = model(signal, sr)
        beats, downbeats = Postprocessor(type="minimal", fps=50)(beat, downbeat)
        if device == "cuda":
            torch.cuda.synchronize()
        assert torch.isfinite(beat).all() and torch.isfinite(downbeat).all()
        np.savez(
            out / f"{device}.npz",
            beat_logits=beat.cpu().numpy(),
            downbeat_logits=downbeat.cpu().numpy(),
            beats=beats,
            downbeats=downbeats,
        )
        results[device] = {
            "seconds": time.perf_counter() - start,
            "rss_bytes": psutil.Process().memory_info().rss,
            "cuda_peak_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else None,
            "frames": len(beat),
            "beats": len(beats),
            "downbeats": len(downbeats),
        }
        del model
(out / "results.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
if any("error" in codec for codec in results["codecs"].values()):
    raise SystemExit(1)
if args.require_cuda and "error" in results.get("cuda", {}):
    raise SystemExit(1)
