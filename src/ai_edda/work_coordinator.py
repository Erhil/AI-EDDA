import json
import math
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .jobs import BusyError, JobManager


class WorkCoordinator:
    def __init__(self, root, handlers):
        self.root = Path(root)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir(parents=True, exist_ok=True)
        self.jobs = JobManager(self.root / "jobs")
        self.handlers = handlers
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self._setup_inflight = 0
        self.writers = {}
        self._owned = set()
        self._writer_open = set()
        self._deleting = set()
        self._job_errors = {}
        self._unknown_workspace_errors = set()
        self.blocked = False
        self.stopping = threading.Event()
        self._recover()
        self.observer = threading.Thread(target=self._observe, daemon=True)
        self.observer.start()

    def _workspace(self, job_id):
        if uuid.UUID(job_id).hex != job_id:
            raise ValueError("invalid job ID")
        path = self.workspaces / job_id
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None and (
            path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 1024
        ):
            raise ValueError("workspace is a reparse point")
        if path.resolve().parent != self.workspaces.resolve():
            raise ValueError("workspace outside root")
        return path

    def _cleanup(self, job_id):
        with self.lock:
            self._writer_open.discard(job_id)
            if self.writers.get(job_id, 0) or job_id not in self._owned or job_id in self._deleting:
                return
            self._deleting.add(job_id)
        try:
            path = self._workspace(job_id)
            if path.exists():
                shutil.rmtree(path)
        except Exception as exc:
            self._set_blocked(job_id, str(exc))
            raise
        else:
            with self.lock:
                self._owned.discard(job_id)
                self._clear_error(job_id)
        finally:
            with self.lock:
                self._deleting.discard(job_id)

    def _recover(self):
        known = set()
        for job_id, job in list(self.jobs.jobs.items()):
            try:
                known.add(job_id)
                if self._workspace(job_id).exists():
                    self._owned.add(job_id)
                state = job["state"]
                if state in ("publication_pending", "interrupted") and "result" in job:
                    if job["kind"] not in self.handlers:
                        self._set_blocked(job_id, "missing context or handler")
                        continue
                    self._reconcile_pending(job_id, job)
                elif state in ("completed", "failed", "cancelled") or (
                    state == "interrupted" and "result" not in job
                ):
                    self._cleanup(job_id)
            except Exception:
                self._set_blocked(job_id, "recovery failed")
        for path in self.workspaces.iterdir():
            if path.name not in known:
                self._set_blocked(None, f"unknown workspace: {path.name}")

    def reserve(self, kind, context):
        if kind not in self.handlers:
            raise ValueError("unknown job kind")
        with self.lock:
            if self.blocked or self.stopping.is_set():
                raise BusyError("coordinator unavailable")
            self._setup_inflight += 1
        job_id = None
        try:
            job_id = self.jobs.reserve(kind, context=context)
            with self.lock:
                self._owned.add(job_id)
                self._writer_open.add(job_id)
            path = self._workspace(job_id)
            path.mkdir()
            (path / "context.json").write_text(json.dumps(context), encoding="utf-8")
        except BaseException:
            if job_id is not None:
                self.jobs.cancel(job_id)
                self._cleanup(job_id)
            raise
        finally:
            with self.condition:
                self._setup_inflight -= 1
                self.condition.notify_all()
        return job_id

    @contextmanager
    def writer(self, job_id):
        with self.lock:
            if (
                job_id not in self._owned
                or job_id not in self._writer_open
                or job_id in self._deleting
                or self.stopping.is_set()
            ):
                raise BusyError("writer admission closed")
            self.writers[job_id] = self.writers.get(job_id, 0) + 1
        try:
            path = self._workspace(job_id)
            yield path
        finally:
            with self.lock:
                self.writers[job_id] -= 1
                self.condition.notify_all()

    def start(self, job_id, payload):
        with self.lock:
            if self.stopping.is_set():
                raise BusyError("coordinator unavailable")
            self._writer_open.discard(job_id)
            if self.writers.get(job_id, 0):
                raise BusyError("workspace writers are active")
        job = self.jobs.get(job_id)
        try:
            self.jobs.start(job_id, self.handlers[job["kind"]]["worker"], payload)
        except BaseException:
            self.jobs.cancel(job_id)
            raise

    def get(self, job_id):
        job = self.jobs.get(job_id)
        with self.lock:
            error = self._job_errors.get(job_id)
        if error:
            job["coordinator_error"] = error
        return job

    def cancel(self, job_id):
        with self.lock:
            self._writer_open.discard(job_id)
        return self.jobs.cancel(job_id)

    def _observe(self):
        while not self.stopping.wait(0.05):
            with self.jobs.lock:
                ids = list(self.jobs.jobs)
            for job_id in ids:
                try:
                    job = self.jobs.get(job_id)
                    if job["state"] == "ready":
                        self._process_ready(job_id, job)
                    elif job["state"] in ("publication_pending", "interrupted") and "result" in job:
                        self._reconcile_pending(job_id, job)
                    if self.jobs.get(job_id)["state"] in ("completed", "failed", "cancelled"):
                        self._cleanup(job_id)
                except Exception as exc:
                    self._set_blocked(job_id, str(exc))

    def _process_ready(self, job_id, job):
        handler = self.handlers[job["kind"]]
        context = job.get("context")
        try:
            path = self._workspace(job_id)
            self.jobs.finish(job_id, lambda value: handler["publish"](context, value, path))
        except Exception as exc:
            current = self.jobs.get(job_id)
            if current["state"] == "publication_pending":
                self._reconcile_pending(job_id, current)
            elif current["state"] == "ready":
                try:
                    self.jobs.fail_finalization(job_id, str(exc))
                except Exception as exc:
                    self._set_blocked(job_id, str(exc))

    def _reconcile_pending(self, job_id, job):
        try:
            job = self.jobs.get(job_id)
            context = job.get("context")
            if context is None:
                path = self._workspace(job_id)
                legacy = path / "context.json"
                if not legacy.exists():
                    raise ValueError("missing recovery context")
                context = json.loads(legacy.read_text(encoding="utf-8"))
                if not isinstance(context, dict) or not self._finite_json(context):
                    raise ValueError("invalid recovery context")
                with self.jobs.lock:
                    stored = self.jobs.jobs[job_id]
                    candidate = dict(stored)
                    candidate["context"] = context
                    self.jobs._save(candidate)
                    stored["context"] = context
                job["context"] = context
            handler = self.handlers[job["kind"]]
            if handler is None:
                raise ValueError("missing recovery handler")
            result = job.get("result")
            found, value = handler["reconcile"](job_id, context, result)
            self.jobs.resolve_publication(
                job_id, found, value=value, error=job.get("publication_error")
            )
        except Exception as exc:
            self._set_blocked(job_id, str(exc))
        else:
            self._clear_error(job_id)

    @staticmethod
    def _finite_json(value):
        if value is None or isinstance(value, (str, bool, int)):
            return True
        if isinstance(value, float):
            return math.isfinite(value)
        if isinstance(value, list):
            return all(WorkCoordinator._finite_json(item) for item in value)
        if isinstance(value, dict):
            return all(
                isinstance(key, str) and WorkCoordinator._finite_json(item)
                for key, item in value.items()
            )
        return False

    def _set_blocked(self, job_id, error):
        with self.lock:
            if job_id is None:
                self._unknown_workspace_errors.add(error)
            else:
                self._job_errors[job_id] = error
            self.blocked = bool(self._job_errors or self._unknown_workspace_errors)

    def _clear_error(self, job_id):
        with self.lock:
            self._job_errors.pop(job_id, None)
            self.blocked = bool(self._job_errors or self._unknown_workspace_errors)

    def close(self):
        self.stopping.set()
        self.observer.join()
        with self.condition:
            self._writer_open.clear()
        with self.jobs.lock:
            ids = list(self.jobs.jobs)
        # Reconcile pending outcomes while the backing application store is live.
        for job_id in ids:
            try:
                job = self.jobs.get(job_id)
                if job["state"] in ("publication_pending", "interrupted") and "result" in job:
                    self._reconcile_pending(job_id, job)
            except Exception as exc:
                self._set_blocked(job_id, str(exc))
        self.jobs.close()
        with self.condition:
            while self._setup_inflight or any(self.writers.values()):
                self.condition.wait()
        with self.jobs.lock:
            ids = list(self.jobs.jobs)
        for job_id in ids:
            if self.jobs.get(job_id)["state"] in ("completed", "failed", "cancelled"):
                self._cleanup(job_id)
