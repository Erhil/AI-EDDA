from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from ..analysis_catalog import resolve_analysis
from ..analysis_jobs import reserve_analysis
from ..jobs import BusyError
from ..project_store import _id


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    audio_asset_id: UUID
    audio_hash: str
    device: Literal["cpu", "cuda"]
    float16: StrictBool


def create_analysis_router(service, auth, leases, coordinator, checkpoint):
    router = APIRouter(prefix="/api")

    def authenticate(request):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")

    def error(exc):
        if isinstance(exc, BusyError):
            return HTTPException(409, "Coordinator busy")
        if isinstance(exc, (FileNotFoundError, KeyError)):
            return HTTPException(404, "Not found")
        if isinstance(exc, ValueError):
            return HTTPException(
                409 if str(exc) == "REQUEST_ID_CONFLICT" else 422, "Invalid request"
            )
        return HTTPException(503, "Storage unavailable")

    @router.post("/projects/{project_id}/analysis-jobs", status_code=202)
    def create(project_id: str, body: AnalysisRequest, request: Request):
        authenticate(request)
        try:
            project_id = _id(project_id)
            request_id, asset_id = str(body.request_id), str(body.audio_asset_id)
            with service._lock:
                snapshot = service.store.load(project_id)
                if not leases.require(
                    project_id,
                    request.headers.get("X-Tab-Id"),
                    request.headers.get("X-Writer-Lease"),
                ):
                    raise HTTPException(409, "Writer lease required")
                current = snapshot.project.audio
            audio = None
            with coordinator.jobs.lock:
                for job in coordinator.jobs.jobs.values():
                    old = job.get("context") or {}
                    if old.get("request_id") != request_id:
                        continue
                    if (
                        job["kind"] != "analysis"
                        or old.get("project_id") != project_id
                        or old.get("audio_asset_id") != asset_id
                        or old.get("audio", {}).get("sha256") != body.audio_hash
                        or old.get("device") != body.device
                        or old.get("float16") != body.float16
                        or old.get("checkpoint") != str(Path(checkpoint).resolve())
                    ):
                        raise ValueError("REQUEST_ID_CONFLICT")
                    audio = old["audio"]
                    break
            if audio is None:
                if current is None:
                    raise HTTPException(404, "Audio not found")
                if current.sha256 != body.audio_hash or current.path.split("/") != [
                    "audio",
                    asset_id,
                    "song.ogg",
                ]:
                    raise HTTPException(409, "Audio mismatch")
                audio = current.model_dump(mode="json")
            return reserve_analysis(
                coordinator,
                service.store,
                project_id,
                request_id,
                asset_id,
                audio,
                checkpoint,
                body.device,
                body.float16,
            )
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError, RuntimeError, BusyError) as exc:
            raise error(exc)

    @router.get("/projects/{project_id}/analysis/{analysis_id}")
    def get_analysis(project_id: str, analysis_id: str, request: Request):
        authenticate(request)
        try:
            with service._lock:
                snapshot = service.store.load(project_id)
                current = snapshot.project.audio
                root = service.store._dir(project_id)
            analysis, _ = resolve_analysis(root, analysis_id)
            return {
                **analysis.model_dump(mode="json"),
                "matches_current_audio": current == analysis.audio,
            }
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            raise error(exc)

    return router
