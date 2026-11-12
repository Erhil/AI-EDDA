import hashlib
import json
from pathlib import Path

from .asset_catalog import _source
from .cover_catalog import prepare_cover, publish_prepared_cover, resolve_cover
from .domain import CoverAsset
from .project_store import _id, _json


def _identity(job_id, context, result):
    return {
        "job_id": _id(job_id),
        "context_sha256": hashlib.sha256(_json(context)).hexdigest(),
        "result_sha256": hashlib.sha256(_json(result)).hexdigest(),
    }


def cover_import_worker(payload, progress, cancel):
    workspace = Path(payload["workspace"])
    source = _source(workspace, payload["upload"])
    if cancel.is_set():
        raise RuntimeError("cancelled")
    cover = prepare_cover(source, workspace)
    if cancel.is_set():
        raise RuntimeError("cancelled")
    return {"cover": cover.model_dump(mode="json"), "output": cover.path}


def make_cover_handlers(store):
    def publish(context, result, workspace):
        cover = CoverAsset.model_validate(result["cover"])
        if result["output"] != cover.path:
            raise ValueError("Invalid output path")
        _source(workspace, result["output"])
        manifest = publish_prepared_cover(
            store._dir(context["project_id"]),
            context["request_id"],
            workspace,
            cover,
            _identity(Path(workspace).name, context, result),
        )
        return {"asset_id": context["request_id"], "cover": manifest["cover"]}

    def reconcile(job_id, context, result):
        project = store._dir(context["project_id"])
        asset_id = context["request_id"]
        manifest_path = project / "assets" / asset_id / "manifest.json"
        if not manifest_path.exists():
            if manifest_path.parent.exists():
                raise ValueError("Invalid cover manifest")
            return False, None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("publication") != _identity(job_id, context, result):
            raise ValueError("Publication identity mismatch")
        parsed = CoverAsset.model_validate(result["cover"])
        if parsed.path != "cover.jpg" or result["output"] != "cover.jpg":
            raise ValueError("Published cover metadata mismatch")
        actual = resolve_cover(project, asset_id)
        expected = parsed.model_copy(update={"path": f"assets/{asset_id}/cover.jpg"})
        if actual != expected:
            raise ValueError("Published cover metadata mismatch")
        return True, {"asset_id": asset_id, "cover": actual.model_dump(mode="json")}

    return {
        "cover_import": {"worker": cover_import_worker, "publish": publish, "reconcile": reconcile}
    }


def reserve_cover(coordinator, store, project_id, request_id, file_name, file_size):
    project_id, request_id = _id(project_id), _id(request_id)
    if not store._dir(project_id).is_dir():
        raise FileNotFoundError("Project does not exist")
    if not isinstance(file_name, str) or not file_name:
        raise ValueError("Invalid file name")
    if (
        isinstance(file_size, bool)
        or not isinstance(file_size, int)
        or not 0 < file_size <= 50 * 1024**2
    ):
        raise ValueError("Invalid file size")
    context = {
        "project_id": project_id,
        "request_id": request_id,
        "file_name": file_name,
        "file_size": file_size,
    }
    with coordinator.jobs.lock:
        for job in coordinator.jobs.jobs.values():
            old = job.get("context") or {}
            if old.get("request_id") != request_id:
                continue
            if job["kind"] != "cover_import" or old != context:
                raise ValueError("REQUEST_ID_CONFLICT")
            if job["state"] not in ("interrupted", "cancelled", "failed") or (
                job["state"] == "interrupted" and "result" in job
            ):
                return {"job_id": job["id"], "upload_required": False}
        job_id = coordinator.reserve("cover_import", context)
        return {"job_id": job_id, "upload_required": True}
