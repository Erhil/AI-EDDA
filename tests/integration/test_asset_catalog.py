from uuid import uuid4

import numpy as np
import pytest
import soundfile as sf

from ai_edda.asset_catalog import publish_audio, resolve_audio
from ai_edda.audio_io import import_audio


def test_publish_audio(tmp_path):
    source = tmp_path / "input.wav"
    sf.write(source, np.zeros(4096), 44100)
    staging = tmp_path / "staging"
    staging.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    audio = import_audio(source, staging)
    asset_id = str(uuid4())
    manifest = publish_audio(project, asset_id, staging, audio)
    assert publish_audio(project, asset_id, staging, audio) == manifest
    assert resolve_audio(project, asset_id).path == f"audio/{asset_id}/song.ogg"
    with pytest.raises(ValueError):
        publish_audio(project, str(uuid4()), staging, audio.model_copy(update={"sha256": "0" * 64}))
    with pytest.raises(ValueError):
        publish_audio(
            project, str(uuid4()), staging, audio.model_copy(update={"path": "../input.wav"})
        )
    other = audio.model_copy(update={"duration_sec": audio.duration_sec + 1})
    with pytest.raises(ValueError):
        publish_audio(project, asset_id, staging, other)
