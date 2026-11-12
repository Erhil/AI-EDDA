import pytest
from pydantic import ValidationError

from ai_edda.arrangement_domain import (
    ArrangementRangeQ,
    ArrangementRequest,
    FeatureManifestRef,
    Section,
    TargetProfile,
)
from ai_edda.domain import AudioTimeRef, Chart, HitEvent


def test_roundtrip_and_independent_charts():
    profile = TargetProfile(version=1, name="test", target_hits_per_second=3)
    assert TargetProfile.model_validate_json(profile.model_dump_json()) == profile
    a = Chart(events=(HitEvent(time_ref=AudioTimeRef(kind="audio", time_sec=1)),))
    b = Chart(events=(HitEvent(time_ref=AudioTimeRef(kind="audio", time_sec=2)),))
    assert a.events[0].id != b.events[0].id
    assert Chart.model_validate_json(b.model_dump_json()) == b


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_numbers(value):
    with pytest.raises(ValidationError):
        TargetProfile(version=1, name="test", target_hits_per_second=value)


def test_invalid_ranges_and_path():
    with pytest.raises(ValidationError):
        Section(start_sec=2, end_sec=1)
    with pytest.raises(ValidationError):
        FeatureManifestRef(path="../x", sha256="a" * 64, version="1")
    with pytest.raises(ValidationError):
        TargetProfile(version=1, name="x", double_event_fraction=2)


@pytest.mark.parametrize("subdivisions", [(True,), ("2",), (3.0,), (0,), (-1,)])
def test_subdivisions_reject_coercion_and_nonpositive_values(subdivisions):
    with pytest.raises(ValidationError):
        TargetProfile(version=1, name="x", allowed_subdivisions=subdivisions)


@pytest.mark.parametrize(
    "raw",
    [
        '{"version":1,"name":"x","allowed_subdivisions":[true]}',
        '{"version":1,"name":"x","allowed_subdivisions":["2"]}',
        '{"version":1,"name":"x","allowed_subdivisions":[3.0]}',
    ],
)
def test_json_subdivisions_reject_coercion(raw):
    with pytest.raises(ValidationError):
        TargetProfile.model_validate_json(raw)


@pytest.mark.parametrize("subdivisions", [[1, 2], (1, 2)])
def test_subdivisions_accept_list_and_tuple(subdivisions):
    assert TargetProfile(
        version=1, name="x", allowed_subdivisions=subdivisions
    ).allowed_subdivisions == (1, 2)


def test_json_subdivisions_accept_array():
    profile = TargetProfile.model_validate_json(
        '{"version":1,"name":"x","allowed_subdivisions":[1,2]}'
    )
    assert profile.allowed_subdivisions == (1, 2)


def _request(ref_overrides=None):
    refs = {
        "analysis": {"id": "a", "revision": 0},
        "plan": {"id": "p", "revision": 1},
        "chart": {"id": "c", "revision": 2},
    }
    refs.update(ref_overrides or {})
    return ArrangementRequest(
        project_id="project",
        generator_id="gen",
        generator_version="1",
        audio_asset_id="audio",
        audio_sha256="a" * 64,
        grid_revision=0,
        target=TargetProfile(version=1, name="x"),
        range_q={"start_q": 0, "end_q": 4},
        mode="heuristic_arranger",
        seed=4,
        **refs,
    )


@pytest.mark.parametrize(
    "field,bad_ref",
    [
        ("analysis", {"id": "", "revision": 0}),
        ("plan", {"id": "p", "revision": True}),
        ("chart", {"id": "c", "revision": "2"}),
        ("analysis", {"id": "a", "revision": 1.0}),
        ("reference_chart", {"id": "c", "revision": False}),
    ],
)
def test_arrangement_references_are_strict(field, bad_ref):
    overrides = {field: bad_ref}
    if field == "reference_chart":
        overrides["reference_influence"] = "pattern_theme"
    with pytest.raises(ValidationError):
        _request(overrides)


def test_valid_full_arrangement_request():
    request = _request(
        {"reference_chart": {"id": "ref", "revision": 3}, "reference_influence": "rhythm_theme"}
    )
    assert request.analysis.id == "a" and request.chart.revision == 2


@pytest.mark.parametrize("start,end", [(2, 1), (1, 1), (float("nan"), 2), (1, float("inf"))])
def test_invalid_quarter_note_ranges(start, end):
    with pytest.raises(ValidationError):
        ArrangementRangeQ(start_q=start, end_q=end)
