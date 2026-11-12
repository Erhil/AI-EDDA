"""Resolve immutable musical plans from verified project inputs."""

import json
import math
import os
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

from .analysis_catalog import resolve_analysis
from .arrangement_domain import MusicalPlan
from .asset_catalog import _safe
from .domain import ObjectRef
from .musical_plan import ProjectionConfig, project_musical_plan
from .plan_inputs import resolve_plan_inputs
from .project_store import _id, _json


def _plan_id(identity, plan):
    content = {key: value for key, value in plan.items() if key != "id"}
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL, _json({"identity": identity, "plan": content}).decode("utf-8")
        )
    )


def load_plan(project_dir, ref):
    if isinstance(ref, dict) and (type(ref.get("revision")) is not int or ref["revision"] != 0):
        raise ValueError("Invalid plan reference revision")
    ref = ObjectRef.model_validate(ref)
    record_id = _id(ref.id)
    if ref.id != record_id or type(ref.revision) is not int or ref.revision != 0:
        raise ValueError("Invalid plan reference")
    raw = json.loads(_safe(Path(project_dir) / "plans" / (record_id + ".json")).read_bytes())
    if (
        not isinstance(raw, dict)
        or set(raw) != {"identity", "plan"}
        or not isinstance(raw["plan"], dict)
    ):
        raise ValueError("Invalid plan envelope")
    plan_data = raw["plan"]
    if (
        plan_data.get("id") != record_id
        or type(plan_data.get("revision")) is not int
        or plan_data["revision"] != 0
        or _plan_id(raw["identity"], plan_data) != record_id
    ):
        raise ValueError("Plan identity mismatch")
    return MusicalPlan.model_validate(plan_data)


def resolve_plan(project_dir, snapshot, config=ProjectionConfig()):
    ref = snapshot.project.analysis
    if ref is None:
        raise ValueError("Selected analysis required")
    analysis, bundle = resolve_analysis(project_dir, ref.id)
    if (
        ref.id != analysis.id
        or ref.revision != analysis.revision
        or snapshot.project.audio != analysis.audio
    ):
        raise ValueError("Selected analysis or audio mismatch")
    inputs = None
    if snapshot.project.plan_inputs is not None:
        inputs = resolve_plan_inputs(project_dir, snapshot.project.plan_inputs)
        if (
            inputs.analysis != ref
            or inputs.audio_asset_id != analysis.audio_asset_id
            or inputs.audio_sha256 != analysis.audio.sha256
        ):
            raise ValueError("Plan inputs mismatch")
        chart_ids = {chart.id for chart in snapshot.charts}
        for anchor in inputs.anchors:
            if (
                not math.isfinite(anchor.time_sec)
                or anchor.time_sec >= analysis.audio.duration_sec
                or not math.isfinite(anchor.tolerance_sec)
                or not set(anchor.target_scope) <= chart_ids
            ):
                raise ValueError("Invalid anchor scope or time")
    identity = {
        "algorithm": "musical-plan-projection-v1",
        "analysis": analysis.model_dump(mode="json"),
        "grid": snapshot.grid.model_dump(mode="json"),
        "config": asdict(config),
        "plan_inputs_ref": snapshot.project.plan_inputs.model_dump(mode="json") if inputs else None,
        "plan_inputs": inputs.model_dump(mode="json") if inputs else None,
    }
    beat = bundle["beat"]["arrays"]
    rhythm = bundle["rhythm"]["arrays"]
    plan = project_musical_plan(
        analysis,
        snapshot.grid,
        beat["beat_times"],
        beat["downbeat_times"],
        rhythm["times"],
        rhythm["onset_envelope"],
        rhythm["rms"],
        rhythm["onset_times"],
        config,
    )
    plan = plan.model_copy(update={"musical_anchors": inputs.anchors if inputs else ()})
    plan_id = _plan_id(identity, plan.model_dump(mode="json"))
    plan = plan.model_copy(update={"id": plan_id})
    payload = _json({"identity": identity, "plan": plan.model_dump(mode="json")})
    directory = _safe(Path(project_dir) / "plans")
    directory.mkdir(exist_ok=True)
    final = _safe(directory / (plan_id + ".json"))
    fd, name = tempfile.mkstemp(prefix=".staging-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, final)
        except FileExistsError:
            if _json(json.loads(_safe(final).read_bytes())) != payload:
                raise ValueError("Plan identity conflict")
    finally:
        os.unlink(name)
    return plan
