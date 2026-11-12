"""Keep grid-validation results equivalent while reusing segment starts."""

import math

import pytest

from ai_edda import domain
from ai_edda.domain import TimingGrid


def segments():
    return [
        {"start_time_sec": 3, "start_beat_q": 4, "bpm_quarter": 120, "grid_division": 4},
        {"start_time_sec": 5, "start_beat_q": 8, "bpm_quarter": 60, "grid_division": 4},
    ]


@pytest.mark.parametrize(
    "beat,time",
    [
        (2, 2),
        (4, 3),
        (7.999, 4.9995),
        (8, 5),
        (8.001, 5.001),
        (10, 7),
    ],
)
def test_validation_preserves_time_formula_at_segment_boundaries(beat, time):
    grid = TimingGrid(segments=segments(), anchors=[{"beat_q": beat, "time_sec": time}])
    assert grid.time_at(beat) == pytest.approx(time, abs=1e-12)


def test_unordered_anchors_keep_their_order():
    anchors = [
        {"beat_q": 10, "time_sec": 7},
        {"beat_q": 2, "time_sec": 2},
        {"beat_q": 8, "time_sec": 5},
    ]
    assert [a.beat_q for a in TimingGrid(segments=segments(), anchors=anchors).anchors] == [
        10,
        2,
        8,
    ]


@pytest.mark.parametrize(
    "delta,accepted",
    [
        (math.nextafter(1e-6, 0), True),
        (1e-6, True),
        (math.nextafter(1e-6, math.inf), False),
    ],
)
def test_validation_preserves_absolute_tolerance(delta, accepted):
    raw = {
        "segments": [
            {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4}
        ],
        "anchors": [{"beat_q": 0, "time_sec": delta}],
    }
    if accepted:
        TimingGrid.model_validate(raw)
    else:
        with pytest.raises(ValueError, match="GRID_ANCHOR_INCONSISTENT"):
            TimingGrid.model_validate(raw)


def test_continuity_error_precedes_anchor_error():
    raw_segments = segments()
    raw_segments[1]["start_time_sec"] = 6
    with pytest.raises(ValueError, match="GRID_DISCONTINUITY"):
        TimingGrid(segments=raw_segments, anchors=[{"beat_q": 0, "time_sec": 99}])


def test_validation_reuses_one_segment_start_sequence(monkeypatch):
    original = domain.bisect_right
    sequences = []

    def observed(starts, beat):
        sequences.append(starts)
        return original(starts, beat)

    monkeypatch.setattr(domain, "bisect_right", observed)
    TimingGrid(segments=segments(), anchors=[{"beat_q": 2, "time_sec": 2}] * 1000)
    assert len(sequences) == 1000
    assert all(sequence is sequences[0] for sequence in sequences)
    assert sequences[0] == [4, 8]
