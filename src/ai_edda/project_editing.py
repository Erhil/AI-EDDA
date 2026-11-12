from .domain import Chart, ChartMetadata, EventLocks, NoteLocks, ObjectRef, SongMetadata, new_id
from .project_store import ProjectSnapshot


def _snapshot(snapshot, project=None, charts=None):
    charts = snapshot.charts if charts is None else tuple(charts)
    project = snapshot.project if project is None else project
    project = project.model_copy(
        update={"chart_heads": tuple(ObjectRef(id=c.id, revision=c.revision) for c in charts)}
    )
    return ProjectSnapshot(project, snapshot.grid, charts)


def update_song_metadata(snapshot, **changes):
    metadata = SongMetadata.model_validate({**snapshot.project.metadata.model_dump(), **changes})
    return _snapshot(snapshot, snapshot.project.model_copy(update={"metadata": metadata}))


def create_chart(snapshot, name=""):
    chart = Chart(
        grid_revision=snapshot.grid.revision, metadata=ChartMetadata(name=name, rank=5, speed=15)
    )
    return _snapshot(snapshot, charts=(*snapshot.charts, chart))


def _replace(snapshot, chart_id, transform):
    if not any(c.id == chart_id for c in snapshot.charts):
        raise KeyError(chart_id)
    return _snapshot(
        snapshot, charts=(transform(c) if c.id == chart_id else c for c in snapshot.charts)
    )


def update_chart_metadata(snapshot, chart_id, **changes):
    allowed = {"name", "rank", "speed"}
    if changes.keys() - allowed:
        raise ValueError("Unknown chart metadata field")
    if "rank" in changes and (type(changes["rank"]) is not int or not 1 <= changes["rank"] <= 10):
        raise ValueError("rank must be an integer from 1 to 10")
    if "speed" in changes:
        from math import isfinite

        speed = changes["speed"]
        if (
            isinstance(speed, bool)
            or not isinstance(speed, (int, float))
            or not isfinite(speed)
            or speed <= 0
        ):
            raise ValueError("speed must be finite and positive")

    def edit(chart):
        metadata = ChartMetadata.model_validate({**chart.metadata.model_dump(), **changes})
        return chart.model_copy(update={"metadata": metadata})

    return _replace(snapshot, chart_id, edit)


def rename_chart(snapshot, chart_id, name):
    return update_chart_metadata(snapshot, chart_id, name=name)


def delete_chart(snapshot, chart_id):
    if not any(c.id == chart_id for c in snapshot.charts):
        raise KeyError(chart_id)
    return _snapshot(snapshot, charts=(c for c in snapshot.charts if c.id != chart_id))


def duplicate_chart(snapshot, chart_id, operation_id=None):
    chart = next((c for c in snapshot.charts if c.id == chart_id), None)
    if chart is None:
        raise KeyError(chart_id)
    operation_id = operation_id or new_id()

    def copied_provenance(provenance, source_id):
        return provenance.model_copy(
            update={
                "source_ids": tuple(dict.fromkeys((*provenance.source_ids, source_id))),
                "last_modified_by": "manual",
                "last_modified_operation_id": operation_id,
            }
        )

    data = chart.model_dump()
    data.update(
        id=new_id(),
        revision=0,
        provenance=copied_provenance(chart.provenance, chart.id).model_dump(),
    )
    for source_event, event in zip(chart.events, data["events"]):
        event_id = new_id()
        event.update(
            id=event_id,
            locks=EventLocks(existence=True, time=True, membership=True).model_dump(),
            provenance=copied_provenance(source_event.provenance, source_event.id).model_dump(),
        )
        for source_note, note in zip(source_event.notes, event["notes"]):
            note.update(
                id=new_id(),
                locks=NoteLocks(existence=True, lane=True).model_dump(),
                provenance=copied_provenance(source_note.provenance, source_note.id).model_dump(),
            )
    return _snapshot(snapshot, charts=(*snapshot.charts, Chart.model_validate(data)))
