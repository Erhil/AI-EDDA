import hashlib
import os
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import psutil
import pytest
import soundfile as sf

from ai_edda.analysis_catalog import resolve_analysis
from ai_edda.analysis_jobs import make_analysis_handlers, reserve_analysis
from ai_edda.application import ApplicationService
from ai_edda.asset_catalog import publish_audio
from ai_edda.domain import AudioAsset
from ai_edda.project_store import ProjectStore
from ai_edda.work_coordinator import WorkCoordinator

CHECKPOINT = Path(os.environ.get(
    'AI_EDDA_TEST_CHECKPOINT',
    str(Path.home() / 'AppData/Local/AI-EDDA/models/beat-this/final0.ckpt'),
))

def injected_worker(payload, progress, cancel):
    import torch
    from beat_this.inference import Audio2Frames
    original_init = Audio2Frames.__init__
    original_call = Audio2Frames.__call__
    def injected_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._test_inject_oom = kwargs.get('device') == 'cuda' or 'cuda' in args
    def injected(self, *args, **kwargs):
        if self._test_inject_oom:
            raise torch.cuda.OutOfMemoryError('CUDA out of memory: injected test')
        return original_call(self, *args, **kwargs)
    Audio2Frames.__init__ = injected_init
    Audio2Frames.__call__ = injected
    import ai_edda.analysis_worker as module
    return module.analysis_worker(payload, progress, cancel)

def wait(coordinator, job_id):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = coordinator.get(job_id)
        if job['state'] in ('failed', 'completed', 'cancelled'):
            return job
        time.sleep(.1)
    raise AssertionError('job timed out')

@pytest.mark.skipif(not CHECKPOINT.is_file(), reason="beat-this checkpoint is not installed")
def test_cuda_oom_then_explicit_cpu_retry(tmp_path):
    import torch
    if not torch.cuda.is_available():
        pytest.skip("CUDA is required for the injected OOM recovery test")

    source = tmp_path / 'source'
    source.mkdir()
    path = source / 'song.ogg'
    samples = (np.sin(2 * np.pi * 440 * np.arange(44100) / 22050) * .2).astype('float32')
    sf.write(path, samples, 22050, format='OGG', subtype='VORBIS')
    info = sf.info(path)
    audio = AudioAsset(path='song.ogg', sha256=hashlib.sha256(path.read_bytes()).hexdigest(), sample_rate=22050, channels=1, frames=info.frames, duration_sec=info.duration, subtype='VORBIS')
    with ProjectStore(tmp_path / 'store') as store:
        view = ApplicationService(store).create_project(str(uuid4()), {})
        root = store._dir(view.project.id)
        aid = str(uuid4())
        audio = AudioAsset.model_validate(publish_audio(root, aid, source, audio)['audio'])
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        handlers = make_analysis_handlers(store)
        handlers['analysis']['worker'] = injected_worker
        coordinator = WorkCoordinator(tmp_path / 'work', handlers)
        try:
            failed = reserve_analysis(coordinator, store, view.project.id, str(uuid4()), aid, audio.model_dump(), CHECKPOINT, 'cuda', False)
            job = wait(coordinator, failed['job_id'])
            assert job['state'] == 'failed' and job['error'] == 'CUDA out of memory during Beat This analysis', job
            assert coordinator.jobs.process is None and coordinator.jobs.current is None
            assert not psutil.pid_exists(job['child_pid'])
            assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
            retry = reserve_analysis(coordinator, store, view.project.id, str(uuid4()), aid, audio.model_dump(), CHECKPOINT, 'cpu', False)
            job = wait(coordinator, retry['job_id'])
            assert job['state'] == 'completed', job
            analysis, _ = resolve_analysis(root, retry['analysis_id'])
            assert analysis.id == retry['analysis_id']
            assert analysis.audio_asset_id == aid
            assert analysis.audio == audio
            assert analysis.revision == 0
        finally:
            coordinator.close()

