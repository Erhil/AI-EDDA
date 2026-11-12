import pytest

from ai_edda.domain import (
    AudioTimeRef,
    BeatTimeRef,
    Chart,
    ChartNote,
    HitEvent,
    Provenance,
    TempoSegment,
    TimingGrid,
)
from ai_edda.editing import create_event, delete_notes, move_notes


def test_editing_preserves_history_and_source():
    p = Provenance(created_by="generated", generation_run_id="run")
    a, b = ChartNote(lane=0, provenance=p), ChartNote(lane=1, provenance=p)
    event = HitEvent(
        time_ref=BeatTimeRef(kind="beat", beat_q=3, micro_offset_sec=0.1),
        notes=(a, b),
        provenance=p,
    )
    chart = Chart(events=(event,))
    grid = TimingGrid(
        segments=(
            TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),
            TempoSegment(start_time_sec=2, start_beat_q=4, bpm_quarter=60, grid_division=4),
        )
    )
    moved = move_notes(chart, grid, [a.id], 1, "move")
    assert moved.events[0].notes[0].lane == 1
    assert moved.events[0].notes[1].locks.lane
    assert moved.events[0].provenance.generation_run_id == "run"
    assert moved.events[0].provenance.last_modified_operation_id == "move"
    assert chart.events[0].notes[0].lane == 0
    with pytest.raises(ValueError):
        move_notes(chart, grid, [a.id], 0, "move", "beats", 1)
    shifted = move_notes(chart, grid, [a.id, b.id], 0, "move", "beats", 2)
    assert shifted.events[0].time_ref == BeatTimeRef(kind="beat", beat_q=5, micro_offset_sec=0.1)
    seconds = move_notes(chart, grid, [a.id, b.id], 0, "move", "seconds", 1)
    assert seconds.events[0].time_ref == AudioTimeRef(kind="audio", time_sec=2.6)
    deleted = delete_notes(chart, [a.id], "delete")
    assert deleted.events[0].notes[0].locks.existence
    assert deleted.events[0].locks.membership
    with pytest.raises(ValueError):
        delete_notes(chart, ["missing"], "delete")
    with pytest.raises(ValueError):
        move_notes(chart, grid, [a.id], -1, "move")


def test_create_and_audio_projection():
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    chart = create_event(Chart(), AudioTimeRef(kind="audio", time_sec=1), [0], "create")
    note = chart.events[0].notes[0]
    moved = move_notes(chart, grid, [note.id], 0, "move", "beats", 1)
    assert moved.events[0].time_ref == BeatTimeRef(kind="beat", beat_q=3)
    assert chart.events[0].locks.time
