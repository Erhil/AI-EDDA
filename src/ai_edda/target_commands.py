import uuid

from pydantic import ConfigDict

from .domain import Model, ObjectRef
from .project_store import ProjectSnapshot
from .target_catalog import _target, publish_target


class SetChartTarget(Model):
    model_config = ConfigDict(extra="forbid")
    chart_id: uuid.UUID
    target: dict | None


def apply(snapshot, payload, project_dir, *, publish=True):
    request = SetChartTarget.model_validate(payload)
    chart_id = str(request.chart_id)
    if not any(chart.id == chart_id for chart in snapshot.charts):
        raise ValueError("CHART_NOT_FOUND")
    ref = None
    if request.target is not None:
        target = _target(request.target)
        try:
            ref = (
                publish_target(project_dir, target)
                if publish
                else ObjectRef(id=target.id, revision=target.version)
            )
        except (OSError, ValueError) as exc:
            raise ValueError("INVALID_TARGET_CATALOG") from exc
    charts = tuple(
        chart.model_copy(update={"target_profile": ref, "review": "needs_review"})
        if chart.id == chart_id
        else chart
        for chart in snapshot.charts
    )
    return ProjectSnapshot(snapshot.project, snapshot.grid, charts)
