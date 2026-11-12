import math

import pytest

from ai_edda.grid_hypotheses import generate_grid_hypotheses


def test_fit_with_missing_and_extra_beats():
    times = sorted([0.25 + 0.5 * i for i in range(16) if i != 5] + [3.43])
    hypotheses = generate_grid_hypotheses(times, 9)
    assert hypotheses[0].estimated_bpm == pytest.approx(120, abs=0.1)
    assert hypotheses[0].phase_sec == pytest.approx(0.25, abs=0.01)
    assert hypotheses[0].inlier_count == 15
    assert hypotheses[1].estimated_bpm == pytest.approx(60, abs=0.1)
    assert hypotheses[2].estimated_bpm == pytest.approx(240, abs=0.1)
    assert all(h.grid.review == "needs_review" for h in hypotheses)


def test_90_and_duplicate_and_silence():
    h = generate_grid_hypotheses([0.2, 0.2, 0.2 + 2 / 3, 0.2 + 4 / 3, 0.2 + 2], 4)
    assert h[0].estimated_bpm == pytest.approx(90)
    assert generate_grid_hypotheses([], 4) == ()
    assert generate_grid_hypotheses([0.2, 0.2], 4) == ()


@pytest.mark.parametrize("times", [[-0.1, 0.5, 1], [0, math.nan, 1], [1, 0.5, 2]])
def test_invalid_times(times):
    with pytest.raises(ValueError):
        generate_grid_hypotheses(times, 4)


def test_phase_near_period_boundary_normalizes_to_zero():
    hypotheses = generate_grid_hypotheses([0.5 * i for i in range(16)], 8)
    assert hypotheses[0].phase_sec == 0


def test_derived_phase_near_candidate_period_normalizes_to_zero():
    hypotheses = generate_grid_hypotheses([0.25 + 0.5 * i for i in range(8)], 5)
    assert hypotheses[2].phase_sec == 0


@pytest.mark.parametrize("times,duration", [([0, True, 1], 2), ([0, 1, 2], True)])
def test_boolean_inputs_are_invalid(times, duration):
    with pytest.raises(ValueError):
        generate_grid_hypotheses(times, duration)
