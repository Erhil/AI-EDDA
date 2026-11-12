from .domain import AudioTimeRef, BeatTimeRef, Chart, TimeRef, TimingGrid


def final_time_sec(grid: TimingGrid, ref: TimeRef) -> float:
    if isinstance(ref, AudioTimeRef):
        return ref.time_sec
    return grid.time_at(ref.beat_q) + ref.micro_offset_sec


def retime_chart(chart: Chart, grid: TimingGrid) -> Chart:
    return chart.model_copy(
        update={
            "revision": chart.revision + 1,
            "grid_revision": grid.revision,
            "review": "needs_review",
        }
    )


def reinterpret_tempo(
    grid: TimingGrid, charts: tuple[Chart, ...], factor: float, pivot_beat_q: float = 0
) -> tuple[TimingGrid, tuple[Chart, ...]]:
    if factor not in (0.5, 2):
        raise ValueError("factor must be 0.5 or 2")

    def scale(q):
        return pivot_beat_q + factor * (q - pivot_beat_q)

    segments = []
    for segment in grid.segments:
        q = scale(segment.start_beat_q)
        if not float(q).is_integer():
            raise ValueError("FRACTIONAL_SEGMENT_BOUNDARY_UNSUPPORTED")
        segments.append(
            segment.model_copy(
                update={"start_beat_q": int(q), "bpm_quarter": segment.bpm_quarter * factor}
            )
        )
    data = grid.model_dump()
    data.update(
        revision=grid.revision + 1,
        segments=segments,
        meter=grid.meter.model_copy(
            update={"bar_origin_beat_q": scale(grid.meter.bar_origin_beat_q)}
        ),
        anchors=[a.model_copy(update={"beat_q": scale(a.beat_q)}) for a in grid.anchors],
        review="needs_review",
    )
    new_grid = TimingGrid.model_validate(data)
    result = []
    for chart in charts:
        events = []
        for event in chart.events:
            ref = event.time_ref
            if isinstance(ref, BeatTimeRef):
                ref = ref.model_copy(update={"beat_q": scale(ref.beat_q)})
            events.append(event.model_copy(update={"time_ref": ref}))
        result.append(retime_chart(chart.model_copy(update={"events": tuple(events)}), new_grid))
    return new_grid, tuple(result)
