import math

from fastapi import APIRouter, HTTPException, Request

from ..analysis_catalog import resolve_analysis
from ..grid_suggestions import get_grid_suggestions

# Bound every returned observation array to keep JSON responses predictable.
MAX_OBSERVATION_VALUES = 4096


def create_grid_router(service, auth):
    router = APIRouter(prefix="/api")

    def authenticate(request):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")

    def error(exc):
        if isinstance(exc, (FileNotFoundError, KeyError)):
            return HTTPException(404, "Not found")
        if isinstance(exc, ValueError):
            return HTTPException(422, "Invalid request")
        return HTTPException(503, "Storage unavailable")

    @router.get("/projects/{project_id}/grid-suggestions")
    def suggestions(project_id: str, request: Request):
        authenticate(request)
        try:
            with service._lock:
                snapshot = service.store.load(project_id)
                root = service.store._dir(project_id)
            return {
                "project_id": snapshot.project.id,
                "project_revision": snapshot.project.revision,
                "grid_revision": snapshot.grid.revision,
                "analysis": snapshot.project.analysis.model_dump(mode="json")
                if snapshot.project.analysis
                else None,
                "suggestions": get_grid_suggestions(root, snapshot),
            }
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            raise error(exc)

    @router.get("/projects/{project_id}/analysis/{analysis_id}/observations")
    def observations(
        project_id: str, analysis_id: str, start_sec: float, end_sec: float, request: Request
    ):
        authenticate(request)
        if not (
            math.isfinite(start_sec)
            and math.isfinite(end_sec)
            and 0 <= start_sec < end_sec
            and end_sec - start_sec <= 60
        ):
            raise HTTPException(422, "Invalid window")
        try:
            with service._lock:
                service.store.load(project_id)
                root = service.store._dir(project_id)
            analysis, bundle = resolve_analysis(root, analysis_id)
            duration = analysis.audio.duration_sec
            if start_sec >= duration:
                raise HTTPException(422, "Invalid window")
            end_sec = min(end_sec, duration)
            beat = bundle["beat"]["arrays"]
            rhythm = bundle["rhythm"]["arrays"]

            def window(values):
                selected = values[(values >= start_sec) & (values <= end_sec)]
                if len(selected) > MAX_OBSERVATION_VALUES:
                    raise HTTPException(422, "Observation response exceeds value limit")
                return selected.tolist()

            mask = (rhythm["times"] >= start_sec) & (rhythm["times"] <= end_sec)
            aligned = {
                name: rhythm[name][mask] for name in ("times", "onset_envelope", "rms", "activity")
            }
            if any(len(values) > MAX_OBSERVATION_VALUES for values in aligned.values()):
                raise HTTPException(422, "Observation response exceeds value limit")
            return {
                "analysis_id": analysis.id,
                "analysis_revision": analysis.revision,
                "audio_asset_id": analysis.audio_asset_id,
                "audio_hash": analysis.audio.sha256,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "beat_times": window(beat["beat_times"]),
                "downbeat_times": window(beat["downbeat_times"]),
                "onset_times": window(rhythm["onset_times"]),
                **{name: values.tolist() for name, values in aligned.items()},
            }
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            raise error(exc)

    return router
