import os
import threading
import time

import pytest

from ai_edda.jobs import BusyError, JobManager


def success(payload, progress, cancel):
    progress("working")
    return payload


def fail(payload, progress, cancel):
    raise RuntimeError("worker failed")


def crash(payload, progress, cancel):
    os._exit(7)


def cooperative(payload, progress, cancel):
    cancel.wait(5)
    return "stopped"


def stubborn(payload, progress, cancel):
    time.sleep(10)


def wait_for(manager, job, *states):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = manager.get(job)
        if value["state"] in states:
            return value
        time.sleep(0.02)
    pytest.fail(f"Timed out: {manager.get(job)}")


@pytest.fixture
def manager(tmp_path):
    instance = JobManager(tmp_path)
    yield instance
    instance.close()
    if instance.process:
        instance.process.join(3)


def test_slot_and_publication(manager):
    job = manager.reserve("test")
    with pytest.raises(BusyError):
        manager.reserve("other")
    manager.start(job, success, {"answer": 42})
    with pytest.raises(BusyError):
        manager.reserve("other")
    assert wait_for(manager, job, "ready")["result"] == {"answer": 42}
    with pytest.raises(BusyError):
        manager.reserve("other")
    assert manager.finish(job, lambda value: value["answer"]) == 42
    assert manager.get(job)["state"] == "completed"
    assert manager.reserve("next")


@pytest.mark.parametrize(
    "worker, message", [(fail, "worker failed"), (crash, "child exited without result")]
)
def test_worker_failure_releases_slot(manager, worker, message):
    job = manager.reserve("test")
    manager.start(job, worker, None)
    assert wait_for(manager, job, "failed")["error"] == message
    assert manager.reserve("next")


@pytest.mark.parametrize("worker", [cooperative, stubborn])
def test_cancel_running_job(manager, worker):
    job = manager.reserve("test")
    manager.start(job, worker, None)
    assert manager.cancel(job)
    wait_for(manager, job, "cancelled")
    assert manager.reserve("next")


def test_repeated_cancel_does_not_release_new_job(manager):
    job = manager.reserve("test")
    manager.start(job, stubborn, None)
    first = threading.Thread(target=manager.cancel, args=(job,))
    first.start()
    wait_for(manager, job, "cancelling")
    second = threading.Thread(target=manager.cancel, args=(job,))
    second.start()
    with pytest.raises(BusyError):
        manager.reserve("next")
    first.join(4)
    second.join(4)
    assert not first.is_alive()
    assert not second.is_alive()
    if manager.process:
        manager.process.join(2)
        assert not manager.process.is_alive()
    assert manager.reserve("next")


def test_close_rejects_reserve_and_joins_child(manager):
    job = manager.reserve("test")
    manager.start(job, stubborn, None)
    process = manager.process
    manager.close()
    assert not process.is_alive()
    with pytest.raises(RuntimeError, match="closed"):
        manager.reserve("next")


def test_close_waits_for_concurrent_cancel(manager):
    job = manager.reserve("test")
    manager.start(job, stubborn, None)
    process = manager.process
    cancel = threading.Thread(target=manager.cancel, args=(job,))
    cancel.start()
    wait_for(manager, job, "cancelling")
    manager.close()
    cancel.join(4)
    assert not cancel.is_alive()
    assert not process.is_alive()
    with pytest.raises(RuntimeError, match="closed"):
        manager.reserve("next")


def test_reserve_save_failure_allows_retry(manager, monkeypatch):
    original = manager._save
    calls = 0

    def fail_once(job):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected save failure")
        original(job)

    monkeypatch.setattr(manager, "_save", fail_once)
    with pytest.raises(OSError, match="injected"):
        manager.reserve("broken")
    assert manager.reserve("retry")


def test_start_save_failure_terminates_child_and_releases_slot(manager, monkeypatch):
    job = manager.reserve("test")
    original = manager._save

    def fail_running(value):
        if value["state"] == "running":
            raise OSError("injected save failure")
        original(value)

    monkeypatch.setattr(manager, "_save", fail_running)
    with pytest.raises(OSError, match="injected"):
        manager.start(job, stubborn, None)
    assert manager.process is None
    assert manager.reserve("retry")


def test_progress_save_failure_finalizes_and_releases_slot(manager, monkeypatch):
    job = manager.reserve("test")
    original = manager._save

    def fail_progress(value):
        if value.get("progress") == "working":
            raise OSError("progress-save-failure")
        original(value)

    monkeypatch.setattr(manager, "_save", fail_progress)
    manager.start(job, success, {"answer": 42})
    assert wait_for(manager, job, "failed")["error"].startswith("progress persistence failed:")
    assert manager.process is None
    assert manager.reserve("retry")


def test_cancel_save_failure_still_joins_and_releases_slot(manager, monkeypatch):
    job = manager.reserve("test")
    manager.start(job, stubborn, None)
    original = manager._save

    def fail_cancel(value):
        if value["state"] == "cancelling":
            raise OSError("cancel-save-failure")
        original(value)

    monkeypatch.setattr(manager, "_save", fail_cancel)
    with pytest.raises(OSError, match="cancel-save-failure"):
        manager.cancel(job)
    assert manager.process is None
    assert manager.get(job)["state"] == "cancelled"
    assert manager.reserve("retry")


@pytest.mark.parametrize("state", ["reserved", "ready"])
def test_cancel_save_failure_allows_retry(manager, monkeypatch, state):
    job = manager.reserve("test")
    if state == "ready":
        manager.start(job, success, "result")
        wait_for(manager, job, "ready")
    original = manager._save

    def fail_cancel(value):
        if value["state"] == "cancelled":
            raise OSError("cancel-save-failure")
        original(value)

    monkeypatch.setattr(manager, "_save", fail_cancel)
    with pytest.raises(OSError, match="cancel-save-failure"):
        manager.cancel(job)
    assert manager.current is None
    monkeypatch.setattr(manager, "_save", original)
    assert manager.reserve("retry")


@pytest.mark.parametrize("state", ["reserved", "ready"])
def test_restart_marks_unfinished_interrupted(tmp_path, state):
    manager = JobManager(tmp_path)
    job = manager.reserve("test")
    if state != "reserved":
        manager.start(job, success, "result")
    if state == "ready":
        wait_for(manager, job, "ready")
    restarted = JobManager(tmp_path)
    assert restarted.get(job)["state"] == "interrupted"
    assert restarted.reserve("next")
    manager.close()
    restarted.close()


def test_restart_with_live_owner_and_child_is_busy(tmp_path):
    manager = JobManager(tmp_path)
    job = manager.reserve("test")
    manager.start(job, stubborn, None)
    with pytest.raises(BusyError):
        JobManager(tmp_path)
    manager.close()
