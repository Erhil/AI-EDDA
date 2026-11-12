from dataclasses import dataclass
from math import isfinite

from pydantic import TypeAdapter, ValidationError

from .domain import AudioTimeRef, BeatTimeRef, EventLocks, NoteLocks, TimeRef, new_id
from .editing import _keep_event, _keep_note, _modified, _selected
from .timing import final_time_sec


def _events(chart, ids):
    ids = set(ids)
    if not ids <= {e.id for e in chart.events}:
        raise ValueError("unknown event ID")
    return ids


def _result(chart, events):
    return chart.model_copy(update={"events": tuple(events), "review": "needs_review"})


def mirror_lanes(chart, note_ids, operation_id):
    selected = _selected(chart, note_ids)
    return _result(
        chart,
        [
            _keep_event(
                e,
                [
                    _keep_note(
                        n.model_copy(update={"lane": 3 - n.lane}) if n.id in selected else n,
                        operation_id,
                    )
                    for n in e.notes
                ],
                operation_id,
            )
            if any(n.id in selected for n in e.notes)
            else e
            for e in chart.events
        ],
    )


def split_event(chart, event_id, note_ids, operation_id, time_ref=None):
    _events(chart, [event_id])
    selected = _selected(chart, note_ids)
    event = next(e for e in chart.events if e.id == event_id)
    if not selected or not selected < {n.id for n in event.notes}:
        raise ValueError("split requires a proper subset of event notes")
    remaining = _keep_event(
        event,
        [_keep_note(n, operation_id) for n in event.notes if n.id not in selected],
        operation_id,
    )
    if time_ref is None:
        child_ref = event.time_ref
    else:
        try:
            child_ref = TypeAdapter(TimeRef).validate_python(
                time_ref.model_dump() if hasattr(time_ref, "model_dump") else time_ref
            )
        except (ValidationError, AttributeError, TypeError):
            raise ValueError("invalid split time reference")
    provenance = _modified(event.provenance, operation_id).model_copy(
        update={"source_ids": tuple(dict.fromkeys((*event.provenance.source_ids, event.id)))}
    )
    child = _keep_event(
        event,
        [_keep_note(n, operation_id) for n in event.notes if n.id in selected],
        operation_id,
        id=new_id(),
        time_ref=child_ref,
        provenance=provenance,
    )
    return _result(
        chart, [x for e in chart.events for x in ((remaining, child) if e.id == event_id else (e,))]
    )


def join_events(chart, grid, event_ids, operation_id):
    ids = _events(chart, event_ids)
    events = [e for e in chart.events if e.id in ids]
    if len(events) < 2:
        raise ValueError("join requires multiple events")
    notes = [n for e in events for n in e.notes]
    times = [final_time_sec(grid, e.time_ref) for e in events]
    if (
        max(times) - min(times) > 1e-6
        or len(notes) > 2
        or len({n.lane for n in notes}) != len(notes)
    ):
        raise ValueError("events cannot be joined")
    first = events[0]
    provenance = _modified(first.provenance, operation_id).model_copy(
        update={
            "source_ids": tuple(
                dict.fromkeys(s for e in events for s in (*e.provenance.source_ids, e.id))
            )
        }
    )
    joined = _keep_event(
        first, [_keep_note(n, operation_id) for n in notes], operation_id, provenance=provenance
    )
    return _result(
        chart,
        [
            joined if e.id == first.id else e
            for e in chart.events
            if e.id == first.id or e.id not in ids
        ],
    )


def set_locks(
    chart, event_ids=(), note_ids=(), event_locks=None, note_locks=None, operation_id=None
):
    events = _events(chart, event_ids)
    notes = _selected(chart, note_ids)
    if (
        events
        and not isinstance(event_locks, EventLocks)
        or notes
        and not isinstance(note_locks, NoteLocks)
    ):
        raise ValueError("locks required")
    result = []
    for e in chart.events:
        ns = tuple(
            n.model_copy(
                update={"locks": note_locks, "provenance": _modified(n.provenance, operation_id)}
            )
            if n.id in notes
            else n
            for n in e.notes
        )
        if e.id in events or any(n.id in notes for n in e.notes):
            e = e.model_copy(
                update={
                    "notes": ns,
                    "locks": event_locks if e.id in events else e.locks,
                    "provenance": _modified(e.provenance, operation_id),
                }
            )
        result.append(e)
    return _result(chart, result)


def quantize_events(chart, grid, event_ids, grid_division, operation_id):
    ids = _events(chart, event_ids)
    if type(grid_division) is not int or grid_division <= 0:
        raise ValueError("invalid grid division")
    changes = []
    result = []
    for e in chart.events:
        if e.id in ids:
            old = final_time_sec(grid, e.time_ref)
            q = round(grid.inverse_time(old) * grid_division) / grid_division
            ref = BeatTimeRef(kind="beat", beat_q=q)
            changes.append((e.id, old, final_time_sec(grid, ref)))
            e = _keep_event(
                e, [_keep_note(n, operation_id) for n in e.notes], operation_id, time_ref=ref
            )
        result.append(e)
    return _result(chart, result), tuple(changes)


@dataclass(frozen=True)
class ClipboardEvent:
    event: object
    beat: float
    second: float


@dataclass(frozen=True)
class NotesClipboard:
    events: tuple[ClipboardEvent, ...]


def copy_notes(chart, grid, note_ids):
    selected = _selected(chart, note_ids)
    if not selected:
        raise ValueError("empty selection")
    entries = []
    for event in chart.events:
        notes = tuple(n for n in event.notes if n.id in selected)
        if notes:
            snapshot = event.model_copy(update={"notes": notes})
            second = final_time_sec(grid, event.time_ref)
            entries.append(ClipboardEvent(snapshot, grid.inverse_time(second), second))
    return NotesClipboard(tuple(entries))


def paste_notes(chart, clipboard, target, mode, operation_id):
    if (
        not isinstance(clipboard, NotesClipboard)
        or not clipboard.events
        or mode not in ("beats", "seconds")
        or not isinstance(target, (int, float))
        or not isfinite(target)
    ):
        raise ValueError("invalid paste")
    events = [x.event for x in clipboard.events]
    positions = [x.beat if mode == "beats" else x.second for x in clipboard.events]
    anchor = min(positions)
    copies = []
    for e, pos in zip(events, positions):
        ref = (
            BeatTimeRef(kind="beat", beat_q=target + pos - anchor)
            if mode == "beats"
            else AudioTimeRef(kind="audio", time_sec=target + pos - anchor)
        )

        def ancestry(p, source):
            return _modified(p, operation_id).model_copy(
                update={"source_ids": tuple(dict.fromkeys((*p.source_ids, source)))}
            )

        notes = [
            n.model_copy(update={"id": new_id(), "provenance": ancestry(n.provenance, n.id)})
            for n in e.notes
        ]
        copied = _keep_event(
            e,
            [_keep_note(n, operation_id) for n in notes],
            operation_id,
            id=new_id(),
            time_ref=ref,
            provenance=ancestry(e.provenance, e.id),
        )
        copies.append(copied)
    return _result(chart, (*chart.events, *copies))
