from fastapi import APIRouter, HTTPException, Request

from ..project_store import _id
from ..target_catalog import resolve_target


def create_target_router(service, auth):
    router = APIRouter(prefix="/api")

    @router.get("/projects/{project_id}/charts/{chart_id}/target")
    def target(project_id: str, chart_id: str, request: Request):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")
        try:
            _id(project_id)
            _id(chart_id)
            with service._lock:
                snapshot = service.store.load(project_id)
                chart = next((c for c in snapshot.charts if c.id == chart_id), None)
                if chart is None:
                    raise HTTPException(404, "Not found")
                ref = chart.target_profile
                try:
                    record = resolve_target(service.store._dir(project_id), ref) if ref else None
                except FileNotFoundError as exc:
                    raise ValueError("Missing target") from exc
            return {
                "project_id": snapshot.project.id,
                "project_revision": snapshot.project.revision,
                "chart_id": chart.id,
                "chart_revision": chart.revision,
                "target_ref": ref.model_dump(mode="json") if ref else None,
                "target": record.model_dump(mode="json") if record else None,
            }
        except (FileNotFoundError, KeyError):
            raise HTTPException(404, "Not found")
        except ValueError:
            raise HTTPException(422, "Invalid request")
        except (OSError, RuntimeError):
            raise HTTPException(503, "Storage unavailable")

    return router
