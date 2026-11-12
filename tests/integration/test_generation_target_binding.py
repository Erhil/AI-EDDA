import uuid
from dataclasses import replace

import pytest

from ai_edda import generation_context
from ai_edda.arrangement_domain import MusicalPlan, RhythmAnalysis, Section, TargetProfile
from ai_edda.domain import AudioAsset, Chart, ObjectRef, SongProject, TempoSegment, TimingGrid
from ai_edda.project_store import ProjectSnapshot
from ai_edda.target_catalog import publish_target


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    audio = AudioAsset(
        path="song.ogg",
        sha256="a" * 64,
        sample_rate=100,
        channels=1,
        frames=1000,
        duration_sec=10,
        subtype="VORBIS",
    )
    analysis = RhythmAnalysis(id=str(uuid.uuid4()), revision=2, audio_asset_id="audio", audio=audio)
    analysis_ref = ObjectRef(id=analysis.id, revision=analysis.revision)
    grid = TimingGrid(
        revision=7,
        review="approved",
        segments=(
            TempoSegment(
                start_time_sec=0,
                start_beat_q=0,
                bpm_quarter=120,
                grid_division=4,
                review="approved",
            ),
        ),
    )
    profile = TargetProfile(
        id=str(uuid.uuid4()),
        version=1,
        name="Selected",
        intended_rank=5,
        allowed_subdivisions=(2, 4),
    )
    target_ref = publish_target(tmp_path, profile)
    chart = Chart(revision=9, grid_revision=7, target_profile=target_ref)
    project = SongProject(audio=audio, analysis=analysis_ref, grid_revision=7)
    snapshot = ProjectSnapshot(project, grid, (chart,))
    plan = MusicalPlan(
        id=str(uuid.uuid4()),
        revision=0,
        audio_asset_id=analysis.audio_asset_id,
        audio_sha256=audio.sha256,
        analysis=analysis_ref,
        grid_revision=7,
        sections=(Section(start_sec=0, end_sec=10),),
    )
    request = {
        "project_id": project.id,
        "generator_id": "single_lane",
        "generator_version": "1",
        "audio_asset_id": analysis.audio_asset_id,
        "audio_sha256": audio.sha256,
        "analysis": analysis_ref.model_dump(),
        "plan": {"id": plan.id, "revision": 0},
        "grid_revision": 7,
        "chart": {"id": chart.id, "revision": chart.revision},
        "target": profile.model_dump(),
        "range_q": {"start_q": 0, "end_q": 4},
        "mode": "single_lane",
        "lane": 2,
        "seed": 17,
    }
    # Existing verified analysis and plan are supplied. Target catalog IO remains real.
    monkeypatch.setattr(generation_context, "resolve_analysis", lambda *args: (analysis, {}))
    monkeypatch.setattr(generation_context, "resolve_plan", lambda *args: plan)
    return tmp_path, snapshot, request, profile


def test_generation_accepts_full_current_target(inputs):
    root, snapshot, request, _ = inputs
    context = generation_context.prepare_generation(root, snapshot, request)
    assert context.chart == snapshot.charts[0]
    assert context.analysis.audio == snapshot.project.audio
    assert context.grid == snapshot.grid


@pytest.mark.parametrize(
    "change",
    [
        {"id": str(uuid.uuid4())},
        {"version": 2},
        {"name": "Changed"},
        {"intended_rank": 6},
        {"allowed_subdivisions": (1,)},
        {"ornament_budget": 0.5},
    ],
)
def test_generation_rejects_any_full_profile_mismatch_as_stale(inputs, change):
    root, snapshot, request, _ = inputs
    request["target"] = {**request["target"], **change}
    with pytest.raises(ValueError, match="^STALE_REVISION$"):
        generation_context.prepare_generation(root, snapshot, request)


def test_current_chart_target_replacement_invalidates_captured_request(inputs):
    root, snapshot, request, _ = inputs
    other = TargetProfile(id=str(uuid.uuid4()), version=1, name="Different selected target")
    ref = publish_target(root, other)
    chart = snapshot.charts[0].model_copy(update={"target_profile": ref})
    with pytest.raises(ValueError, match="^STALE_REVISION$"):
        generation_context.prepare_generation(root, replace(snapshot, charts=(chart,)), request)


def test_unbound_chart_keeps_ad_hoc_target_support(inputs):
    root, snapshot, request, _ = inputs
    chart = snapshot.charts[0].model_copy(update={"target_profile": None})
    request["target"] = {
        "id": str(uuid.uuid4()),
        "version": 3,
        "name": "Ad hoc",
        "allowed_subdivisions": [1],
    }
    context = generation_context.prepare_generation(
        root, replace(snapshot, charts=(chart,)), request
    )
    assert context.chart.target_profile is None


@pytest.mark.parametrize("corrupt", [False, True])
def test_bound_target_must_resolve_from_catalog(inputs, corrupt):
    root, snapshot, request, profile = inputs
    path = root / "targets" / f"{profile.id}-1.json"
    if corrupt:
        path.write_bytes(b"{broken")
    else:
        path.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        generation_context.prepare_generation(root, snapshot, request)


@pytest.mark.parametrize(
    "change",
    [
        {"project_id": str(uuid.uuid4())},
        {"chart": {"id": str(uuid.uuid4()), "revision": 9}},
        {"chart": {"id": None, "revision": 8}},
        {"grid_revision": 6},
        {"analysis": {"id": str(uuid.uuid4()), "revision": 2}},
        {"plan": {"id": str(uuid.uuid4()), "revision": 0}},
        {"audio_sha256": "b" * 64},
        {"audio_asset_id": "other"},
    ],
)
def test_existing_generation_revision_checks_remain_active(inputs, change):
    root, snapshot, request, _ = inputs
    if "chart" in change and change["chart"]["id"] is None:
        change = {"chart": {"id": snapshot.charts[0].id, "revision": 8}}
    with pytest.raises(ValueError, match="^STALE_REVISION$"):
        generation_context.prepare_generation(root, snapshot, {**request, **change})


@pytest.mark.parametrize("grid_approved", [False, True])
def test_unreviewed_grid_or_segment_is_rejected(inputs, grid_approved):
    root, snapshot, request, _ = inputs
    if grid_approved:
        segment = snapshot.grid.segments[0].model_copy(update={"review": "needs_review"})
        grid = snapshot.grid.model_copy(update={"segments": (segment,)})
    else:
        grid = snapshot.grid.model_copy(update={"review": "needs_review"})
    with pytest.raises(ValueError, match="^STALE_REVISION$"):
        generation_context.prepare_generation(root, replace(snapshot, grid=grid), request)


@pytest.mark.parametrize(
    "change,message",
    [
        ({"generator_version": "2"}, "unsupported generator"),
        ({"range_q": {"start_q": 0, "end_q": 100}}, "range outside audio"),
        (
            {
                "reference_chart": {"id": str(uuid.uuid4()), "revision": 0},
                "reference_influence": "rhythm_theme",
            },
            "reference charts are unsupported",
        ),
    ],
)
def test_existing_generator_limits_remain_active(inputs, change, message):
    root, snapshot, request, _ = inputs
    with pytest.raises(ValueError, match=message):
        generation_context.prepare_generation(root, snapshot, {**request, **change})
