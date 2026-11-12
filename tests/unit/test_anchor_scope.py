import pytest

from ai_edda.arrangement import SingleLaneProvider
from ai_edda.arrangement_domain import (
    ArrangementContext,
    ArrangementRequest,
    MusicalAnchor,
    MusicalPlan,
    RhythmAnalysis,
    Section,
    TargetProfile,
)
from ai_edda.domain import (
    AudioAsset,
    Chart,
    ObjectRef,
    TempoSegment,
    TimingGrid,
)


def inputs(*, anchors=(), start=0, end=8):
    grid = TimingGrid(
        segments=(
            TempoSegment(
                start_time_sec=0,
                start_beat_q=0,
                bpm_quarter=120,
                grid_division=4,
            ),
        ),
        review="confirmed",
    )
    audio = AudioAsset(
        path="not-opened.ogg",
        sha256="a" * 64,
        sample_rate=100,
        channels=1,
        frames=4 * 100,
        duration_sec=4,
        subtype="VORBIS",
    )
    analysis = RhythmAnalysis(id="analysis", audio_asset_id="audio", audio=audio)
    plan = MusicalPlan(
        id="plan",
        audio_asset_id="audio",
        audio_sha256=audio.sha256,
        analysis=ObjectRef(id=analysis.id, revision=analysis.revision),
        grid_revision=0,
        sections=(Section(id="section", start_sec=0, end_sec=4),),
        rhythm_sites=(),
        musical_anchors=anchors,
    )
    chart = Chart(id="chart", events=())
    context = ArrangementContext(grid=grid, analysis=analysis, plan=plan, chart=chart)
    request = ArrangementRequest(
        id="run",
        project_id="project",
        generator_id="single_lane",
        generator_version="1",
        audio_asset_id="audio",
        audio_sha256=audio.sha256,
        analysis={"id": analysis.id, "revision": 0},
        plan={"id": plan.id, "revision": 0},
        chart={"id": chart.id, "revision": 0},
        grid_revision=0,
        target=TargetProfile(id="target", version=1, name="test"),
        range_q={"start_q": start, "end_q": end},
        mode="single_lane",
        lane=2,
        seed=17,
    )
    return request, context


def generate(request, context):
    return SingleLaneProvider().generate(request, context)


@pytest.mark.parametrize(
    "scope,raises",
    [
        ((), True),
        (("chart",), True),
        (("other-chart",), False),
        (("other-chart", "chart"), True),
    ],
)
def test_required_anchor_applies_only_to_selected_charts(scope, raises):
    anchor = MusicalAnchor(time_sec=0.5, strength="required", target_scope=scope)
    request, context = inputs(anchors=(anchor,))
    if raises:
        with pytest.raises(ValueError, match="unsupported required musical anchor"):
            generate(request, context)
    else:
        assert generate(request, context).events == ()


@pytest.mark.parametrize("time_sec,in_range", [(1.0, True), (3.0, False)])
def test_required_anchor_uses_half_open_requested_span(time_sec, in_range):
    anchor = MusicalAnchor(time_sec=time_sec, strength="required", target_scope=("chart",))
    request, context = inputs(anchors=(anchor,), start=2, end=6)
    if in_range:
        with pytest.raises(ValueError, match="unsupported required musical anchor"):
            generate(request, context)
    else:
        assert generate(request, context).events == ()
