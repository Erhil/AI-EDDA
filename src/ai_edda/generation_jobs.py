import json
import uuid

from .arrangement_domain import ArrangementRequest
from .generation_catalog import publish_generation, reconcile_generation
from .generation_context import prepare_generation
from .generation_worker import generation_worker
from .project_store import _id


def make_generation_handlers(store):
    def publish(context, result, workspace):
        return publish_generation(
            store._dir(context["project_id"]), context["draft_id"], workspace.name, context, result
        )

    def reconcile(job_id, context, result):
        return reconcile_generation(
            store._dir(context["project_id"]), context["draft_id"], job_id, context, result
        )

    return {"generation": {"worker": generation_worker, "publish": publish, "reconcile": reconcile}}


def reserve_generation(coordinator, service, project_id, request):
    project_id = _id(project_id)
    request = ArrangementRequest.model_validate(
        request.model_dump() if isinstance(request, ArrangementRequest) else request
    )
    request_json = json.loads(json.dumps(request.model_dump(mode="json"), allow_nan=False))
    request_id = _id(request.id)
    with coordinator.jobs.lock:
        for job in coordinator.jobs.jobs.values():
            old = job.get("context") or {}
            if old.get("request_id") != request_id:
                continue
            if (
                job["kind"] != "generation"
                or old.get("project_id") != project_id
                or old.get("request") != request_json
            ):
                raise ValueError("REQUEST_ID_CONFLICT")
            if job["state"] not in ("interrupted", "cancelled", "failed") or (
                job["state"] == "interrupted" and "result" in job
            ):
                return {"job_id": job["id"], "draft_id": old["draft_id"]}
        with service._lock:
            snapshot = service.store.load(project_id)
            prepared = prepare_generation(service.store._dir(project_id), snapshot, request)
            context = {
                "project_id": project_id,
                "request_id": request_id,
                "draft_id": str(uuid.uuid4()),
                "request": request_json,
                "context": prepared.model_dump(mode="json"),
            }
            context = json.loads(json.dumps(context, allow_nan=False))
            job_id = coordinator.reserve("generation", context)
        try:
            coordinator.start(job_id, json.loads(json.dumps(context)))
        except BaseException:
            coordinator.cancel(job_id)
            raise
        return {"job_id": job_id, "draft_id": context["draft_id"]}
