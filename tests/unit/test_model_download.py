import hashlib
import io
import json
import os
import subprocess
import sys
import tracemalloc
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_edda import model_download as model

PINNED = (model._URL, model._SIZE, model._SHA256)


@pytest.fixture(autouse=True)
def spec(monkeypatch):
    monkeypatch.setattr(model, "_SIZE", 4)
    monkeypatch.setattr(model, "_SHA256", hashlib.sha256(b"test").hexdigest())


def test_download_and_reuse(tmp_path):
    with patch.object(model, "urlopen", return_value=io.BytesIO(b"test")) as request:
        path, status = model.ensure_model(tmp_path)
        assert status == "downloaded" and path.read_bytes() == b"test"
        assert model.ensure_model(tmp_path) == (path, "existing")
        request.assert_called_once_with(model._URL, timeout=30)


@pytest.mark.parametrize("data", [b"tes", b"tests", b"fail"])
def test_bad_download_leaves_no_file(tmp_path, data):
    with patch.object(model, "urlopen", return_value=io.BytesIO(data)):
        with pytest.raises(ValueError):
            model.ensure_model(tmp_path)
    assert not list((tmp_path / "models" / "beat-this").iterdir())


def test_existing_corrupt_is_preserved(tmp_path):
    path = tmp_path / "models" / "beat-this" / "final0.ckpt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fail")
    with pytest.raises(ValueError):
        model.ensure_model(tmp_path)
    assert path.read_bytes() == b"fail"


def test_cancel_and_timeout_cleanup(tmp_path):
    with patch.object(model, "urlopen", return_value=io.BytesIO(b"test")):
        with pytest.raises(InterruptedError):
            model.ensure_model(tmp_path, cancel=lambda: True)
    with patch.object(model, "urlopen", side_effect=TimeoutError):
        with pytest.raises(TimeoutError):
            model.ensure_model(tmp_path)
    assert not list((tmp_path / "models" / "beat-this").iterdir())


def test_concurrent_winner(tmp_path, monkeypatch):
    real_link = model.os.link

    def winner(source, target):
        target.write_bytes(b"test")
        real_link(source, target)

    monkeypatch.setattr(model.os, "link", winner)
    with patch.object(model, "urlopen", return_value=io.BytesIO(b"test")):
        path, status = model.ensure_model(tmp_path)
    assert status == "existing" and path.read_bytes() == b"test"


def test_constants_match_single_artifact_manifest():
    manifest = json.loads((Path(__file__).parents[2] / "MODEL_MANIFEST.json").read_text())
    assert PINNED == (manifest["url"], manifest["bytes"], manifest["sha256"])


def test_cancelled_before_start_never_opens_network(tmp_path):
    with patch.object(model, "urlopen", return_value=io.BytesIO(b"test")) as request:
        with pytest.raises(InterruptedError):
            model.ensure_model(tmp_path, cancel=lambda: True)
        request.assert_not_called()
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("*.ckpt"))


@pytest.mark.parametrize("partial", [False, True])
def test_cancel_after_payload_progress_cleans_owned_file(tmp_path, partial):
    received = []

    class Payload(io.BytesIO):
        def read(self, size):
            return super().read(min(size, 1) if partial else size)

    response = Payload(b"test")
    with patch.object(model, "urlopen", return_value=response):
        with pytest.raises(InterruptedError):
            model.ensure_model(
                tmp_path,
                progress=lambda size, total: received.append((size, total)),
                cancel=lambda: bool(received),
            )
    assert received == [(1 if partial else 4, 4)]
    assert response.closed
    assert not list((tmp_path / "models" / "beat-this").iterdir())


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_callback_failure_cleans_only_owned_file(tmp_path, failure):
    directory = tmp_path / "models" / "beat-this"
    directory.mkdir(parents=True)
    unrelated = directory / ".final0-unrelated.tmp"
    unrelated.write_bytes(b"keep me")

    def fail(size, total):
        raise failure("callback failed")

    with patch.object(model, "urlopen", return_value=io.BytesIO(b"test")):
        with pytest.raises(failure, match="callback failed"):
            model.ensure_model(tmp_path, progress=fail)
    assert list(directory.iterdir()) == [unrelated]
    assert unrelated.read_bytes() == b"keep me"


@pytest.mark.parametrize("reported", ["0", "1", "999999999", "invalid"])
def test_actual_payload_count_overrides_response_headers(tmp_path, reported):
    response = io.BytesIO(b"test")
    response.headers = {"Content-Length": reported}
    with patch.object(model, "urlopen", return_value=response):
        path, status = model.ensure_model(tmp_path)
    assert status == "downloaded" and path.read_bytes() == b"test"


