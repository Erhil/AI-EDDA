from dataclasses import replace

import pytest

from ai_edda.domain import (
    AudioAsset,
    AudioTimeRef,
    BeatTimeRef,
    Chart,
    ChartNote,
    HitEvent,
    Provenance,
    SongProject,
    TempoSegment,
    TimingGrid,
)
from ai_edda.history import HistoryRecord
from ai_edda.project_store import ProjectSnapshot


def snapshot():
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    event = HitEvent(
        id="event",
        time_ref=BeatTimeRef(kind="beat", beat_q=1),
        notes=(ChartNote(id="note", lane=1),),
    )
    return ProjectSnapshot(
        SongProject(), grid, (Chart(id="chart", events=(event,), review="approved"),)
    )


def test_event_undo_preserves_unrelated_changes_and_redo_restores_identity():
    before = snapshot()
    event = HitEvent(
        id="new",
        time_ref=AudioTimeRef(kind="audio", time_sec=2),
        notes=(ChartNote(id="new-note", lane=2),),
        provenance=Provenance(created_by="generated", source_ids=("source",)),
    )
    after = replace(before, charts=(before.charts[0].model_copy(update={"events": (event,)}),))
    audio = AudioAsset(
        path="song.wav",
        sha256="hash",
        sample_rate=48000,
        channels=2,
        frames=48000,
        duration_sec=1,
        subtype="PCM",
    )
    current = replace(
        after,
        project=after.project.model_copy(
            update={
                "metadata": after.project.metadata.model_copy(update={"title": "Later"}),
                "audio": audio,
            }
        ),
        charts=(
            after.charts[0].model_copy(
                update={"metadata": after.charts[0].metadata.model_copy(update={"name": "Later"})}
            ),
        ),
    )
    record = HistoryRecord.between(before, after)
    undone = record.restore(current, "undo")
    assert undone.project.metadata.title == "Later" and undone.project.audio == audio
    assert undone.charts[0].metadata.name == "Later"
    assert undone.charts[0].events == before.charts[0].events
    assert record.restore(undone, "redo").charts[0].events == (event,)


def test_event_conflict_does_not_mutate_input():
    before = snapshot()
    after = replace(before, charts=(before.charts[0].model_copy(update={"events": ()}),))
    current = replace(
        after,
        charts=(
            after.charts[0].model_copy(
                update={"events": (before.charts[0].events[0].model_copy(update={"id": "other"}),)}
            ),
        ),
    )
    saved = current.charts[0].model_dump()
    with pytest.raises(ValueError, match="UNDO_CONFLICT"):
        HistoryRecord.between(before, after).restore(current, "undo")
    assert current.charts[0].model_dump() == saved


def test_grid_and_charts_restore_together_and_keep_revisions():
    before = snapshot()
    audio = HitEvent(time_ref=AudioTimeRef(kind="audio", time_sec=3))
    second = Chart(id="second", events=(audio,), review="approved")
    before = replace(before, charts=before.charts + (second,))
    grid = before.grid.model_copy(
        update={"segments": (before.grid.segments[0].model_copy(update={"bpm_quarter": 180}),)}
    )
    moved = (
        before.charts[0]
        .events[0]
        .model_copy(update={"time_ref": BeatTimeRef(kind="beat", beat_q=2)})
    )
    after = replace(
        before, grid=grid, charts=(before.charts[0].model_copy(update={"events": (moved,)}), second)
    )
    current = replace(
        after,
        grid=grid.model_copy(update={"revision": 9}),
        charts=tuple(
            c.model_copy(update={"revision": 8, "grid_revision": 9}) for c in after.charts
        ),
    )
    result = HistoryRecord.between(before, after).restore(current, "undo")
    assert result.grid.segments == before.grid.segments and result.grid.revision == 9
    assert result.charts[0].events == before.charts[0].events
    assert result.charts[1].events[0].time_ref.time_sec == 3
    assert all(c.revision == 8 and c.grid_revision == 9 for c in result.charts)
    assert all(c.review == "needs_review" for c in result.charts)


@pytest.mark.parametrize("direction", ["undo", "redo"])
def test_chart_presence_conflicts_with_modified_chart(direction):
    before = snapshot()
    after = replace(before, charts=())
    current = replace(before, charts=(before.charts[0].model_copy(update={"events": ()}),))
    record = (
        HistoryRecord.between(before, after)
        if direction == "redo"
        else HistoryRecord.between(after, before)
    )
    with pytest.raises(ValueError, match="UNDO_CONFLICT"):
        record.restore(current, direction)
