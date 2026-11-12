from typing import Any

from pydantic import Field, StrictInt

from . import advanced_commands, editing, grid_commands, project_editing, timing
from .domain import Chart, Model, TimeRef, TimingGrid
from .project_store import ProjectSnapshot


class CreateEvent(Model):
    chart_id: str
    time_ref: TimeRef
    lanes: tuple[StrictInt, ...] = Field(min_length=1)


class DeleteNotes(Model):
    chart_id: str
    note_ids: tuple[str, ...]


class MoveNotes(DeleteNotes):
    delta_lane: StrictInt
    time_mode: str | None = None
    time_delta: float = 0


class SongChanges(Model):
    title: str | None = None
    artist: str | None = None
    mapper: str | None = None
    subtitle: str | None = None
    explicit: bool | None = None


class ChartId(Model):
    chart_id: str


class CreateChart(Model):
    name: str = ""


class ChartChanges(ChartId):
    name: str | None = None
    rank: StrictInt | None = None
    speed: float | None = Field(default=None, strict=True, gt=0, allow_inf_nan=False)


class ApplyGrid(Model):
    grid: TimingGrid


class Reinterpret(Model):
    factor: float
    pivot_beat_q: float = 0


def dispatch(
    snapshot: ProjectSnapshot, kind: str, payload: dict[str, Any], operation_id: str
) -> ProjectSnapshot:
    types = {
        "create_event": CreateEvent,
        "delete_notes": DeleteNotes,
        "move_notes": MoveNotes,
        "update_song_metadata": SongChanges,
        "create_chart": CreateChart,
        "duplicate_chart": ChartId,
        "delete_chart": ChartId,
        "update_chart_metadata": ChartChanges,
        "apply_grid": ApplyGrid,
        "reinterpret_tempo": Reinterpret,
    }
    types.update(advanced_commands.TYPES)
    types.update(grid_commands.TYPES)
    if kind not in types:
        raise ValueError("UNKNOWN_COMMAND")
    p = types[kind].model_validate(payload)
    if kind in grid_commands.TYPES:
        return grid_commands.apply(snapshot, kind, p)
    if kind == "update_song_metadata":
        return project_editing.update_song_metadata(snapshot, **p.model_dump(exclude_unset=True))
    if kind == "create_chart":
        return project_editing.create_chart(snapshot, p.name)
    if kind == "duplicate_chart":
        return project_editing.duplicate_chart(snapshot, p.chart_id, operation_id)
    if kind == "delete_chart":
        return project_editing.delete_chart(snapshot, p.chart_id)
    if kind == "update_chart_metadata":
        return project_editing.update_chart_metadata(
            snapshot, p.chart_id, **p.model_dump(exclude={"chart_id"}, exclude_unset=True)
        )
    if kind == "apply_grid":
        grid = TimingGrid.model_validate(
            {**p.grid.model_dump(), "revision": snapshot.grid.revision + 1}
        )
        return ProjectSnapshot(
            snapshot.project, grid, tuple(timing.retime_chart(c, grid) for c in snapshot.charts)
        )
    if kind == "reinterpret_tempo":
        grid, charts = timing.reinterpret_tempo(
            snapshot.grid, snapshot.charts, p.factor, p.pivot_beat_q
        )
        return ProjectSnapshot(snapshot.project, grid, charts)
    charts = list(snapshot.charts)
    index = next((i for i, c in enumerate(charts) if c.id == p.chart_id), None)
    if index is None:
        raise ValueError("unknown chart ID")
    chart = charts[index]
    if kind in advanced_commands.TYPES:
        chart = advanced_commands.apply(chart, snapshot.grid, kind, p, operation_id)
    elif kind == "create_event":
        chart = Chart.model_validate(
            editing.create_event(chart, p.time_ref, p.lanes, operation_id).model_dump()
        )
    elif kind == "delete_notes":
        chart = editing.delete_notes(chart, p.note_ids, operation_id)
    else:
        chart = editing.move_notes(
            chart, snapshot.grid, p.note_ids, p.delta_lane, operation_id, p.time_mode, p.time_delta
        )
    charts[index] = chart
    return ProjectSnapshot(snapshot.project, snapshot.grid, tuple(charts))
