import json

import pytest

from ai_edda.jobs import BusyError, JobManager


def test_pending_publication_survives_cancel_close_restart_and_resolves(tmp_path):
    manager = JobManager(tmp_path)
    job_id = manager.reserve("test")
    job = manager.jobs[job_id]
    job.update(state="publication_pending", result={"answer": 42})
    manager._save(job)

    assert manager.cancel(job_id) is False
    manager.close()
    assert manager.get(job_id)["state"] == "publication_pending"

    restarted = JobManager(tmp_path)
    assert restarted.current == job_id
    with pytest.raises(BusyError):
        restarted.reserve("next")
    resolved = restarted.resolve_publication(job_id, True, {"id": "published"})
    assert resolved["state"] == "completed"
    assert restarted.current is None
    restarted.close()


def test_unreadable_job_record_aborts_admission(tmp_path):
    (tmp_path / "broken.json").write_text("{bad json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        JobManager(tmp_path)
