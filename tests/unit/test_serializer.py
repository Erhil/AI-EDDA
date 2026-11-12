import json
from pathlib import Path

import pytest

from ai_edda.domain import (
    BeatTimeRef,
    Chart,
    ChartMetadata,
    ChartNote,
    HitEvent,
    SongMetadata,
    TempoSegment,
    TimingGrid,
)
from ai_edda.serializer import serialize_difficulty, serialize_info

CASES = json.loads(Path("fixtures/timing_cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_timing(case):
    grid = TimingGrid(
        segments=tuple(
            TempoSegment(**{k: v for k, v in s.items() if k != "expected_global_beat"})
            for s in case["segments"]
        )
    )
    chart = Chart(
        events=tuple(
            HitEvent(
                time_ref=BeatTimeRef(kind="beat", beat_q=n["beat_q"]),
                notes=tuple(ChartNote(lane=lane) for lane in n["lanes"]),
            )
            for n in case["notes"]
        )
    )
    result = serialize_difficulty(chart, grid)
    expected = [(n["expected_global_beat"], lane) for n in case["notes"] for lane in n["lanes"]]
    assert [n["_time"] for n in result["_notes"]] == pytest.approx([t for t, _ in expected])
    assert [n["_lineIndex"] for n in result["_notes"]] == [lane for _, lane in expected]
    assert [m["_time"] for m in result["_customData"]["_BPMChanges"]] == pytest.approx(
        [s["expected_global_beat"] for s in case["segments"]]
    )


def test_info_selection():
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    charts = [Chart(metadata=ChartMetadata(rank=r, speed=15)) for r in (7, 3, 7)]
    result = serialize_info(
        SongMetadata(title="Song", subtitle="Sub", artist="Artist", mapper="Mapper", explicit=True),
        grid,
        12.1,
        charts,
    )
    items = result["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"]
    assert [(x["_difficulty"], x["_difficultyRank"]) for x in items] == [
        ("Easy", 3),
        ("Normal", 7),
        ("Hard", 7),
    ]
    assert result["_songApproximativeDuration"] == 13
    assert result["_songSubName"] == "Sub"
    assert result["_explicit"] == "true"
    with pytest.raises(ValueError):
        serialize_info(SongMetadata(), grid, 12, charts + charts[:1])


def test_info_rejects_duplicate_chart_id():
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    chart = Chart(metadata=ChartMetadata(rank=3, speed=15))
    with pytest.raises(ValueError, match="duplicate selected chart id"):
        serialize_info(SongMetadata(), grid, 12, [chart, chart])

    # Identical contents and rank remain valid when chart IDs differ.
    twin = Chart(id="different-id", metadata=ChartMetadata(rank=3, speed=15))
    result = serialize_info(SongMetadata(), grid, 12, [chart, twin])
    assert len(result["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"]) == 2
