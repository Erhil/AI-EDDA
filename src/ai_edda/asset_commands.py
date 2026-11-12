from uuid import UUID

from .asset_catalog import resolve_audio
from .cover_catalog import resolve_cover
from .domain import Model
from .project_store import ProjectSnapshot


class AudioCommand(Model):
    asset_id: UUID


def apply(snapshot, kind, payload, project_dir):
    request = AudioCommand.model_validate(payload)
    if kind == "set_cover":
        cover = resolve_cover(project_dir, request.asset_id)
        project = snapshot.project.model_copy(update={"cover": cover})
        return ProjectSnapshot(project, snapshot.grid, snapshot.charts)
    if kind == "attach_audio" and (
        snapshot.project.audio is not None or any(chart.events for chart in snapshot.charts)
    ):
        raise ValueError("ATTACH_AUDIO_REQUIRES_EMPTY_PROJECT")
    audio = resolve_audio(project_dir, request.asset_id)
    charts = tuple(chart.model_copy(update={"review": "needs_review"}) for chart in snapshot.charts)
    grid = snapshot.grid.model_copy(
        update={
            "review": "needs_review",
            "segments": tuple(
                s.model_copy(update={"review": "needs_review"}) for s in snapshot.grid.segments
            ),
        }
    )
    project = snapshot.project.model_copy(
        update={"audio": audio, "analysis": None, "plan_inputs": None}
    )
    return ProjectSnapshot(project, grid, charts)
