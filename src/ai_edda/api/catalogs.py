from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from ..arrangement_domain import ChartDraft, RhythmAnalysis
from ..asset_catalog import _safe
from ..generation_catalog import _hash
from ..native_history import MAX_NODES, MAX_TOTAL_BYTES, _read, _uuid
from ..project_store import _id, _json


def create_catalog_router(service, auth):
    router = APIRouter(prefix="/api")

    @router.get("/projects/{project_id}/artifacts")
    def listing(
        project_id: str,
        request: Request,
        kind: Literal["analysis", "draft"],
        after: str | None = None,
        limit: int = Query(20, ge=1, le=50),
    ):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")
        try:
            project_id = _id(project_id)
            if after is not None:
                _uuid(after)
        except ValueError:
            raise HTTPException(422, "Invalid request")
        try:
            with service._lock:
                service.store.load(project_id)
                root = service.store._dir(project_id)
            catalog = _safe(root / ("analysis" if kind == "analysis" else "generation"))
            if not catalog.exists():
                return {"kind": kind, "items": [], "next_cursor": None}
            names = []
            for path in catalog.iterdir():
                try:
                    _uuid(path.name)
                except ValueError:
                    continue
                if after is None or path.name > after:
                    names.append(path.name)
            names.sort()
            selected = names[:limit]
            budget = [MAX_TOTAL_BYTES, MAX_NODES]
            items = []
            for name in selected:
                try:
                    folder = _safe(catalog / name)
                    raw = _read(
                        folder / ("analysis.json" if kind == "analysis" else "draft.json"), budget
                    )
                    model = (RhythmAnalysis if kind == "analysis" else ChartDraft).model_validate(
                        raw
                    )
                    if _json(model.model_dump(mode="json")) != _json(raw) or model.id != name:
                        raise ValueError("Invalid artifact")
                    if kind == "analysis":
                        _uuid(model.audio_asset_id)
                        item = {
                            "id": name,
                            "audio_asset_id": model.audio_asset_id,
                            "audio_sha256": model.audio.sha256,
                            "revision": model.revision,
                        }
                    else:
                        req = model.generation_run.request
                        for value in (model.base_chart.id, model.chart.id, req.chart.id):
                            _uuid(value)
                        if (
                            model.chart.id != model.base_chart.id
                            or req.chart.id != model.base_chart.id
                        ):
                            raise ValueError("Invalid chart identity")
                        rejection = _safe(folder / "rejection.json")
                        rejected = rejection.exists()
                        if rejected:
                            record = _read(rejection, budget)
                            if (
                                not isinstance(record, dict)
                                or set(record) != {"draft_id", "decision_id", "draft_sha256"}
                                or record["draft_id"] != name
                                or record["draft_sha256"] != _hash(raw)
                            ):
                                raise ValueError("Invalid rejection")
                            _uuid(record["decision_id"])
                        item = {
                            "id": name,
                            "chart_id": model.chart.id,
                            "base_chart_revision": model.base_chart.revision,
                            "generator_id": req.generator_id,
                            "seed": req.seed,
                            "event_count": len(model.chart.events),
                            "rejected": rejected,
                        }
                    items.append(item)
                except (ValueError, TypeError, KeyError, OSError, AttributeError):
                    items.append({"id": name, "error": "Artifact unavailable"})
            return {
                "kind": kind,
                "items": items,
                "next_cursor": selected[-1] if len(names) > limit else None,
            }
        except (FileNotFoundError, KeyError):
            raise HTTPException(404, "Not found")
        except (ValueError, OSError, RuntimeError):
            raise HTTPException(503, "Storage unavailable")

    return router
