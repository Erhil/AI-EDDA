"""Compare participant summaries with the former direct-pair/window semantics."""

import math
import random

import pytest

from ai_edda.domain import (
    AudioTimeRef,
    Chart,
    ChartNote,
    HitEvent,
    TempoSegment,
    TimingGrid,
)
from ai_edda.validation import validate_chart

EPS = 1e-6
CONFLICTS = ("DUPLICATE_LANE_TIME", "TOO_MANY_SIMULTANEOUS_NOTES")
GRID = TimingGrid(
    segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
)


def make_chart(spec):
    return Chart(
        events=tuple(
            HitEvent(
                id=f"e{i}",
                time_ref=AudioTimeRef(kind="audio", time_sec=time),
                notes=tuple(ChartNote(id=f"n{i}-{j}", lane=lane) for j, lane in enumerate(lanes)),
            )
            for i, (time, lanes) in enumerate(spec)
        )
    )


def old_participant_oracle(chart):
    """Use the former nested forward scans, with no production window helpers."""
    notes = sorted(
        (
            (event.time_ref.time_sec, event.id, note)
            for event in chart.events
            for note in event.notes
        ),
        key=lambda row: row[0],
    )
    participants = {code: set() for code in CONFLICTS}
    for i, (time, _, note) in enumerate(notes):
        window = [i]
        for j in range(i + 1, len(notes)):
            if notes[j][0] - time > EPS:
                break
            window.append(j)
            if note.lane == notes[j][2].lane:
                participants[CONFLICTS[0]].update((i, j))
        if len(window) > 2 and len({notes[j][1] for j in window}) > 1:
            participants[CONFLICTS[1]].update(window)
    return {
        code: (
            tuple(dict.fromkeys(notes[i][1] for i in sorted(indices))),
            tuple(notes[i][2].id for i in sorted(indices)),
        )
        for code, indices in participants.items()
        if indices
    }


def assert_oracle(chart):
    expected = old_participant_oracle(chart)
    actual = [item for item in validate_chart(chart, GRID, 1000) if item.code in CONFLICTS]
    assert len(actual) == len(expected)
    assert {item.code for item in actual} == set(expected)
    for item in actual:
        assert (item.event_ids, item.note_ids) == expected[item.code]
        assert len(item.event_ids) == len(set(item.event_ids))
        assert len(item.note_ids) == len(set(item.note_ids))
        assert item.severity == "error"
    assert validate_chart(chart, GRID, 1000) == validate_chart(chart, GRID, 1000)


@pytest.mark.parametrize(
    "spec",
    [
        [],
        [(0, ())],
        [(0, (0,))],
        [(0, (0, 1))],
        [(0, (0, 1, 2))],
        [(0, (0, 1, 2)), (0, (3,))],
        [(0, (0,)), (0.75 * EPS, (1,)), (1.5 * EPS, (2,))],
        [(0, (0,)), (0.75 * EPS, (0,)), (1.5 * EPS, (0,))],
        [(0, (0,)), (0.4 * EPS, (1,)), (0.8 * EPS, (2,)), (1.2 * EPS, (3,))],
        [(0, (0, 1)), (0.5 * EPS, (2, 3)), (1.25 * EPS, (0,))],
        [(10, (0,)), (0, (1, 2)), (0.5 * EPS, (3,)), (10, (1, 2))],
        [(0, (0,)), (0, (0,)), (10, (1,)), (10, (1,))],
    ],
)
def test_small_direct_window_participant_unions(spec):
    assert_oracle(make_chart(spec))


@pytest.mark.parametrize(
    "gap,conflicts",
    [
        (math.nextafter(EPS, -math.inf), True),
        (EPS, True),
        (math.nextafter(EPS, math.inf), False),
    ],
)
def test_actual_float_epsilon_boundaries(gap, conflicts):
    # Zero avoids rounding the boundary through an unrelated time offset.
    chart = make_chart([(0, (0, 1)), (gap, (0,))])
    assert_oracle(chart)
    actual = {item.code for item in validate_chart(chart, GRID, 1000)}
    assert all((code in actual) == conflicts for code in CONFLICTS)


def test_randomized_direct_window_participant_unions():
    rng = random.Random(872361)
    times = [
        0,
        0.25 * EPS,
        0.75 * EPS,
        EPS,
        1.5 * EPS,
        2 * EPS,
        math.nextafter(EPS, -math.inf),
        math.nextafter(EPS, math.inf),
        1,
    ]
    for _ in range(400):
        spec = [
            (rng.choice(times), tuple(rng.randrange(4) for _ in range(rng.randrange(5))))
            for _ in range(rng.randrange(16))
        ]
        rng.shuffle(spec)
        assert_oracle(make_chart(spec))


def test_separate_conflict_groups_use_participant_summary_message():
    chart = make_chart([(0, (0, 1)), (0, (0,)), (10, (2, 3)), (10, (2,))])
    assert_oracle(chart)
    summaries = [item for item in validate_chart(chart, GRID, 1000) if item.code in CONFLICTS]
    for item in summaries:
        assert "participate in conflicts at one or more positions" in item.message.lower()


def test_per_event_errors_and_review_warning_remain():
    chart = make_chart([(-1, ()), (2, (0, 1, 2)), (2, (0,))])
    shifted_grid = GRID.model_copy(
        update={"segments": (GRID.segments[0].model_copy(update={"start_time_sec": 0.25}),)}
    )
    actual = validate_chart(chart, shifted_grid, 1)
    assert [
        (d.code, d.severity, d.event_ids, d.note_ids) for d in actual if d.code not in CONFLICTS
    ] == [
        ("NEEDS_REVIEW", "warning", (), ()),
        ("OUTSIDE_AUDIO_DURATION", "error", ("e0",), ()),
        ("GRID_NOT_COVERING_NOTES", "error", ("e0",), ()),
        ("INVALID_EVENT_SIZE", "error", ("e0",), ()),
        ("OUTSIDE_AUDIO_DURATION", "error", ("e1",), ()),
        ("INVALID_EVENT_SIZE", "error", ("e1",), ("n1-0", "n1-1", "n1-2")),
        ("OUTSIDE_AUDIO_DURATION", "error", ("e2",), ()),
    ]


@pytest.mark.parametrize("coincident", [True, False])
def test_ten_thousand_notes_keep_complete_bounded_output(coincident):
    count = 10000
    chart = make_chart([(1 if coincident else i * 0.05, (i % 4,)) for i in range(count)])
    actual = validate_chart(chart, GRID, 1000)
    assert [(d.code, d.severity) for d in actual if d.code not in CONFLICTS] == [
        ("NEEDS_REVIEW", "warning")
    ]
    summaries = [d for d in actual if d.code in CONFLICTS]
    assert len(summaries) == (2 if coincident else 0)
    for item in summaries:
        assert item.event_ids == tuple(event.id for event in chart.events)
        assert item.note_ids == tuple(note.id for event in chart.events for note in event.notes)
