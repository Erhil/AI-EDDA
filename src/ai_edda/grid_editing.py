from math import ceil, isclose, isfinite

from .domain import TempoSegment, TimingGrid


def _grid(grid, segments, anchors):
    return TimingGrid(
        revision=grid.revision + 1,
        segments=segments,
        meter=grid.meter,
        anchors=anchors,
        review="needs_review",
    )


def shift_grid(grid, delta_seconds):
    if isinstance(delta_seconds, bool) or not isfinite(delta_seconds):
        raise ValueError("INVALID_SHIFT")
    segments = [
        TempoSegment(
            **(
                s.model_dump()
                | {"start_time_sec": s.start_time_sec + delta_seconds, "review": "needs_review"}
            )
        )
        for s in grid.segments
    ]
    anchors = [
        type(a)(**(a.model_dump() | {"time_sec": a.time_sec + delta_seconds})) for a in grid.anchors
    ]
    return _grid(grid, segments, anchors)


def change_bpm(grid, segment_index, bpm, pivot_q):
    if (
        isinstance(bpm, bool)
        or isinstance(pivot_q, bool)
        or not isfinite(bpm)
        or bpm <= 0
        or not isfinite(pivot_q)
    ):
        raise ValueError("INVALID_BPM_OR_PIVOT")
    if type(segment_index) is not int:
        raise TypeError("segment_index must be an int")
    if segment_index < 0 or segment_index >= len(grid.segments):
        raise IndexError(segment_index)
    old = grid.segments
    rates = [s.bpm_quarter for s in old]
    rates[segment_index] = bpm
    times = [0.0]
    for i in range(1, len(old)):
        times.append(
            times[-1] + (old[i].start_beat_q - old[i - 1].start_beat_q) * 60 / rates[i - 1]
        )
    i = (
        max(i for i, s in enumerate(old) if s.start_beat_q <= pivot_q)
        if pivot_q >= old[0].start_beat_q
        else 0
    )
    offset = grid.time_at(pivot_q) - (times[i] + (pivot_q - old[i].start_beat_q) * 60 / rates[i])
    segments = [
        TempoSegment(
            **(
                s.model_dump()
                | {"start_time_sec": t + offset, "bpm_quarter": rate, "review": "needs_review"}
            )
        )
        for s, t, rate in zip(old, times, rates)
    ]
    try:
        return _grid(grid, segments, grid.anchors)
    except ValueError as e:
        if "GRID_ANCHOR_INCONSISTENT" in str(e):
            raise ValueError("ANCHOR_CONFLICT") from e
        raise


def set_anchors(grid, anchors):
    anchors = tuple(sorted(anchors, key=lambda a: a.beat_q))
    if any(not isfinite(a.beat_q) or not isfinite(a.time_sec) for a in anchors):
        raise ValueError("INVALID_ANCHOR")
    if any(b.beat_q <= a.beat_q or b.time_sec <= a.time_sec for a, b in zip(anchors, anchors[1:])):
        raise ValueError("NONMONOTONIC_ANCHORS")
    if len(anchors) < 2:
        if not anchors:
            return _grid(grid, grid.segments, ())
        return shift_grid(
            _grid(grid, grid.segments, ()), anchors[0].time_sec - grid.time_at(anchors[0].beat_q)
        ).model_copy(update={"revision": grid.revision + 1, "anchors": anchors})
    slopes = [
        (b.time_sec - a.time_sec) / (b.beat_q - a.beat_q) for a, b in zip(anchors, anchors[1:])
    ]
    first = anchors[0]
    start_q = ceil(first.beat_q - first.time_sec / slopes[0] - 1e-10)
    start_t = first.time_sec + (start_q - first.beat_q) * slopes[0]
    template = grid.segments[0]
    # Keep every existing subdivision boundary and overlay the tempo boundaries
    # implied by the anchors. Each resulting interval uses the applicable slope.
    slope_boundaries = {start_q: (slopes[0], start_t)}
    for i in range(1, len(slopes)):
        if isclose(slopes[i], slopes[i - 1], rel_tol=1e-10, abs_tol=1e-10):
            continue
        a = anchors[i]
        if not isclose(a.beat_q, round(a.beat_q), abs_tol=1e-9):
            raise ValueError("UNSUPPORTED_FRACTIONAL_BOUNDARY")
        if round(a.beat_q) <= start_q:
            raise ValueError("UNSUPPORTED_BOUNDARY")
        slope_boundaries[round(a.beat_q)] = (slopes[i], a.time_sec)
    # Retain only boundaries where the subdivision changes. The first segment
    # marker defines the grid origin; it is not itself a subdivision change.
    existing = [
        s
        for i, s in enumerate(grid.segments)
        if i > 0
        and s.start_beat_q > start_q
        and s.grid_division != grid.segments[i - 1].grid_division
    ]
    boundaries = sorted(set(slope_boundaries) | {s.start_beat_q for s in existing})
    segments = []
    current_slope = slopes[0]
    for q in boundaries:
        if q in slope_boundaries:
            current_slope, anchor_time = slope_boundaries[q]
        old = next((s for s in reversed(grid.segments) if s.start_beat_q <= q), template)
        prior_q = max(boundary for boundary in slope_boundaries if boundary <= q)
        prior_time = slope_boundaries[prior_q][1]
        time = prior_time + (q - prior_q) * current_slope
        segments.append(
            TempoSegment(
                start_beat_q=q,
                start_time_sec=max(0, time),
                bpm_quarter=60 / current_slope,
                grid_division=old.grid_division,
            )
        )
    return _grid(grid, segments, anchors)
