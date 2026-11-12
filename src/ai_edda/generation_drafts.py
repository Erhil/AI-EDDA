from .arrangement import validate_context
from .arrangement_domain import ChartDiff, ChartDraft, GenerationRun
from .domain import Chart, ObjectRef, Provenance
from .generation_locks import validate_generated_span
from .timing import final_time_sec
from .validation import validate_chart


def assemble_draft(request, context, output, capabilities):
    validate_context(request, context)
    if (
        (request.generator_id, request.generator_version)
        != (capabilities.backend_id, capabilities.backend_version)
        or (output.backend_id, output.backend_version)
        != (capabilities.backend_id, capabilities.backend_version)
        or request.mode not in capabilities.modes
    ):
        raise ValueError("unsupported generator or mode")
    if output.target != request.target or output.seed != request.seed:
        raise ValueError("output target or seed does not match request")
    grid = context.grid
    start = grid.time_at(request.range_q.start_q)
    end = grid.time_at(request.range_q.end_q)
    validate_generated_span(context.chart, grid, start, end, output.events)
    base = {e.id: e for e in context.chart.events}
    base_notes = {n.id: n for e in context.chart.events for n in e.notes}

    def provenance(old, changed):
        if not changed:
            return old
        return old.model_copy(
            update={"last_modified_by": "generated", "last_modified_operation_id": request.id}
        )

    def new_provenance(p):
        return Provenance(
            created_by="generated", generation_run_id=request.id, source_ids=p.source_ids
        )

    events = []
    for event in output.events:
        original = base.get(event.id)
        notes = []
        for note in event.notes:
            old = base_notes.get(note.id)
            if old:
                changed = note.model_dump(exclude={"provenance", "locks"}) != old.model_dump(
                    exclude={"provenance", "locks"}
                )
                note = note.model_copy(
                    update={"locks": old.locks, "provenance": provenance(old.provenance, changed)}
                )
            else:
                note = note.model_copy(update={"provenance": new_provenance(note.provenance)})
            notes.append(note)
        if original:
            changed = event.time_ref != original.time_ref or tuple(notes) != original.notes
            event = event.model_copy(
                update={
                    "notes": tuple(notes),
                    "locks": original.locks,
                    "provenance": provenance(original.provenance, changed),
                }
            )
        else:
            event = event.model_copy(
                update={"notes": tuple(notes), "provenance": new_provenance(event.provenance)}
            )
        events.append(event)
    outside = [
        e for e in context.chart.events if not start <= final_time_sec(grid, e.time_ref) < end
    ]
    # Rebuild nested models from field data. Pydantic otherwise trusts already-created
    # instances and can let invalid nested values through model_copy updates.
    chart_data = context.chart.model_dump()
    chart_data["events"] = [event.model_dump() for event in outside + events]
    chart = Chart.model_validate(chart_data)
    after = {e.id: e for e in chart.events}
    diff = ChartDiff(
        added_event_ids=tuple(e.id for e in chart.events if e.id not in base),
        removed_event_ids=tuple(e.id for e in context.chart.events if e.id not in after),
        changed_event_ids=tuple(e.id for e in chart.events if e.id in base and e != base[e.id]),
    )
    return ChartDraft(
        base_chart=ObjectRef(id=context.chart.id, revision=context.chart.revision),
        chart=chart,
        diff=diff,
        validation=validate_chart(chart, grid, context.analysis.audio.duration_sec),
        generation_run=GenerationRun(
            id=request.id, request=request, capabilities=capabilities, output=output
        ),
    )
