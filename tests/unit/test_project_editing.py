import pytest

from ai_edda.domain import (
    BeatTimeRef,
    Chart,
    ChartNote,
    EventLocks,
    HitEvent,
    Provenance,
    SongProject,
    TempoSegment,
    TimingGrid,
)
from ai_edda.project_editing import (
    create_chart,
    delete_chart,
    duplicate_chart,
    rename_chart,
    update_chart_metadata,
    update_song_metadata,
)
from ai_edda.project_store import ProjectSnapshot


def test_project_operations():
    grid = TimingGrid(
        revision=3,
        segments=(
            TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),
        ),
    )
    note = ChartNote(
        lane=1,
        provenance=Provenance(
            created_by="generated", source_ids=("old-note",), generation_run_id="run-1"
        ),
    )
    event = HitEvent(
        time_ref=BeatTimeRef(kind="beat", beat_q=1),
        notes=(note,),
        locks=EventLocks(time=True),
        provenance=Provenance(
            created_by="generated", source_ids=("old-event",), generation_run_id="run-1"
        ),
    )
    source = Chart(
        grid_revision=3,
        events=(event,),
        provenance=Provenance(
            created_by="generated", source_ids=("old-chart",), generation_run_id="run-1"
        ),
    )
    original = ProjectSnapshot(SongProject(), grid, (source,))
    changed = update_song_metadata(original, title="Song", explicit=True)
    assert changed.project.metadata.title == "Song" and changed.project.metadata.artist == ""
    changed = create_chart(changed, "New")
    new = changed.charts[-1]
    assert (new.metadata.rank, new.metadata.speed, new.grid_revision) == (5, 15, 3)
    changed = rename_chart(changed, new.id, "Renamed")
    changed = update_chart_metadata(changed, new.id, rank=10, speed=1.5)
    assert changed.charts[-1].metadata.name == "Renamed"
    for values in ({"rank": 0}, {"rank": 11}, {"speed": float("nan")}, {"speed": 0}):
        with pytest.raises(ValueError):
            update_chart_metadata(changed, new.id, **values)
    duplicated = duplicate_chart(changed, source.id, operation_id="copy-op")
    clone = duplicated.charts[-1]
    assert clone.id != source.id and clone.events[0].id != source.events[0].id
    assert clone.events[0].notes[0].id != source.events[0].notes[0].id
    assert clone.provenance.source_ids == ("old-chart", source.id)
    assert clone.events[0].provenance.source_ids == ("old-event", source.events[0].id)
    assert clone.events[0].notes[0].provenance.source_ids == (
        "old-note",
        source.events[0].notes[0].id,
    )
    for copied, original in (
        (clone, source),
        (clone.events[0], source.events[0]),
        (clone.events[0].notes[0], source.events[0].notes[0]),
    ):
        assert copied.provenance.created_by == original.provenance.created_by
        assert copied.provenance.generation_run_id == original.provenance.generation_run_id
        assert copied.provenance.last_modified_by == "manual"
        assert copied.provenance.last_modified_operation_id == "copy-op"
    assert clone.events[0].locks == EventLocks(existence=True, time=True, membership=True)
    assert clone.events[0].notes[0].locks == ChartNote(lane=1).locks.model_copy(
        update={"existence": True, "lane": True}
    )
    renamed = rename_chart(duplicated, clone.id, "Copy")
    assert renamed.charts[0] == source
    assert len(delete_chart(renamed, clone.id).charts) == 2
