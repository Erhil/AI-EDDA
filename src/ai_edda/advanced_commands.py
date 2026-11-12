from typing import Literal

from pydantic import Field, StrictInt

from . import advanced_editing as edits
from . import editing
from .domain import ChartNote, EventLocks, HitEvent, Model, NoteLocks, Provenance, TimeRef


class Selection(Model):
    chart_id: str
    note_ids: tuple[str, ...]


class EventSelection(Model):
    chart_id: str
    event_ids: tuple[str, ...]


class Split(Selection):
    event_id: str
    time_ref: TimeRef | None = None
    delta_lane: StrictInt = 0


class Quantize(EventSelection):
    grid_division: StrictInt = Field(gt=0)


class Locks(Model):
    chart_id: str
    event_ids: tuple[str, ...] = ()
    note_ids: tuple[str, ...] = ()
    event_locks: EventLocks | None = None
    note_locks: NoteLocks | None = None


class ClipboardEntry(Model):
    event: HitEvent
    beat: float
    second: float


class Paste(Model):
    chart_id: str
    events: tuple[ClipboardEntry, ...] = Field(min_length=1)
    target: float
    mode: Literal["beats", "seconds"]


class EventLanes(Model):
    chart_id: str
    event_id: str
    lanes: tuple[StrictInt, ...] = Field(min_length=1, max_length=2)


TYPES = {
    "mirror_lanes": Selection,
    "split_event": Split,
    "join_events": EventSelection,
    "quantize_events": Quantize,
    "set_locks": Locks,
    "paste_notes": Paste,
    "set_event_lanes": EventLanes,
}


def apply(chart, grid, kind, p, operation_id):
    if kind == "mirror_lanes":
        return edits.mirror_lanes(chart, p.note_ids, operation_id)
    if kind == "split_event":
        result = edits.split_event(chart, p.event_id, p.note_ids, operation_id, p.time_ref)
        return editing.move_notes(result, grid, p.note_ids, p.delta_lane, operation_id)
    if kind == "join_events":
        return edits.join_events(chart, grid, p.event_ids, operation_id)
    if kind == "quantize_events":
        return edits.quantize_events(chart, grid, p.event_ids, p.grid_division, operation_id)[0]
    if kind == "set_locks":
        return edits.set_locks(
            chart, p.event_ids, p.note_ids, p.event_locks, p.note_locks, operation_id
        )
    if kind == "paste_notes":
        clipboard = edits.NotesClipboard(
            tuple(edits.ClipboardEvent(e.event, e.beat, e.second) for e in p.events)
        )
        return edits.paste_notes(chart, clipboard, p.target, p.mode, operation_id)
    edits._events(chart, [p.event_id])
    if len(set(p.lanes)) != len(p.lanes) or any(lane < 0 or lane > 3 for lane in p.lanes):
        raise ValueError("invalid lanes")
    event = next(e for e in chart.events if e.id == p.event_id)
    existing = {n.lane: n for n in event.notes}
    notes = [
        editing._keep_note(
            existing[lane]
            if lane in existing
            else ChartNote(
                lane=lane, provenance=Provenance(created_by="manual", operation_id=operation_id)
            ),
            operation_id,
        )
        for lane in p.lanes
    ]
    replacement = editing._keep_event(event, notes, operation_id)
    return edits._result(chart, [replacement if e.id == p.event_id else e for e in chart.events])
