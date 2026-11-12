from pathlib import Path

from .asset_catalog import _source, publish_audio, resolve_audio
from .audio_io import import_audio
from .domain import AudioAsset
from .project_store import _id


def audio_import_worker(payload, progress, cancel):
    workspace = Path(payload["workspace"])
    source = _source(workspace, payload["upload"])
    if cancel.is_set():
        raise RuntimeError("cancelled")
    audio = import_audio(source, workspace)
    return {"audio": audio.model_dump(mode="json"), "output": audio.path}


def make_audio_handlers(store):
    def publish(context, result, workspace):
        audio = AudioAsset.model_validate(result["audio"])
        if result["output"] != audio.path:
            raise ValueError("Invalid output path")
        _source(workspace, result["output"])
        manifest = publish_audio(
            store._dir(context["project_id"]), context["request_id"], workspace, audio
        )
        return {"asset_id": context["request_id"], "audio": manifest["audio"]}

    def reconcile(job_id, context, result):
        project = store._dir(context["project_id"])
        asset_id = context["request_id"]
        manifest = project / "audio" / asset_id / "manifest.json"
        if not manifest.exists():
            return False, None
        actual = resolve_audio(project, asset_id)
        expected = AudioAsset.model_validate(result["audio"]).model_copy(
            update={"path": f"audio/{asset_id}/song.ogg"}
        )
        if actual != expected:
            raise ValueError("Published audio metadata mismatch")
        return True, {"asset_id": asset_id, "audio": actual.model_dump(mode="json")}

    return {
        "audio_import": {"worker": audio_import_worker, "publish": publish, "reconcile": reconcile}
    }


def reserve_audio(coordinator, store, project_id, request_id, file_name, file_size):
    project_id, request_id = _id(project_id), _id(request_id)
    if not store._dir(project_id).is_dir():
        raise FileNotFoundError("Project does not exist")
    if not isinstance(file_name, str) or not file_name:
        raise ValueError("Invalid file name")
    if (
        isinstance(file_size, bool)
        or not isinstance(file_size, int)
        or not 0 < file_size <= 1024**3
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
            if job["kind"] != "audio_import" or old != context:
                raise ValueError("REQUEST_ID_CONFLICT")
            if job["state"] not in ("interrupted", "cancelled", "failed"):
                return {"job_id": job["id"], "upload_required": False}
        job_id = coordinator.reserve("audio_import", context)
        return {"job_id": job_id, "upload_required": True}
