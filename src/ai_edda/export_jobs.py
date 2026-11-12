import json
import math
import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .game_export_catalog import publish_game_export, reconcile_game_export
from .game_export_worker import game_export_worker
from .native_capture import capture_inventory
from .native_export_catalog import publish_native_export, reconcile_native_export
from .native_export_worker import native_export_worker
from .project_store import _id
from .validation import validate_chart


def _canonical_uuid(value):
    if not isinstance(value, str) or _id(value) != value:
        raise ValueError("Expected canonical UUID")
    return value


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    kind: Literal["native", "game"]
    project_revision: int = Field(strict=True, ge=0)
    chart_ids: list[str] = Field(default_factory=list)
    folder_name: str | None = None

    @field_validator("request_id")
    @classmethod
    def request_uuid(cls, value):
        return _canonical_uuid(value)

    @field_validator("chart_ids")
    @classmethod
    def charts_unique(cls, value):
        for item in value:
            _canonical_uuid(item)
        if len(set(value)) != len(value):
            raise ValueError("Duplicate chart")
        return value

    @model_validator(mode="after")
    def selection(self):
        if self.kind == "native":
            if self.chart_ids or self.folder_name is not None:
                raise ValueError("Native export has no selection")
        elif (
            not 1 <= len(self.chart_ids) <= 3
            or not isinstance(self.folder_name, str)
            or not re.fullmatch(r"[a-z]+", self.folder_name)
        ):
            raise ValueError("Invalid game export selection")
        return self


def project_export_worker(payload, progress, cancel):
    worker = native_export_worker if payload["kind"] == "native" else game_export_worker
    return worker(payload["payload"], progress, cancel)


def make_export_handlers(store, config_root):
    def publish(context, result, workspace):
        target = config_root if context["kind"] == "native" else store._dir(context["project_id"])
        fn = publish_native_export if context["kind"] == "native" else publish_game_export
        return fn(
            target, context["export_id"], workspace.name, context, result, workspace / "prepared"
        )

    def reconcile(job_id, context, result):
        target = config_root if context["kind"] == "native" else store._dir(context["project_id"])
        fn = reconcile_native_export if context["kind"] == "native" else reconcile_game_export
        return fn(target, context["export_id"], job_id, context, result)

    return {
        "project_export": {
            "worker": project_export_worker,
            "publish": publish,
            "reconcile": reconcile,
        }
    }


def reserve_project_export(coordinator, service, project_id, request):
    project_id = _id(project_id)
    request = ExportRequest.model_validate(
        request.model_dump() if isinstance(request, ExportRequest) else request
    )
    data = request.model_dump(mode="json")
    with coordinator.jobs.lock:
        for job in coordinator.jobs.jobs.values():
            old = job.get("context") or {}
            if old.get("request_id") != request.request_id:
                continue
            try:
                stored_request = ExportRequest.model_validate({k: old[k] for k in data})
                stored_data = json.loads(stored_request.model_dump_json())
                exact_request = json.dumps(
                    stored_data,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                incoming_request = json.dumps(
                    data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
                )
            except (KeyError, TypeError, ValueError):
                exact_request = None
                incoming_request = ""
            if (
                job.get("kind") != "project_export"
                or old.get("project_id") != project_id
                or exact_request is None
                or exact_request != incoming_request
            ):
                raise ValueError("REQUEST_ID_CONFLICT")
            if job["state"] not in ("interrupted", "cancelled", "failed") or (
                job["state"] == "interrupted" and "result" in job
            ):
                return {"job_id": job["id"], "export_id": old["export_id"]}
        with service._lock, service.store._mutex:
            store = service.store
            snapshot = store.load(project_id)
            if snapshot.project.revision != request.project_revision:
                raise ValueError("STALE_REVISION")
            export_id = str(uuid.uuid4())
            context = json.loads(
                json.dumps(
                    {**data, "project_id": project_id, "export_id": export_id}, allow_nan=False
                )
            )
            project_dir = store._dir(project_id).resolve()
            if request.kind == "native":
                inventory = capture_inventory(
                    project_dir, project_id, (project_dir / "project.json").read_bytes()
                )
                payload = {"inventory": inventory, "project_revision": request.project_revision}
            else:
                project = snapshot.project
                if project.audio is None or project.cover is None:
                    raise ValueError("Missing media")
                charts = {chart.id: chart for chart in snapshot.charts}
                if any(cid not in charts for cid in request.chart_ids):
                    raise ValueError("Unknown chart")
                selected = [charts[cid] for cid in request.chart_ids]
                for chart in selected:
                    if (
                        chart.grid_revision != snapshot.grid.revision
                        or chart.metadata.rank is None
                        or not 1 <= chart.metadata.rank <= 10
                        or chart.metadata.speed is None
                        or not math.isfinite(chart.metadata.speed)
                        or chart.metadata.speed <= 0
                        or any(
                            d.severity == "error"
                            for d in validate_chart(
                                chart, snapshot.grid, project.audio.duration_sec
                            )
                        )
                    ):
                        raise ValueError("Invalid chart")
                payload = {
                    "project_dir": str(project_dir),
                    "folder_name": request.folder_name,
                    "snapshot": {
                        "metadata": project.metadata.model_dump(mode="json"),
                        "grid": snapshot.grid.model_dump(mode="json"),
                        "selected_charts": [c.model_dump(mode="json") for c in selected],
                        "audio": project.audio.model_dump(mode="json"),
                        "cover_path": project.cover.path,
                        "cover_sha256": project.cover.sha256,
                        "project_revision": project.revision,
                        "snapshot_id": export_id,
                    },
                }
            job_id = coordinator.reserve("project_export", context)
        try:
            with coordinator.writer(job_id) as workspace:
                payload["workspace"] = str(workspace.resolve())
            coordinator.start(job_id, {"kind": request.kind, "payload": payload})
        except BaseException:
            coordinator.cancel(job_id)
            raise
        return {"job_id": job_id, "export_id": export_id}
