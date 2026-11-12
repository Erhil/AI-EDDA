import json

from ai_edda.work_coordinator import WorkCoordinator


def _persist_job(root, state="publication_pending", context=None, result=None):
    jobs = root / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job_id = "a" * 32
    record = {"id": job_id, "kind": "test", "state": state, "context": context}
    if result is not None:
        record["result"] = result
    (jobs / f"{job_id}.json").write_text(json.dumps(record), encoding="utf-8")
    return job_id


def test_pending_receipt_reconciles_when_workspace_is_missing(tmp_path):
    job_id = _persist_job(tmp_path, context={"key": "value"}, result={"answer": 42})
    calls = []
    handlers = {"test": {"reconcile": lambda *args: calls.append(args) or (True, {"receipt": 1})}}

    coordinator = WorkCoordinator(tmp_path, handlers)
    try:
        assert coordinator.jobs.get(job_id)["state"] == "completed"
        assert calls == [(job_id, {"key": "value"}, {"answer": 42})]
        assert coordinator.blocked is False
    finally:
        coordinator.close()


def test_unknown_workspace_is_preserved_and_blocks_admission(tmp_path):
    unknown = tmp_path / "workspaces" / ("b" * 32)
    unknown.mkdir(parents=True)
    coordinator = WorkCoordinator(tmp_path, {})
    try:
        assert unknown.exists()
        assert coordinator.blocked is True
    finally:
        coordinator.close()


def test_pending_job_without_context_blocks_recovery(tmp_path):
    job_id = _persist_job(tmp_path, context=None, result={"answer": 42})
    coordinator = WorkCoordinator(tmp_path, {"test": {"reconcile": lambda *args: (True, None)}})
    try:
        assert coordinator.jobs.get(job_id)["state"] == "publication_pending"
        assert coordinator.blocked is True
    finally:
        coordinator.close()
