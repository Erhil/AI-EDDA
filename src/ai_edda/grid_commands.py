from pydantic import Field, StrictFloat, StrictInt

from . import grid_editing, timing
from .domain import GridAnchor, Model, TimingGrid
from .project_store import ProjectSnapshot


class ShiftGrid(Model):
    delta_seconds: float = Field(strict=True)


class ChangeBpm(Model):
    segment_index: StrictInt
    bpm: float = Field(strict=True)
    pivot_q: float = Field(strict=True)


class SetAnchors(Model):
    anchors: tuple["StrictGridAnchor", ...]


class StrictGridAnchor(Model):
    beat_q: StrictFloat
    time_sec: StrictFloat
    origin: str = "manual"


class SetSubdivision(Model):
    segment_index: StrictInt
    grid_division: StrictInt = Field(gt=0)


class ReviewGrid(Model):
    pass


TYPES = {
    "shift_grid": ShiftGrid,
    "change_bpm": ChangeBpm,
    "set_grid_anchors": SetAnchors,
    "set_grid_subdivision": SetSubdivision,
    "review_grid": ReviewGrid,
}


def apply(snapshot, kind, p):
    grid = snapshot.grid
    if kind == "review_grid":
        segments = [s.model_copy(update={"review": "approved"}) for s in grid.segments]
        grid = TimingGrid.model_validate(
            grid.model_dump()
            | {"segments": segments, "review": "approved", "revision": grid.revision + 1}
        )
        return ProjectSnapshot(snapshot.project, grid, snapshot.charts)
    if kind == "shift_grid":
        grid = grid_editing.shift_grid(grid, p.delta_seconds)
    elif kind == "change_bpm":
        if p.segment_index < 0 or p.segment_index >= len(grid.segments):
            raise ValueError(f"Invalid segment_index: {p.segment_index}")
        grid = grid_editing.change_bpm(grid, p.segment_index, p.bpm, p.pivot_q)
    elif kind == "set_grid_anchors":
        grid = grid_editing.set_anchors(
            grid, tuple(GridAnchor(**anchor.model_dump()) for anchor in p.anchors)
        )
        segments = [s.model_copy(update={"review": "needs_review"}) for s in grid.segments]
        grid = TimingGrid.model_validate(
            grid.model_dump() | {"segments": segments, "review": "needs_review"}
        )
    else:
        if p.segment_index < 0 or p.segment_index >= len(grid.segments):
            raise ValueError(f"Invalid segment_index: {p.segment_index}")
        segments = list(grid.segments)
        segments[p.segment_index] = segments[p.segment_index].model_copy(
            update={"grid_division": p.grid_division, "review": "needs_review"}
        )
        grid = TimingGrid.model_validate(
            grid.model_dump()
            | {"segments": segments, "review": "needs_review", "revision": grid.revision + 1}
        )
    return ProjectSnapshot(
        snapshot.project, grid, tuple(timing.retime_chart(c, grid) for c in snapshot.charts)
    )
