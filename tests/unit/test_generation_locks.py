import pytest

from ai_edda.domain import (
    AudioTimeRef,
    BeatTimeRef,
    Chart,
    ChartNote,
    EventLocks,
    HitEvent,
    NoteLocks,
    TempoSegment,
    TimingGrid,
)
from ai_edda.generation_locks import validate_generated_span

GRID = TimingGrid(
    segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
)
NOTE = ChartNote(id="n", lane=0, locks=NoteLocks(existence=True, lane=True))
EVENT = HitEvent(
    id="e",
    time_ref=AudioTimeRef(kind="audio", time_sec=1),
    notes=(NOTE,),
    locks=EventLocks(existence=True, time=True, membership=True),
)


def check(base, output):
    validate_generated_span(Chart(events=base), GRID, 1, 2, output)


def test_keep_and_missing():
    check((EVENT,), (EVENT,))
    with pytest.raises(ValueError, match="missing protected event"):
        check((EVENT,), ())


@pytest.mark.parametrize(
    "changed, message",
    [
        (EVENT.model_copy(update={"time_ref": AudioTimeRef(kind="audio", time_sec=1.1)}), "time"),
        (EVENT.model_copy(update={"notes": (NOTE.model_copy(update={"lane": 1}),)}), "lane"),
        (EVENT.model_copy(update={"notes": (ChartNote(id="other", lane=1),)}), "membership"),
    ],
)
def test_locked_fields(changed, message):
    with pytest.raises(ValueError, match=message):
        check((EVENT,), (changed,))


def test_partial_lock_and_owner():
    event = EVENT.model_copy(update={"locks": EventLocks(time=True)})
    double = event.model_copy(update={"notes": (NOTE, ChartNote(id="new", lane=1))})
    check((event,), (double,))
    with pytest.raises(ValueError, match="missing protected note"):
        check((event,), (event.model_copy(update={"notes": (ChartNote(id="new", lane=1),)}),))


def test_boundaries_and_collision():
    outside = EVENT.model_copy(
        update={
            "id": "outside",
            "time_ref": BeatTimeRef(kind="beat", beat_q=2, micro_offset_sec=-0.1),
        }
    )
    with pytest.raises(ValueError, match="outside span"):
        check((), (outside,))
    with pytest.raises(ValueError, match="duplicate note"):
        check((outside,), (EVENT,))


def test_invalid_lanes():
    with pytest.raises(ValueError, match="distinct lanes"):
        check((), (EVENT.model_copy(update={"notes": (NOTE, ChartNote(id="n2", lane=0))}),))


@pytest.mark.parametrize("lane", [-1, 4, True, 1.5])
def test_reject_invalid_lane_values(lane):
    event = EVENT.model_copy(update={"notes": (NOTE.model_copy(update={"lane": lane}),)})
    with pytest.raises(ValueError, match="lanes must be integers from 0 to 3"):
        check((), (event,))
