import threading

import pytest

from ai_edda.jobs import BusyError
from ai_edda.work_coordinator import WorkCoordinator


def _coordinator(tmp_path):
    return WorkCoordinator(
        tmp_path,
        {
            "test": {
                "worker": lambda *args: None,
                "publish": lambda *args: None,
                "reconcile": lambda *args: (False, None),
            }
        },
    )


def test_writer_and_cleanup_are_serialized(tmp_path):
    coordinator = _coordinator(tmp_path)
    job = coordinator.reserve("test", {})
    entered = threading.Event()
    release = threading.Event()

    def acquire():
        with coordinator.writer(job):
            entered.set()
            release.wait()

    thread = threading.Thread(target=acquire)
    thread.start()
    assert entered.wait(2)
    coordinator._cleanup(job)
    assert (coordinator.workspaces / job).exists()
    release.set()
    thread.join(2)
    coordinator._cleanup(job)
    assert not (coordinator.workspaces / job).exists()
    coordinator.close()


def test_writer_rejects_unknown_and_closed_job(tmp_path):
    coordinator = _coordinator(tmp_path)
    with pytest.raises(BusyError):
        with coordinator.writer("a" * 32):
            pass
    job = coordinator.reserve("test", {})
    coordinator.cancel(job)
    with pytest.raises(BusyError):
        with coordinator.writer(job):
            pass
    coordinator.close()


def test_start_rejects_active_writer(tmp_path):
    coordinator = _coordinator(tmp_path)
    job = coordinator.reserve("test", {})
    with coordinator.writer(job):
        with pytest.raises(BusyError):
            coordinator.start(job, {})
    coordinator.close()


def test_cancel_waits_for_real_open_file_before_cleanup(tmp_path):
    coordinator = _coordinator(tmp_path)
    job = coordinator.reserve("test", {})
    with coordinator.writer(job) as path:
        handle = (path / "upload").open("w")
        handle.write("open")
        coordinator.cancel(job)
        coordinator._cleanup(job)
        assert (path / "upload").exists()
    handle.close()
    coordinator._cleanup(job)
    assert not (path / "upload").exists()
    coordinator.close()
