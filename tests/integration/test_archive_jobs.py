import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from ai_edda.archive_jobs import ImportInProgress, reserve_project_import, start_project_import

REQUEST = "11111111-1111-4111-8111-111111111111"


class Coordinator:
    def __init__(self, root):
        self.root = root
        self.jobs = SimpleNamespace(lock=threading.RLock(), jobs={})
        self.jobs.get = lambda job_id: self.jobs.jobs[job_id]
        self.cancelled = []

    def reserve(self, kind, context):
        job_id = str(len(self.jobs.jobs) + 1)
        self.jobs.jobs[job_id] = dict(id=job_id, kind=kind, context=context, state="reserved")
        return job_id

    @contextmanager
    def writer(self, job_id):
        yield self.root

    def start(self, job_id, payload):
        self.payload = payload

    def cancel(self, job_id):
        self.cancelled.append(job_id)


def test_admission_and_retry(tmp_path):
    c = Coordinator(tmp_path)
    args = (c, None, REQUEST, "native", "file.zip", 1)
    first = reserve_project_import(*args)
    with pytest.raises(ImportInProgress):
        reserve_project_import(*args)
    assert len(c.jobs.jobs) == 1
    c.jobs.jobs[first["job_id"]]["state"] = "failed"
    second = reserve_project_import(*args)
    assert second["upload_required"] and not second["verifying"]
    start_project_import(c, second["job_id"])
    assert c.payload["request_context"]["request_id"] == REQUEST
    c.jobs.jobs[second["job_id"]]["kind"] = "audio_import"
    with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
        reserve_project_import(*args)
