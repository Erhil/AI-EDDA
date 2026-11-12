import pytest

from ai_edda.arrangement_domain import RhythmAnalysis
from ai_edda.domain import AudioAsset, TempoSegment, TimingGrid
from ai_edda.musical_plan import ProjectionConfig, project_musical_plan, reproject_musical_plan


def fixtures():
    audio = AudioAsset(
        path="song.ogg",
        sha256="a" * 64,
        sample_rate=100,
        channels=1,
        frames=200,
        duration_sec=2,
        subtype="VORBIS",
    )
    analysis = RhythmAnalysis(id="analysis", audio_asset_id="audio", audio=audio)
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    return analysis, grid


def test_silence_and_candidates():
    a, g = fixtures()
    args = ([0.5, 1], [0.5], [0, 0.5, 1, 1.5], [0, 2, 1, 0], [0, 0.2, 0.1, 0], [0.5, 1])
    p = project_musical_plan(a, g, *args)
    assert len(p.rhythm_sites) == 5
    assert p.sections[0].end_sec == 2
    assert p.rhythm_sites[0].attack_strength == 2
    assert not project_musical_plan(a, g, *args[:4], [0] * 4, args[5]).rhythm_sites
    new_grid = g.model_copy(
        update={"revision": 1, "segments": (g.segments[0].model_copy(update={"bpm_quarter": 60}),)}
    )
    revised = reproject_musical_plan(p, new_grid)
    assert revised.grid_revision == 1
    assert revised.rhythm_sites[0].beat_q != p.rhythm_sites[0].beat_q
    assert revised.rhythm_sites[0].id == p.rhythm_sites[0].id
    assert revised.rhythm_sites[0].acoustic_evidence == p.rhythm_sites[0].acoustic_evidence
    assert revised.rhythm_sites[0].time_sec == p.rhythm_sites[0].time_sec


@pytest.mark.parametrize(
    "times,env,rms",
    [([0, 1], [0], [0, 1]), ([0, float("nan")], [0, 1], [0, 1]), ([0, 3], [0, 1], [0, 1])],
)
def test_invalid_features(times, env, rms):
    a, g = fixtures()
    with pytest.raises(ValueError):
        project_musical_plan(a, g, [], [], times, env, rms, [])


def test_plan_identity_tracks_projection_inputs():
    a, g = fixtures()
    args = ([0.5], [], [0.5], [1], [1], [])
    base = project_musical_plan(a, g, *args)
    stricter = project_musical_plan(a, g, *args, config=ProjectionConfig(min_rms=2))
    other_grid = g.model_copy(
        update={"segments": (g.segments[0].model_copy(update={"bpm_quarter": 60}),)}
    )
    changed_grid = project_musical_plan(a, other_grid, *args)
    assert len(base.rhythm_sites) == 1
    assert not stricter.rhythm_sites
    assert (base.id, base.revision) != (stricter.id, stricter.revision)
    assert (base.id, base.revision) != (changed_grid.id, changed_grid.revision)
    assert base.id == project_musical_plan(a, g, *args).id
