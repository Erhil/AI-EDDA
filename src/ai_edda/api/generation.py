from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..arrangement import SingleLaneProvider
from ..arrangement_domain import ArrangementRequest
from ..generation_jobs import reserve_generation
from ..jobs import BusyError
from ..plan_catalog import resolve_plan
from ..project_store import _id


class RejectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: UUID


def create_generation_router(service, auth, leases, coordinator):
    router = APIRouter(prefix="/api")

    def authenticate(request):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")

    def lease(project_id, request):
        if not leases.require(
            project_id, request.headers.get("X-Tab-Id"), request.headers.get("X-Writer-Lease")
        ):
            raise HTTPException(409, "Writer lease required")

    def error(exc):
        if isinstance(exc, BusyError):
            return HTTPException(409, "Coordinator busy")
        if isinstance(exc, (FileNotFoundError, KeyError)):
            return HTTPException(404, "Not found")
        if isinstance(exc, ValueError):
            status = (
                409
                if str(exc) in ("STALE_REVISION", "REQUEST_ID_CONFLICT", "Draft rejection conflict")
                else 422
            )
            return HTTPException(status, str(exc))
        return HTTPException(503, "Storage unavailable")

    @router.get("/generation-capabilities")
    def capabilities(request: Request):
        authenticate(request)
        return SingleLaneProvider().describe().model_dump(mode="json")

    @router.get("/projects/{project_id}/musical-plan")
    def musical_plan(project_id: str, request: Request):
        authenticate(request)
        try:
            with service._lock:
                snapshot = service.store.load(_id(project_id))
                plan = resolve_plan(service.store._dir(project_id), snapshot)
            project = snapshot.project
            return {
                "project_id": project.id,
                "project_revision": project.revision,
                "grid_revision": snapshot.grid.revision,
                "analysis": project.analysis.model_dump(mode="json") if project.analysis else None,
                "plan_inputs": project.plan_inputs.model_dump(mode="json")
                if project.plan_inputs
                else None,
                "plan": plan.model_dump(mode="json"),
            }
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            raise error(exc)

    @router.post("/projects/{project_id}/generation-jobs", status_code=202)
    def create(project_id: str, body: ArrangementRequest, request: Request):
        authenticate(request)
        try:
            project_id = _id(project_id)
            with coordinator.jobs.lock:
                with service._lock:
                    lease(project_id, request)
                    return reserve_generation(coordinator, service, project_id, body)
        except (ValueError, KeyError, OSError, RuntimeError, BusyError) as exc:
            raise error(exc)

    @router.get("/projects/{project_id}/drafts/{draft_id}")
    def inspect(project_id: str, draft_id: str, request: Request):
        authenticate(request)
        try:
            return service.inspect_draft(project_id, draft_id)
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            raise error(exc)

    @router.post("/projects/{project_id}/drafts/{draft_id}/reject")
    def reject(project_id: str, draft_id: str, body: RejectionRequest, request: Request):
        authenticate(request)
        try:
            project_id = _id(project_id)
            with service._lock:
                lease(project_id, request)
                return service.reject_draft(project_id, draft_id, str(body.decision_id))
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            raise error(exc)

    return router
