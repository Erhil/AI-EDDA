import pytest

from ai_edda.advanced_editing import (
    copy_notes,
    join_events,
    mirror_lanes,
    paste_notes,
    quantize_events,
    set_locks,
    split_event,
)
from ai_edda.domain import (
    AudioTimeRef,
    BeatTimeRef,
    Chart,
    ChartNote,
    EventLocks,
    HitEvent,
    NoteLocks,
    Provenance,
    TempoSegment,
    TimingGrid,
)


@pytest.fixture
def data():
    p = Provenance(created_by="generated", generation_run_id="run", source_ids=("source",))
    a, b, c = (ChartNote(lane=i, provenance=p) for i in range(3))
    e = HitEvent(time_ref=BeatTimeRef(kind="beat", beat_q=1.1), notes=(a, b), provenance=p)
    f = HitEvent(time_ref=AudioTimeRef(kind="audio", time_sec=3), notes=(c,), provenance=p)
    grid = TimingGrid(
        segments=(
            TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),
            TempoSegment(start_time_sec=2, start_beat_q=4, bpm_quarter=60, grid_division=4),
        )
    )
    return Chart(events=(e, f)), grid, a, b, c


def test_mirror_and_split(data):
    chart, grid, a, b, c = data
    mirrored = mirror_lanes(chart, [a.id], "mirror")
    assert mirrored.events[0].notes[0].lane == 3
    assert mirrored.events[1] == chart.events[1]
    assert chart.events[0].notes[0].lane == 0
    ref = AudioTimeRef(kind="audio", time_sec=4)
    result = split_event(chart, chart.events[0].id, [a.id], "split", ref)
    assert result.events[0].id == chart.events[0].id
    assert result.events[0].notes[0].id == b.id
    assert result.events[1].notes[0].id == a.id
    assert result.events[1].time_ref == ref
    assert chart.events[0].id in result.events[1].provenance.source_ids
    assert result.events[2] == chart.events[1]
    for ids in ([], [a.id, b.id], [c.id], ["missing"]):
        with pytest.raises(ValueError):
            split_event(chart, chart.events[0].id, ids, "split")
    with pytest.raises(ValueError):
        mirror_lanes(chart, ["missing"], "mirror")
    for bad in (
        object(),
        {"kind": "audio", "time_sec": float("nan")},
        AudioTimeRef(kind="audio", time_sec=1).model_copy(update={"time_sec": float("inf")}),
        0,
    ):
        with pytest.raises(ValueError):
            split_event(chart, chart.events[0].id, [a.id], "split", bad)


def test_join_and_atomic_rejection(data):
    chart, grid, a, b, c = data
    e = HitEvent(time_ref=AudioTimeRef(kind="audio", time_sec=1), notes=(a,))
    f = HitEvent(time_ref=AudioTimeRef(kind="audio", time_sec=1.0000005), notes=(b,))
    source = Chart(events=(e, f))
    result = join_events(source, grid, [e.id, f.id], "join")
    assert len(result.events) == 1
    assert {n.id for n in result.events[0].notes} == {a.id, b.id}
    assert set(result.events[0].provenance.source_ids) >= {e.id, f.id}
    for bad in (
        f.model_copy(update={"time_ref": AudioTimeRef(kind="audio", time_sec=1.000002)}),
        f.model_copy(update={"notes": (b.model_copy(update={"lane": a.lane}),)}),
        f.model_copy(update={"notes": (b, c)}),
    ):
        original = Chart(events=(e, bad))
        before = original.model_dump()
        with pytest.raises(ValueError):
            join_events(original, grid, [e.id, bad.id], "join")
        assert original.model_dump() == before
    with pytest.raises(ValueError):
        join_events(source, grid, [e.id, "missing"], "join")


def test_locks_and_quantize(data):
    chart, grid, a, b, c = data
    e = chart.events[0]
    locked = set_locks(chart, [e.id], [a.id], EventLocks(time=True), NoteLocks(lane=True), "lock")
    unlocked = set_locks(locked, [e.id], [a.id], EventLocks(), NoteLocks(), "unlock")
    assert not unlocked.events[0].locks.time
    assert not unlocked.events[0].notes[0].locks.lane
    assert unlocked.events[1] == chart.events[1]
    result, changes = quantize_events(chart, grid, [e.id], 4, "quantize")
    assert changes == ((e.id, pytest.approx(0.55), pytest.approx(0.5)),)
    assert result.events[0].provenance.generation_run_id == "run"
    assert result.events[0].provenance.source_ids == ("source",)
    assert result.events[1] == chart.events[1]
    with pytest.raises(ValueError):
        quantize_events(chart, grid, ["missing"], 4, "quantize")
    with pytest.raises(ValueError):
        set_locks(chart, note_ids=["missing"], note_locks=NoteLocks())


@pytest.mark.parametrize(
    "mode,target,expected", [("beats", 8, (8, 11.9)), ("seconds", 5, (5, 7.45))]
)
def test_paste_mixed_refs_and_ancestry(data, mode, target, expected):
    chart, grid, a, b, c = data
    clipboard = copy_notes(chart, grid, [a.id, c.id])
    changed = mirror_lanes(chart, [a.id, c.id], "edit")
    destination = Chart()
    result = paste_notes(destination, clipboard, target, mode, "paste")
    copies = result.events
    values = tuple(e.time_ref.beat_q if mode == "beats" else e.time_ref.time_sec for e in copies)
    assert values == pytest.approx(expected)
    for copy, original in zip(copies, chart.events):
        assert copy.id != original.id
        assert original.id in copy.provenance.source_ids
        assert copy.notes[0].id != original.notes[0].id
        assert original.notes[0].id in copy.notes[0].provenance.source_ids
        assert copy.notes[0].locks.existence
    for invalid in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            paste_notes(destination, clipboard, invalid, mode, "paste")
    second = paste_notes(result, clipboard, target, mode, "paste-again")
    assert {e.id for e in second.events[2:]}.isdisjoint({e.id for e in copies})
    assert changed.events[0].notes[0].lane != copies[0].notes[0].lane
