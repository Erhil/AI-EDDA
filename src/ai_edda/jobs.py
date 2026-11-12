import copy
import json
import multiprocessing as mp
import os
import queue
import threading
import time
import uuid
from pathlib import Path

import psutil


class BusyError(RuntimeError):
    pass


def _child(fn, payload, messages, cancel, gate):
    parent = mp.parent_process()

    def watchdog():
        while parent.is_alive():
            time.sleep(0.1)
        os._exit(1)

    threading.Thread(target=watchdog, daemon=True).start()
    gate.wait()
    try:
        result = fn(payload, lambda value: messages.put(("progress", value)), cancel)
        json.dumps(result)
        messages.put(("result", result))
    except BaseException as exc:
        messages.put(("error", str(exc)))


class JobManager:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.ctx = mp.get_context("spawn")
        self.current = None
        self.closed = False
        self.process = None
        self.cancel_event = None
        self.jobs = {}
        pending = []
        for path in self.root.glob("*.json"):
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["state"] == "publication_pending":
                self._recover(job)
                pending.append(job)
            elif job["state"] in ("reserved", "running", "cancelling", "ready"):
                self._recover(job)
                job["state"] = "interrupted"
                self.jobs[job["id"]] = job
                self._save(job)
            else:
                self.jobs[job["id"]] = job
        if len(pending) > 1:
            raise BusyError("multiple pending publications")
        for job in pending:
            self.jobs[job["id"]] = job
            self.current = job["id"]

    def _recover(self, job):
        pid, created = job.get("child_pid"), job.get("child_create_time")
        if pid is None or created is None:
            return
        try:
            child = psutil.Process(pid)
            if abs(child.create_time() - created) > 1e-3:
                return
        except psutil.NoSuchProcess:
            return
        try:
            parent = psutil.Process(job["parent_pid"])
            if (
                abs(parent.create_time() - job["parent_create_time"]) <= 1e-3
                and parent.is_running()
            ):
                raise BusyError("job manager owner is still alive")
        except psutil.NoSuchProcess:
            pass
        try:
            child.terminate()
            child.wait(timeout=3)
        except psutil.NoSuchProcess:
            return
        except psutil.TimeoutExpired as exc:
            raise BusyError("orphaned job child did not exit") from exc

    def _save(self, job):
        path = self.root / (job["id"] + ".json")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job), encoding="utf-8")
        os.replace(tmp, path)

    def reserve(self, kind, context=None):
        try:
            detached_context = json.loads(json.dumps(context, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("context must contain finite JSON values") from exc
        with self.lock:
            if self.closed:
                raise RuntimeError("job manager is closed")
            if self.current is not None:
                raise BusyError("BUSY")
            job = {
                "id": uuid.uuid4().hex,
                "kind": kind,
                "state": "reserved",
                "progress": None,
                "context": detached_context,
            }
            self.jobs[job["id"]] = job
            self.current = job["id"]
            try:
                self._save(job)
            except BaseException:
                self.current = None
                self.jobs.pop(job["id"], None)
                raise
            return job["id"]

    def start(self, job_id, fn, payload):
        with self.lock:
            job = self.jobs[job_id]
            if self.current != job_id or job["state"] != "reserved":
                raise ValueError("job is not reserved")
            messages = self.ctx.Queue()
            self.cancel_event = self.ctx.Event()
            gate = self.ctx.Event()
            process = self.ctx.Process(
                target=_child, args=(fn, payload, messages, self.cancel_event, gate)
            )
            process.start()
            self.process = process
            job["state"] = "running"
            try:
                child = psutil.Process(process.pid)
                job.update(
                    child_pid=process.pid,
                    child_create_time=child.create_time(),
                    parent_pid=os.getpid(),
                    parent_create_time=psutil.Process(os.getpid()).create_time(),
                )
                self._save(job)
            except BaseException:
                self.cancel_event.set()
                process.terminate()
                process.join()
                messages.close()
                self.process = None
                self.current = None
                job["state"] = "failed"
                raise
            threading.Thread(
                target=self._monitor, args=(job_id, process, messages), daemon=True
            ).start()
            gate.set()

    def _monitor(self, job_id, process, messages):
        result = None
        error = None
        storage_error = None
        while process.is_alive():
            try:
                kind, value = messages.get(timeout=0.05)
                if kind == "progress":
                    with self.lock:
                        self.jobs[job_id]["progress"] = value
                        try:
                            self._save(self.jobs[job_id])
                        except OSError as exc:
                            storage_error = str(exc)
                elif kind == "result":
                    result = value
                else:
                    error = value
            except queue.Empty:
                pass
        process.join()
        while True:
            try:
                kind, value = messages.get_nowait()
                if kind == "result":
                    result = value
                elif kind == "error":
                    error = value
                elif kind == "progress":
                    with self.lock:
                        self.jobs[job_id]["progress"] = value
            except queue.Empty:
                break
        messages.close()
        with self.lock:
            job = self.jobs[job_id]
            if job["state"] == "cancelled":
                pass
            elif job["state"] == "cancelling":
                job["state"] = "cancelled"
            elif storage_error is not None:
                job["state"] = "failed"
                job["error"] = f"progress persistence failed: {storage_error}"
            elif error is not None or result is None or process.exitcode != 0:
                job["state"] = "failed"
                job["error"] = error or "child exited without result"
            else:
                job["state"] = "ready"
                job["result"] = result
            if job["state"] != "ready" and self.current == job_id:
                self.current = None
            if self.process is process:
                self.process = None
            try:
                self._save(job)
            except OSError as exc:
                job["state"] = "failed"
                job.setdefault("error", f"job persistence failed: {exc}")
                if self.current == job_id:
                    self.current = None

    def get(self, job_id):
        with self.lock:
            return json.loads(json.dumps(self.jobs[job_id], allow_nan=False))

    def cancel(self, job_id):
        save_error = None
        process = None
        with self.lock:
            job = self.jobs[job_id]
            if self.current != job_id:
                return False
            if job["state"] == "publication_pending":
                return False
            if job["state"] in ("running", "cancelling"):
                if job["state"] == "running":
                    job["state"] = "cancelling"
                    self.cancel_event.set()
                    try:
                        self._save(job)
                    except OSError as exc:
                        save_error = exc
                process = self.process
            else:
                job["state"] = "cancelled"
                self.current = None
                try:
                    self._save(job)
                except OSError as exc:
                    save_error = exc
        if process is None:
            if save_error:
                raise save_error
            return True
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join()
        with self.lock:
            job = self.jobs[job_id]
            job["state"] = "cancelled"
            if self.current == job_id:
                self.current = None
            if self.process is process:
                self.process = None
            try:
                self._save(job)
            except OSError as exc:
                save_error = save_error or exc
        if save_error:
            raise save_error
        return True

    def finish(self, job_id, publish):
        with self.lock:
            job = self.jobs[job_id]
            if self.current != job_id or job["state"] != "ready":
                raise ValueError("job is not ready")
            pending = copy.deepcopy(job)
            pending["state"] = "publication_pending"
            self._save(pending)
            self.jobs[job_id] = job = pending
            try:
                value = publish(copy.deepcopy(job["result"]))
            except BaseException as exc:
                job["publication_error"] = str(exc)
                try:
                    self._save(job)
                except BaseException:
                    pass
                raise
            completed = copy.deepcopy(job)
            completed["state"] = "completed"
            completed["publication_result"] = copy.deepcopy(value)
            try:
                self._save(completed)
            except BaseException as exc:
                job["publication_error"] = str(exc)
                try:
                    self._save(job)
                except BaseException:
                    pass
                raise
            self.jobs[job_id] = completed
            self.current = None
            return value

    def fail_finalization(self, job_id, error):
        with self.lock:
            job = self.jobs[job_id]
            if self.current != job_id or job["state"] != "ready":
                raise ValueError("job is not ready")
            failed = copy.deepcopy(job)
            failed["state"] = "failed"
            failed["error"] = str(error)
            try:
                self._save(failed)
            except BaseException:
                pending = copy.deepcopy(job)
                pending["state"] = "publication_pending"
                pending["publication_error"] = str(error)
                self.jobs[job_id] = pending
                raise
            self.jobs[job_id] = failed
            self.current = None

    def resolve_publication(self, job_id, found, value=None, error=None):
        with self.lock:
            job = self.jobs[job_id]
            if job["state"] not in ("publication_pending", "interrupted") or "result" not in job:
                raise ValueError("job has no unresolved publication")
            if self.current not in (None, job_id):
                raise BusyError("BUSY")
            candidate = copy.deepcopy(job)
            # Claim the slot before saving a legacy interrupted record. If the
            # terminal save fails, it must remain protected like pending work.
            if self.current is None:
                self.current = job_id
            if job["state"] == "interrupted":
                job["state"] = "publication_pending"
            if found:
                candidate["state"] = "completed"
                candidate["publication_result"] = copy.deepcopy(value)
                candidate.pop("publication_error", None)
            else:
                candidate["state"] = "failed"
                candidate["error"] = str(
                    error or job.get("publication_error") or "publication was not found"
                )
            self._save(candidate)
            self.jobs[job_id] = candidate
            self.current = None
            return copy.deepcopy(candidate)

    def close(self):
        with self.lock:
            self.closed = True
            job_id = self.current
        if job_id is not None:
            self.cancel(job_id)
