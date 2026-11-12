"""Prepare generation inputs from the current project and verified catalogs."""

from .analysis_catalog import resolve_analysis
from .arrangement import SingleLaneProvider, validate_context
from .arrangement_domain import ArrangementContext, ArrangementRequest
from .plan_catalog import resolve_plan
from .project_store import _id
from .target_catalog import resolve_target


def prepare_generation(project_dir, snapshot, request):
    request = ArrangementRequest.model_validate(
        request.model_dump() if isinstance(request, ArrangementRequest) else request
    )
    if request.project_id != _id(request.project_id) or request.project_id != snapshot.project.id:
        raise ValueError("STALE_REVISION")
    chart = next((c for c in snapshot.charts if c.id == request.chart.id), None)
    if chart is None:
        raise ValueError("STALE_REVISION")
    if (
        chart.target_profile is not None
        and resolve_target(project_dir, chart.target_profile) != request.target
    ):
        raise ValueError("STALE_REVISION")
    capabilities = SingleLaneProvider().describe()
    if (
        request.generator_id != capabilities.backend_id
        or request.generator_version != capabilities.backend_version
        or request.mode not in capabilities.modes
    ):
        raise ValueError("unsupported generator or mode")
    if request.reference_chart is not None:
        raise ValueError("reference charts are unsupported")
    allowed = {"id", "version", "name", "intended_rank", *capabilities.target_fields}
    if any(
        value is not None
        for key, value in request.target.model_dump().items()
        if key not in allowed
    ):
        raise ValueError("unsupported target controls")
    divisions = request.target.allowed_subdivisions
    if divisions is not None and (not divisions or any(d not in (1, 2, 4) for d in divisions)):
        raise ValueError("unsupported subdivisions")
    grid = snapshot.grid
    if (
        grid.review != "approved"
        or any(s.review != "approved" for s in grid.segments)
        or snapshot.project.grid_revision != grid.revision
        or request.grid_revision != grid.revision
    ):
        raise ValueError("STALE_REVISION")
    ref = snapshot.project.analysis
    if ref is None or (ref.id, ref.revision) != (request.analysis.id, request.analysis.revision):
        raise ValueError("STALE_REVISION")
    analysis, _ = resolve_analysis(project_dir, ref.id)
    if snapshot.project.audio != analysis.audio:
        raise ValueError("STALE_REVISION")
    start, end = grid.time_at(request.range_q.start_q), grid.time_at(request.range_q.end_q)
    if (
        start < max(0, grid.segments[0].start_time_sec)
        or end > analysis.audio.duration_sec
        or end <= start
    ):
        raise ValueError("range outside audio")
    plan = resolve_plan(project_dir, snapshot)
    context = ArrangementContext(grid=grid, plan=plan, analysis=analysis, chart=chart)
    validate_context(request, context)
    return context
