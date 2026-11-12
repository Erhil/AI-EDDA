import math

import pytest

from ai_edda.jobs import JobManager


def test_context_and_result_snapshots_are_detached(tmp_path):
    manager = JobManager(tmp_path)
    context = {"nested": {"items": [1, 2]}}
    job_id = manager.reserve("test", context)
    context["nested"]["items"].append(3)
    snapshot = manager.get(job_id)
    assert snapshot["context"] == {"nested": {"items": [1, 2]}}
    snapshot["context"]["nested"]["items"].append(4)
    assert manager.get(job_id)["context"] == {"nested": {"items": [1, 2]}}
    manager.jobs[job_id]["result"] = {"nested": {"items": [5]}}
    result_snapshot = manager.get(job_id)
    result_snapshot["result"]["nested"]["items"].append(6)
    assert manager.get(job_id)["result"] == {"nested": {"items": [5]}}
    manager.close()


@pytest.mark.parametrize("context", [{"bad": object()}, {"bad": math.inf}, {"bad": float("nan")}])
def test_invalid_context_is_rejected_before_admission(tmp_path, context):
    manager = JobManager(tmp_path)
    with pytest.raises(ValueError):
        manager.reserve("test", context)
    assert manager.current is None
    assert manager.jobs == {}
    assert list(tmp_path.glob("*.json")) == []
    assert manager.reserve("valid")
    manager.close()


def test_context_survives_restart(tmp_path):
    manager = JobManager(tmp_path)
    job_id = manager.reserve("test", {"request": {"id": 17}})
    restarted = JobManager(tmp_path)
    assert restarted.get(job_id)["context"] == {"request": {"id": 17}}
    manager.close()
    restarted.close()
