import math
from dataclasses import dataclass

import numpy as np

from .domain import TempoSegment, TimingGrid


@dataclass(frozen=True)
class GridHypothesis:
    grid: TimingGrid
    estimated_bpm: float
    phase_sec: float
    residual_rms_sec: float
    inlier_count: int
    score: float
    interpretation: str


def generate_grid_hypotheses(
    observed_beat_times, duration_sec: float
) -> tuple[GridHypothesis, ...]:
    """Fit a constant tempo. Score is an uncalibrated fit measure, not a probability."""
    if (
        isinstance(duration_sec, (bool, np.bool_))
        or not math.isfinite(duration_sec)
        or duration_sec < 0
    ):
        raise ValueError("duration must be finite and nonnegative")
    raw_times = tuple(observed_beat_times)
    if any(isinstance(value, (bool, np.bool_)) for value in raw_times):
        raise ValueError("beat times must be numeric and not boolean")
    times = np.asarray(raw_times, dtype=float)
    if (
        times.ndim != 1
        or np.any(~np.isfinite(times))
        or np.any(times < 0)
        or np.any(times > duration_sec)
    ):
        raise ValueError("beat times must be finite and inside the audio duration")
    if np.any(np.diff(times) < 0):
        raise ValueError("beat times must be sorted")
    times = np.unique(times)
    if len(times) < 3:
        return ()
    period = float(np.median(np.diff(times)))
    if period <= 0:
        return ()
    # Circular phase voting is robust to isolated extra observations.
    phase = float(
        times[
            np.argmax(
                [
                    np.sum(np.abs((times - t + period / 2) % period - period / 2) < period * 0.15)
                    for t in times
                ]
            )
        ]
        % period
    )
    for _ in range(5):
        indices = np.rint((times - phase) / period)
        residual = times - (phase + indices * period)
        mask = np.abs(residual) < period * 0.2
        if mask.sum() < 3 or np.ptp(indices[mask]) < 2:
            return ()
        period, phase = np.polyfit(indices[mask], times[mask], 1)
        if period <= 0:
            return ()
    phase = float(phase % period)
    phase_tolerance = max(1e-12, abs(period) * 1e-12)
    if phase <= phase_tolerance or period - phase <= phase_tolerance:
        phase = 0.0
    residual = (times - phase + period / 2) % period - period / 2
    mask = np.abs(residual) < period * 0.2
    rms = float(np.sqrt(np.mean(residual[mask] ** 2)))
    score = float(mask.sum() / len(times) / (1 + rms / period))
    result = []
    for factor, label in (
        (1.0, "observed beats are quarter notes"),
        (0.5, "observed beats are eighth notes; alternate downbeat phase is possible"),
        (2.0, "observed beats are half notes"),
    ):
        candidate_period = period / factor
        candidate_phase = phase % candidate_period
        if (
            candidate_phase <= phase_tolerance
            or candidate_period - candidate_phase <= phase_tolerance
        ):
            candidate_phase = 0.0
        bpm = 60 / candidate_period
        grid = TimingGrid(
            segments=(
                TempoSegment(
                    start_time_sec=candidate_phase,
                    start_beat_q=0,
                    bpm_quarter=bpm,
                    grid_division=4,
                    origin="automatic",
                ),
            )
        )
        result.append(
            GridHypothesis(grid, bpm, candidate_phase, rms, int(mask.sum()), score, label)
        )
    return tuple(result)
