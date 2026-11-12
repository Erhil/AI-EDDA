import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_edda.domain import BeatTimeRef, Chart, ChartNote, Provenance, TimingGrid
from ai_edda.timing import final_time_sec

ROOT = Path(__file__).resolve().parents[2]


def fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def test_timing_cases():
    cases = fixture("timing_cases.json")["cases"]
    count = 0
    for case in cases:
        segments = [
            {k: v for k, v in s.items() if k != "expected_global_beat"} for s in case["segments"]
        ]
        grid = TimingGrid(segments=segments, meter=case["meter"])
        for note in case["notes"]:
            t = final_time_sec(grid, BeatTimeRef(kind="beat", beat_q=note["beat_q"]))
            assert t == pytest.approx(note["expected_time_sec"], abs=1e-6)
            assert grid.inverse_time(t) == pytest.approx(note["beat_q"])
            count += len(note["lanes"])
    assert count == 26


def test_invalid_and_immutable():
    with pytest.raises(ValidationError, match="GRID_DISCONTINUITY"):
        TimingGrid(segments=fixture("timing_cases.json")["invalid_cases"][0]["segments"])
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            BeatTimeRef(kind="beat", beat_q=value)
    with pytest.raises(ValidationError):
        ChartNote(lane=True)
    assert Provenance(created_by="imported").source_origin == "unknown"


def test_independent_fixture_charts():
    charts = []
    for raw in fixture("arrangement_cases.json")["charts"]:
        events = []
        for event in raw["events"]:
            event = dict(event)
            event["provenance"] = {
                k: v for k, v in event["provenance"].items() if k != "fixture_label"
            }
            events.append(event)
        charts.append(
            Chart(
                id=raw["id"],
                revision=raw["revision"],
                grid_revision=raw["grid_revision"],
                metadata={"rank": raw["intended_rank"]},
                events=events,
            )
        )
    assert charts[0].events[0].id != charts[1].events[0].id
    assert sum(len(e.notes) for e in charts[0].events) == 5
    assert sum(len(e.notes) for e in charts[1].events) == 7
    with pytest.raises(ValidationError):
        Chart(events=[charts[0].events[0], charts[0].events[0]])
