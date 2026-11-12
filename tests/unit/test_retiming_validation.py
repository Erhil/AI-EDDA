import pytest

from ai_edda.domain import (
    AudioTimeRef,
    BeatTimeRef,
    Chart,
    ChartNote,
    GridAnchor,
    HitEvent,
    TempoSegment,
    TimingGrid,
)
from ai_edda.timing import final_time_sec, reinterpret_tempo, retime_chart
from ai_edda.validation import validate_chart


def grid():
    return TimingGrid(
        segments=(
            TempoSegment(start_time_sec=0.25, start_beat_q=0, bpm_quarter=120, grid_division=4),
        ),
        anchors=(GridAnchor(beat_q=2, time_sec=1.25),),
    )


def test_reinterpretation_preserves_time_and_source():
    g = grid()
    c = Chart(
        events=(
            HitEvent(
                time_ref=BeatTimeRef(kind="beat", beat_q=1.5, micro_offset_sec=0.01),
                notes=(ChartNote(lane=1),),
            ),
        )
    )
    for factor in (2, 0.5):
        new, (chart,) = reinterpret_tempo(g, (c,), factor)
        assert final_time_sec(new, chart.events[0].time_ref) == pytest.approx(
            final_time_sec(g, c.events[0].time_ref)
        )
        assert chart.events[0].id == c.events[0].id
        assert chart.grid_revision == new.revision
    assert c.revision == 0 and g.revision == 0
    assert retime_chart(c, g).events == c.events


def test_anchor_and_fraction_errors():
    with pytest.raises(ValueError, match="GRID_ANCHOR_INCONSISTENT"):
        TimingGrid(segments=grid().segments, anchors=(GridAnchor(beat_q=1, time_sec=4),))
    with pytest.raises(ValueError, match="FRACTIONAL_SEGMENT"):
        reinterpret_tempo(grid(), (), 0.5, 1)


def test_global_collision():
    c = Chart(
        events=(
            HitEvent(
                time_ref=BeatTimeRef(kind="beat", beat_q=0, micro_offset_sec=0.1),
                notes=(ChartNote(lane=0), ChartNote(lane=1)),
            ),
            HitEvent(
                time_ref=AudioTimeRef(kind="audio", time_sec=0.35), notes=(ChartNote(lane=1),)
            ),
        )
    )
    codes = {d.code for d in validate_chart(c, grid(), 10)}
    assert {"DUPLICATE_LANE_TIME", "TOO_MANY_SIMULTANEOUS_NOTES", "NEEDS_REVIEW"} <= codes


def test_validation_collision_tolerance_boundaries():
    g = grid()

    def chart_at(delta):
        return Chart(
            events=(
                HitEvent(
                    time_ref=AudioTimeRef(kind="audio", time_sec=1.0), notes=(ChartNote(lane=0),)
                ),
                HitEvent(
                    time_ref=AudioTimeRef(kind="audio", time_sec=1.0 + delta),
                    notes=(ChartNote(lane=0),),
                ),
            )
        )

    assert "DUPLICATE_LANE_TIME" in {d.code for d in validate_chart(chart_at(1e-6), g, 10)}
    assert "DUPLICATE_LANE_TIME" not in {
        d.code for d in validate_chart(chart_at(1.000001e-6), g, 10)
    }