def test_read_failure_closes_response_and_cleans_temp(tmp_path):
    class Broken(io.BytesIO):
        def read(self, size):
            if self.tell():
                raise OSError("connection failed during payload")
            return super().read(1)

    response = Broken(b"test")
    with patch.object(model, "urlopen", return_value=response):
        with pytest.raises(OSError, match="during payload"):
            model.ensure_model(tmp_path)
    assert response.closed
    assert not list((tmp_path / "models" / "beat-this").iterdir())


@pytest.mark.parametrize("failure_point", ["fsync", "link"])
def test_disk_failure_leaves_no_final_or_temporary_file(tmp_path, monkeypatch, failure_point):
    def fail(*args, **kwargs):
        raise OSError("injected disk failure")

    monkeypatch.setattr(model.os, failure_point, fail)
    with patch.object(model, "urlopen", return_value=io.BytesIO(b"test")):
        with pytest.raises(OSError, match="injected disk failure"):
            model.ensure_model(tmp_path)
    assert not list((tmp_path / "models" / "beat-this").iterdir())


def test_concurrent_corrupt_winner_is_preserved(tmp_path, monkeypatch):
    real_link = model.os.link

    def corrupt_winner(source, destination):
        destination.write_bytes(b"fail")
        real_link(source, destination)

    monkeypatch.setattr(model.os, "link", corrupt_winner)
    with patch.object(model, "urlopen", return_value=io.BytesIO(b"test")):
        with pytest.raises(ValueError):
            model.ensure_model(tmp_path)
    path = tmp_path / "models" / "beat-this" / "final0.ckpt"
    assert path.read_bytes() == b"fail"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
@pytest.mark.parametrize("ancestor", ["config", "model"])
def test_reparse_ancestry_is_rejected_before_network(tmp_path, ancestor):
    outside = tmp_path / "outside"
    outside.mkdir()
    config = tmp_path / "config"
    junction = config if ancestor == "config" else config / "models" / "beat-this"
    junction.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        with patch.object(model, "urlopen") as request:
            with pytest.raises(ValueError, match="Unsafe path"):
                model.ensure_model(config)
            request.assert_not_called()
        assert list(outside.iterdir()) == []
    finally:
        junction.rmdir()


def test_streaming_download_and_existing_verify_use_bounded_memory(tmp_path, monkeypatch):
    total = 12 * 1024 * 1024 + 17
    digest = hashlib.sha256()
    for _ in range(12):
        digest.update(b"x" * 1024 * 1024)
    digest.update(b"x" * 17)
    monkeypatch.setattr(model, "_SIZE", total)
    monkeypatch.setattr(model, "_SHA256", digest.hexdigest())

    class Generated(io.BytesIO):
        remaining = total
        reads = []

        def read(self, size):
            assert 0 < size <= 1024 * 1024
            self.reads.append(size)
            amount = min(size, self.remaining)
            self.remaining -= amount
            return b"x" * amount

    response = Generated()
    progress = []
    with patch.object(model, "urlopen", return_value=response) as request:
        tracemalloc.start()
        try:
            path, status = model.ensure_model(
                tmp_path, progress=lambda size, total: progress.append((size, total))
            )
            assert status == "downloaded"
            assert model.ensure_model(tmp_path) == (path, "existing")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        request.assert_called_once()
    assert peak < 6 * 1024 * 1024
    assert response.reads[-1] == 1
    assert progress[-1] == (total, total)
    assert all(a[0] < b[0] for a, b in zip(progress, progress[1:]))


def test_existing_real_pinned_checkpoint_is_read_only_and_offline(monkeypatch):
    config = Path.home() / "AppData" / "Local" / "AI-EDDA"
    path = config / "models" / "beat-this" / "final0.ckpt"
    if not path.is_file():
        pytest.skip("existing pinned checkpoint is unavailable")
    monkeypatch.setattr(model, "_SIZE", PINNED[1])
    monkeypatch.setattr(model, "_SHA256", PINNED[2])
    before = path.stat()
    with patch.object(model, "urlopen", side_effect=AssertionError("network is forbidden")):
        assert model.ensure_model(config) == (path, "existing")
    after = path.stat()
    assert after.st_size == before.st_size == 81058141
    assert after.st_mtime_ns == before.st_mtime_ns


def test_import_does_not_load_torch():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import ai_edda.model_download; assert 'torch' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
