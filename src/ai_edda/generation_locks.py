from collections.abc import Iterable
from math import isfinite

from .domain import Chart, HitEvent, TimingGrid
from .timing import final_time_sec


def validate_generated_span(
    base_chart: Chart,
    grid: TimingGrid,
    start_sec: float,
    end_sec: float,
    proposed_events: Iterable[HitEvent],
) -> None:
    """Validate a complete replacement of the physical half-open span."""
    if not (isfinite(start_sec) and isfinite(end_sec) and start_sec < end_sec):
        raise ValueError("invalid span boundaries")
    inside = []
    outside = []
    for event in base_chart.events:
        (inside if start_sec <= final_time_sec(grid, event.time_ref) < end_sec else outside).append(
            event
        )
    event_ids = {event.id for event in outside}
    note_ids = {note.id for event in outside for note in event.notes}
    proposed = {}
    for event in proposed_events:
        if not start_sec <= final_time_sec(grid, event.time_ref) < end_sec:
            raise ValueError(f"event {event.id} outside span")
        if event.id in event_ids:
            raise ValueError(f"duplicate event ID {event.id}")
        event_ids.add(event.id)
        if any(type(n.lane) is not int or not 0 <= n.lane <= 3 for n in event.notes):
            raise ValueError(f"event {event.id} lanes must be integers from 0 to 3")
        if len(event.notes) not in (1, 2) or len({n.lane for n in event.notes}) != len(event.notes):
            raise ValueError(f"event {event.id} must have one or two distinct lanes")
        for note in event.notes:
            if note.id in note_ids:
                raise ValueError(f"duplicate note ID {note.id}")
            note_ids.add(note.id)
        proposed[event.id] = event
    for original in inside:
        protected_notes = [n for n in original.notes if n.locks.existence or n.locks.lane]
        required = (
            original.locks.existence
            or original.locks.time
            or original.locks.membership
            or protected_notes
        )
        current = proposed.get(original.id)
        if current is None:
            if required:
                raise ValueError(f"missing protected event {original.id}")
            continue
        if original.locks.time and current.time_ref != original.time_ref:
            raise ValueError(f"locked time changed for event {original.id}")
        current_notes = {n.id: n for n in current.notes}
        if original.locks.membership and set(current_notes) != {n.id for n in original.notes}:
            raise ValueError(f"locked membership changed for event {original.id}")
        for note in protected_notes:
            replacement = current_notes.get(note.id)
            if replacement is None:
                raise ValueError(f"missing protected note {note.id} in event {original.id}")
            if note.locks.lane and replacement.lane != note.lane:
                raise ValueError(f"locked lane changed for note {note.id}")
