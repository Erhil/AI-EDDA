import threading
import time
from collections import defaultdict, deque
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..application import CommandEnvelope
from ..validation import validate_chart


class Login(BaseModel):
    password: str


class CreateProject(BaseModel):
    request_id: str
    metadata: dict[str, Any]


class LeaseRequest(BaseModel):
    tab_id: str = Field(min_length=1)
    action: Literal["acquire", "heartbeat", "takeover"]
    lease_id: str | None = None


class Preview(BaseModel):
    kind: str
    payload: dict[str, Any]


def create_router(service, auth, leases):
    router = APIRouter(prefix="/api")
    attempts = defaultdict(deque)
    attempts_lock = threading.Lock()

    def require_auth(request: Request):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")
        return token

    def call(fn):
        try:
            return fn()
        except FileNotFoundError:
            raise HTTPException(404, "Project or chart not found")
        except KeyError:
            raise HTTPException(404, "Project or chart not found")
        except ValueError as exc:
            code = str(exc)
            if code in (
                "STALE_REVISION",
                "REQUEST_ID_CONFLICT",
                "COMMAND_ID_CONFLICT",
                "UNDO_CONFLICT",
                "HISTORY_EMPTY",
            ):
                raise HTTPException(409, code)
            raise HTTPException(422, "Invalid request")
        except (OSError, RuntimeError):
            raise HTTPException(503, "Storage unavailable")

    @router.post("/session")
    def login(body: Login, request: Request, response: Response):
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with attempts_lock:
            # Keep both the time window and the client table bounded.
            for key in list(attempts):
                while attempts[key] and attempts[key][0] <= now - 60:
                    attempts[key].popleft()
                if not attempts[key]:
                    del attempts[key]
            if len(attempts) >= 1024 and client not in attempts:
                raise HTTPException(429, "Too many login attempts")
            if len(attempts[client]) >= 5:
                raise HTTPException(429, "Too many login attempts")
            attempts[client].append(now)
        token = auth.login(body.password)
        if not token:
            raise HTTPException(401, "Invalid credentials")
        with attempts_lock:
            attempts.pop(client, None)
        response.set_cookie(
            "ai_edda_session",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=auth.session_ttl,
        )
        return {"authenticated": True}

    @router.get("/session")
    def session(token=Depends(require_auth)):
        return {"authenticated": True}

    @router.delete("/session")
    def logout(response: Response, token=Depends(require_auth)):
        auth.logout(token)
        response.delete_cookie("ai_edda_session", httponly=True, samesite="strict")
        return {"authenticated": False}

    @router.get("/projects")
    def projects(token=Depends(require_auth)):
        return call(service.list)

    @router.post("/projects")
    def create(body: CreateProject, token=Depends(require_auth)):
        return call(lambda: service.create_project(body.request_id, body.metadata))

    @router.get("/projects/{project_id}")
    def project(project_id: str, token=Depends(require_auth)):
        return call(lambda: service.get(project_id))

    def validation_result(view):
        audio = view.project.audio
        duration = audio.duration_sec if audio is not None else float("inf")
        diagnostics = [
            {"chart_id": chart.id, **diagnostic.model_dump(mode="json")}
            for chart in view.charts
            for diagnostic in validate_chart(chart, view.grid, duration)
        ]
        return {"view": view, "diagnostics": diagnostics}

    @router.get("/projects/{project_id}/validation")
    def validation(project_id: str, token=Depends(require_auth)):
        return call(lambda: validation_result(service.get(project_id)))

    @router.post("/projects/{project_id}/preview-validation")
    def preview_validation(project_id: str, body: Preview, token=Depends(require_auth)):
        return call(lambda: validation_result(service.preview(project_id, body.kind, body.payload)))

    @router.get("/projects/{project_id}/charts/{chart_id}")
    def chart(project_id: str, chart_id: str, token=Depends(require_auth)):
        view = call(lambda: service.get(project_id))
        result = next((c for c in view.charts if c.id == chart_id), None)
        if result is None:
            raise HTTPException(404, "Project or chart not found")
        return result

    @router.post("/projects/{project_id}/writer-lease")
    def lease(project_id: str, body: LeaseRequest, token=Depends(require_auth)):
        with service._lock:
            call(lambda: service.get(project_id))
            if body.action == "heartbeat":
                lease_id = (
                    body.lease_id
                    if leases.heartbeat(project_id, body.tab_id, body.lease_id)
                    else None
                )
            else:
                lease_id = leases.acquire(
                    project_id, body.tab_id, takeover=body.action == "takeover"
                )
            if not lease_id:
                raise HTTPException(409, "Writer lease unavailable")
            return {
                "lease_id": lease_id,
                "ttl": leases.ttl,
                "heartbeat_interval": leases.heartbeat_interval,
            }

    @router.post("/projects/{project_id}/commands")
    def command(
        project_id: str,
        body: CommandEnvelope,
        token=Depends(require_auth),
        x_tab_id: str | None = Header(None),
        x_writer_lease: str | None = Header(None),
    ):
        with service._lock:
            if not leases.require(project_id, x_tab_id, x_writer_lease):
                raise HTTPException(409, "Writer lease required")
            return call(lambda: service.execute(project_id, body, writer_session=x_writer_lease))

    @router.post("/projects/{project_id}/preview")
    def preview(project_id: str, body: Preview, token=Depends(require_auth)):
        return call(lambda: service.preview(project_id, body.kind, body.payload))

    return router
