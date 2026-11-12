from math import isfinite

from .domain import (
    AudioTimeRef,
    BeatTimeRef,
    ChartNote,
    EventLocks,
    HitEvent,
    NoteLocks,
    Provenance,
)
from .timing import final_time_sec


def _modified(provenance, operation_id):
    return provenance.model_copy(
        update={"last_modified_by": "manual", "last_modified_operation_id": operation_id}
    )


def _keep_note(note, operation_id):
    return note.model_copy(
        update={
            "locks": NoteLocks(existence=True, lane=True),
            "provenance": _modified(note.provenance, operation_id),
        }
    )


def _keep_event(event, notes, operation_id, **updates):
    return event.model_copy(
        update={
            "notes": tuple(notes),
            "locks": EventLocks(existence=True, time=True, membership=True),
            "provenance": _modified(event.provenance, operation_id),
            **updates,
        }
    )


def create_event(chart, time_ref, lanes, operation_id):
    provenance = Provenance(created_by="manual", operation_id=operation_id)
    notes = tuple(
        ChartNote(lane=lane, provenance=provenance, locks=NoteLocks(existence=True, lane=True))
        for lane in lanes
    )
    event = HitEvent(
        time_ref=time_ref,
        notes=notes,
        provenance=provenance,
        locks=EventLocks(existence=True, time=True, membership=True),
    )
    return chart.model_copy(update={"events": chart.events + (event,), "review": "needs_review"})


def _selected(chart, note_ids):
    selected = set(note_ids)
    known = {note.id for event in chart.events for note in event.notes}
    if not selected <= known:
        raise ValueError("unknown note ID")
    return selected


def delete_notes(chart, note_ids, operation_id):
    selected = _selected(chart, note_ids)
    events = []
    for event in chart.events:
        if any(note.id in selected for note in event.notes):
            notes = [
                _keep_note(note, operation_id) for note in event.notes if note.id not in selected
            ]
            if notes:
                events.append(_keep_event(event, notes, operation_id))
        else:
            events.append(event)
    return chart.model_copy(update={"events": tuple(events), "review": "needs_review"})


def move_notes(chart, grid, note_ids, delta_lane, operation_id, time_mode=None, time_delta=0):
    selected = _selected(chart, note_ids)
    if type(delta_lane) is not int:
        raise ValueError("lane delta must be an integer")
    if (
        time_mode not in (None, "beats", "seconds")
        or not isinstance(time_delta, (int, float))
        or not isfinite(time_delta)
    ):
        raise ValueError("invalid time shift")
    if time_mode is None and time_delta != 0:
        raise ValueError("time mode required")
    events = []
    for event in chart.events:
        affected = [note for note in event.notes if note.id in selected]
        if not affected:
            events.append(event)
            continue
        if time_mode is not None and len(affected) != len(event.notes):
            raise ValueError("partial event time move requires split")
        notes = []
        for note in event.notes:
            if note.id in selected:
                lane = note.lane + delta_lane
                if not 0 <= lane <= 3:
                    raise ValueError("lane out of range")
                note = note.model_copy(update={"lane": lane})
            notes.append(_keep_note(note, operation_id))
        ref = event.time_ref
        if time_mode == "beats":
            if isinstance(ref, BeatTimeRef):
                ref = BeatTimeRef(
                    kind="beat",
                    beat_q=ref.beat_q + time_delta,
                    micro_offset_sec=ref.micro_offset_sec,
                )
            else:
                ref = BeatTimeRef(kind="beat", beat_q=grid.inverse_time(ref.time_sec) + time_delta)
        elif time_mode == "seconds":
            ref = AudioTimeRef(kind="audio", time_sec=final_time_sec(grid, ref) + time_delta)
        events.append(_keep_event(event, notes, operation_id, time_ref=ref))
    return chart.model_copy(update={"events": tuple(events), "review": "needs_review"})
