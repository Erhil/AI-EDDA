import threading
import time

from ai_edda.work_coordinator import WorkCoordinator


def _unknown_worker(payload, progress, cancel):
    return payload


def _unknown_publish(context, value, path):
    raise RuntimeError("unknown")


def _handlers_with_picklable_worker():
    return {
        "x": {
            "worker": _unknown_worker,
            "publish": _unknown_publish,
            "reconcile": _unknown_reconcile,
        }
    }


def _unknown_reconcile(job_id, context, result):
    raise RuntimeError("lookup unavailable")


def _handlers():
    return {
        "x": {
            "worker": lambda payload, path: payload,
            "publish": lambda context, value, path: value,
            "reconcile": lambda job_id, context, result: (False, None),
        }
    }


def test_close_waits_for_reservation_setup(tmp_path):
    coordinator = WorkCoordinator(tmp_path, _handlers_with_picklable_worker())
    entered, release = threading.Event(), threading.Event()
    original = coordinator._workspace

    def paused(job_id):
        entered.set()
        assert release.wait(3)
        return original(job_id)

    coordinator._workspace = paused
    reserve = threading.Thread(target=lambda: coordinator.reserve("x", {}))
    reserve.start()
    assert entered.wait(3)
    close = threading.Thread(target=coordinator.close)
    close.start()
    time.sleep(0.03)
    assert close.is_alive()
    release.set()
    reserve.join(3)
    close.join(3)
    assert not close.is_alive()


def test_close_waits_for_open_writer(tmp_path):
    coordinator = WorkCoordinator(tmp_path, _handlers())
    job_id = coordinator.reserve("x", {})
    entered, release = threading.Event(), threading.Event()

    def hold():
        with coordinator.writer(job_id):
            entered.set()
            release.wait(3)

    writer = threading.Thread(target=hold)
    writer.start()
    assert entered.wait(3)
    close = threading.Thread(target=coordinator.close)
    close.start()
    time.sleep(0.03)
    assert close.is_alive()
    release.set()
    writer.join(3)
    close.join(3)
    assert not close.is_alive()


def test_pending_unknown_survives_close(tmp_path):
    coordinator = WorkCoordinator(tmp_path, _handlers_with_picklable_worker())
    job_id = coordinator.reserve("x", {})
    coordinator.start(job_id, {"ok": True})
    deadline = time.time() + 3
    while coordinator.jobs.get(job_id)["state"] != "publication_pending" and time.time() < deadline:
        time.sleep(0.01)
    job = coordinator.jobs.get(job_id)
    assert job["state"] == "publication_pending"
    coordinator.close()
    pending = coordinator.jobs.get(job_id)
    assert pending["state"] == "publication_pending"
    assert pending["result"] == {"ok": True}
    assert pending["publication_error"] == "unknown"
