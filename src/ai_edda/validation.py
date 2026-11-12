from typing import Literal

from .domain import Chart, Model, TimingGrid
from .timing import final_time_sec


class Diagnostic(Model):
    code: str
    severity: Literal["error", "warning"]
    message: str
    event_ids: tuple[str, ...] = ()
    note_ids: tuple[str, ...] = ()


def validate_chart(
    chart: Chart, grid: TimingGrid, audio_duration_sec: float
) -> tuple[Diagnostic, ...]:
    result = []

    def add(code, message, events=(), notes=(), severity="error"):
        result.append(
            Diagnostic(
                code=code, severity=severity, message=message, event_ids=events, note_ids=notes
            )
        )

    if chart.review == "needs_review":
        add("NEEDS_REVIEW", "Chart needs review.", severity="warning")
    notes = []
    for event in chart.events:
        t = final_time_sec(grid, event.time_ref)
        if t < 0 or t > audio_duration_sec:
            add("OUTSIDE_AUDIO_DURATION", "Event is outside audio duration.", (event.id,))
        if t < grid.segments[0].start_time_sec - 1e-6:
            add("GRID_NOT_COVERING_NOTES", "Event is before the first marker.", (event.id,))
        if len(event.notes) == 0 or len(event.notes) > 2:
            add(
                "INVALID_EVENT_SIZE",
                "Event must contain one or two notes.",
                (event.id,),
                tuple(n.id for n in event.notes),
            )
        notes.extend((t, event.id, n) for n in event.notes)
    notes.sort(key=lambda x: x[0])
    duplicate = set()
    previous = {}
    for i, (t, _, note) in enumerate(notes):
        prior = previous.get(note.lane)
        if prior is not None and t - notes[prior][0] <= 1e-6:
            duplicate.update((prior, i))
        previous[note.lane] = i
    left = 0
    counts = {}
    coverage = [0] * (len(notes) + 1)
    for right, (t, eid, _) in enumerate(notes):
        counts[eid] = counts.get(eid, 0) + 1
        while t - notes[left][0] > 1e-6:
            old_id = notes[left][1]
            counts[old_id] -= 1
            if counts[old_id] == 0:
                del counts[old_id]
            left += 1
        if right - left + 1 > 2 and len(counts) > 1:
            coverage[left] += 1
            coverage[right + 1] -= 1
    if duplicate:
        ids = tuple(dict.fromkeys(notes[i][1] for i in sorted(duplicate)))
        nids = tuple(dict.fromkeys(notes[i][2].id for i in sorted(duplicate)))
        add(
            "DUPLICATE_LANE_TIME",
            "These notes participate in conflicts at one or more positions.",
            ids,
            nids,
        )
    affected = []
    active = 0
    for i in range(len(notes)):
        active += coverage[i]
        if active:
            affected.append(i)
    if affected:
        ids = tuple(dict.fromkeys(notes[i][1] for i in affected))
        nids = tuple(dict.fromkeys(notes[i][2].id for i in affected))
        add(
            "TOO_MANY_SIMULTANEOUS_NOTES",
            "These notes participate in conflicts at one or more positions.",
            ids,
            nids,
        )
    return tuple(result)
