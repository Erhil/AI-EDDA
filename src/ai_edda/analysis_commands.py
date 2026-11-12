from uuid import UUID

from .analysis_catalog import resolve_analysis
from .domain import Model, ObjectRef
from .project_store import ProjectSnapshot


class SelectAnalysis(Model):
    analysis_id: UUID


def apply(snapshot, payload, project_dir):
    request = SelectAnalysis.model_validate(payload)
    analysis, _ = resolve_analysis(project_dir, request.analysis_id)
    if snapshot.project.audio is None or analysis.audio != snapshot.project.audio:
        raise ValueError("ANALYSIS_AUDIO_MISMATCH")
    ref = ObjectRef(id=analysis.id, revision=analysis.revision)
    inputs = snapshot.project.plan_inputs if snapshot.project.analysis == ref else None
    return ProjectSnapshot(
        snapshot.project.model_copy(update={"analysis": ref, "plan_inputs": inputs}),
        snapshot.grid,
        snapshot.charts,
    )
