from pydantic import ConfigDict

from .analysis_catalog import resolve_analysis
from .asset_catalog import resolve_audio
from .domain import Model, ObjectRef
from .plan_inputs import (
    build_plan_inputs,
    publish_plan_inputs,
    remove_chart_scope,
    resolve_plan_inputs,
)
from .project_store import ProjectSnapshot


class SetMusicalAnchors(Model):
    model_config = ConfigDict(extra="forbid")
    anchors: list[dict]


def apply(snapshot, payload, project_dir, *, publish=True):
    request = SetMusicalAnchors.model_validate(payload)
    audio = snapshot.project.audio
    ref = snapshot.project.analysis
    if audio is None or ref is None:
        raise ValueError("ANALYSIS_AUDIO_MISMATCH")
    analysis, _ = resolve_analysis(project_dir, ref.id)
    if (
        analysis.revision != ref.revision
        or analysis.audio != audio
        or resolve_audio(project_dir, analysis.audio_asset_id) != audio
    ):
        raise ValueError("ANALYSIS_AUDIO_MISMATCH")
    record = build_plan_inputs(
        analysis.audio_asset_id, audio, ref, request.anchors, [c.id for c in snapshot.charts]
    )
    result_ref = (
        publish_plan_inputs(project_dir, record) if publish else ObjectRef(id=record.id, revision=0)
    )
    return ProjectSnapshot(
        snapshot.project.model_copy(update={"plan_inputs": result_ref}),
        snapshot.grid,
        snapshot.charts,
    )


def remove_deleted_chart_scopes(before, candidate, chart_id, project_dir, *, publish=True):
    ref = before.project.plan_inputs
    if ref is None:
        return candidate
    record = resolve_plan_inputs(project_dir, ref)
    record = remove_chart_scope(record, chart_id, [chart.id for chart in candidate.charts])
    if record.id == ref.id:
        return candidate
    result_ref = (
        publish_plan_inputs(project_dir, record) if publish else ObjectRef(id=record.id, revision=0)
    )
    return ProjectSnapshot(
        candidate.project.model_copy(update={"plan_inputs": result_ref}),
        candidate.grid,
        candidate.charts,
    )
