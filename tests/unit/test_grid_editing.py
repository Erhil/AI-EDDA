import pytest

from ai_edda.domain import GridAnchor, TempoSegment, TimingGrid
from ai_edda.grid_editing import change_bpm, set_anchors, shift_grid


def grid():
    return TimingGrid(
        segments=(
            TempoSegment(start_time_sec=0.25, start_beat_q=0, bpm_quarter=120, grid_division=4),
        )
    )


def test_anchors_and_pickup():
    result = set_anchors(
        grid(), [GridAnchor(beat_q=0, time_sec=1), GridAnchor(beat_q=3, time_sec=3)]
    )
    assert result.segments[0].bpm_quarter == 90
    assert result.segments[0].start_beat_q == -1
    assert result.time_at(3) == pytest.approx(3)


def test_shift_and_pivot():
    assert shift_grid(grid(), 1).time_at(0) == 1.25
    result = change_bpm(grid(), 0, 90, 1)
    assert result.time_at(1) == pytest.approx(grid().time_at(1))


def test_conflict_and_bpm():
    anchored = set_anchors(
        grid(), [GridAnchor(beat_q=0, time_sec=1), GridAnchor(beat_q=3, time_sec=3)]
    )
    with pytest.raises(ValueError, match="ANCHOR_CONFLICT"):
        change_bpm(anchored, 0, 120, 0)
    for bpm in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            change_bpm(grid(), 0, bpm, 0)


def test_fractional_boundary():
    anchors = [
        GridAnchor(beat_q=0, time_sec=1),
        GridAnchor(beat_q=0.5, time_sec=1.5),
        GridAnchor(beat_q=1, time_sec=2.5),
    ]
    with pytest.raises(ValueError, match="UNSUPPORTED_FRACTIONAL_BOUNDARY"):
        set_anchors(grid(), anchors)


def test_anchors_preserve_existing_subdivision_change():
    source = TimingGrid(
        segments=(
            TempoSegment(start_time_sec=1, start_beat_q=0, bpm_quarter=120, grid_division=4),
            TempoSegment(start_time_sec=2, start_beat_q=2, bpm_quarter=120, grid_division=8),
        )
    )
    result = set_anchors(
        source, [GridAnchor(beat_q=0, time_sec=1), GridAnchor(beat_q=4, time_sec=3)]
    )
    assert [s.grid_division for s in result.segments] == [4, 8]
    assert result.segments[1].start_beat_q == 2
    assert result.segments[1].start_time_sec == pytest.approx(2)
    assert result.time_at(4) == pytest.approx(3)
