import json
import uuid
from pathlib import Path

from .analysis_catalog import publish_analysis, reconcile_analysis
from .analysis_worker import analysis_worker
from .asset_catalog import _source, resolve_audio
from .beat_analysis import MODEL_SHA256, _hash
from .domain import AudioAsset
from .project_store import _id


def make_analysis_handlers(store):
    def publish(context, result, workspace):
        return publish_analysis(
            store._dir(context["project_id"]),
            context["analysis_id"],
            workspace.name,
            context,
            result,
            workspace,
        )

    def reconcile(job_id, context, result):
        return reconcile_analysis(
            store._dir(context["project_id"]), context["analysis_id"], job_id, context, result
        )

    return {"analysis": {"worker": analysis_worker, "publish": publish, "reconcile": reconcile}}


def reserve_analysis(
    coordinator, store, project_id, request_id, audio_asset_id, audio, checkpoint, device, float16
):
    project_id, request_id, audio_asset_id = map(_id, (project_id, request_id, audio_asset_id))
    project = store._dir(project_id)
    if not project.is_dir():
        raise FileNotFoundError("Project does not exist")
    if device not in ("cpu", "cuda") or type(float16) is not bool or (device == "cpu" and float16):
        raise ValueError("Invalid device or float16")
    checkpoint = Path(checkpoint)
    if not checkpoint.is_absolute():
        raise ValueError("Checkpoint path must be absolute")
    checkpoint = checkpoint.resolve()
    audio = AudioAsset.model_validate(audio)
    meaning = {
        "project_id": project_id,
        "request_id": request_id,
        "audio_asset_id": audio_asset_id,
        "audio": audio.model_dump(mode="json"),
        "checkpoint": str(checkpoint),
        "device": device,
        "float16": float16,
    }
    meaning = json.loads(json.dumps(meaning, allow_nan=False))
    with coordinator.jobs.lock:
        for job in coordinator.jobs.jobs.values():
            old = job.get("context") or {}
            if old.get("request_id") != request_id:
                continue
            if (
                job["kind"] != "analysis"
                or {k: v for k, v in old.items() if k != "analysis_id"} != meaning
            ):
                raise ValueError("REQUEST_ID_CONFLICT")
            if job["state"] not in ("interrupted", "cancelled", "failed"):
                return {"job_id": job["id"], "analysis_id": old["analysis_id"]}
        checkpoint = checkpoint.resolve(strict=True)
        if not checkpoint.is_file() or _hash(checkpoint) != MODEL_SHA256:
            raise ValueError("Invalid checkpoint identity")
        if resolve_audio(project, audio_asset_id) != audio:
            raise ValueError("Audio mismatch")
        meaning["checkpoint"] = str(checkpoint)
        context = {**meaning, "analysis_id": str(uuid.uuid4())}
        job_id = coordinator.reserve("analysis", context)
        try:
            with coordinator.writer(job_id) as workspace:
                payload = {
                    **meaning,
                    "workspace": str(workspace.resolve()),
                    "cache_root": str((coordinator.root / "analysis-cache").resolve()),
                    "canonical_ogg": str(_source(project, audio.path).resolve(strict=True)),
                }
            coordinator.start(job_id, payload)
        except BaseException:
            coordinator.cancel(job_id)
            raise
        return {"job_id": job_id, "analysis_id": context["analysis_id"]}
