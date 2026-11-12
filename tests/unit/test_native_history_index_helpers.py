import hashlib
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from ai_edda.domain import (
    AudioTimeRef,
    Chart,
    ChartNote,
    HitEvent,
    Provenance,
    TempoSegment,
    TimingGrid,
)
from ai_edda.native_history_index import DEFAULT_PROFILE, NativeHistoryLimits, model_fingerprint
from ai_edda.native_history_reader import NativeHistoryReadLimits


def canonical(model):
    data = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sample_chart():
    return Chart(
        id="chart",
        events=(
            HitEvent(
                id="a",
                time_ref=AudioTimeRef(kind="audio", time_sec=1),
                notes=(ChartNote(id="n", lane=1),),
            ),
            HitEvent(id="b", time_ref=AudioTimeRef(kind="audio", time_sec=2)),
        ),
    )


def test_canonical_and_unchanged():
    chart = sample_chart()
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    for model in (chart, grid):
        before = model.model_dump(mode="json")
        assert model_fingerprint(model) == canonical(model)
        assert model_fingerprint(type(model).model_validate(before)) == canonical(model)
        assert model.model_dump(mode="json") == before


def test_changes_affect_fingerprint():
    chart = sample_chart()
    event = chart.events[0]
    variants = [
        chart.model_copy(update={"events": tuple(reversed(chart.events))}),
        chart.model_copy(update={"provenance": Provenance(operation_id="é")}),
        chart.model_copy(
            update={
                "events": (
                    event.model_copy(
                        update={"notes": (event.notes[0].model_copy(update={"lane": 2}),)}
                    ),
                    chart.events[1],
                )
            }
        ),
    ]
    assert all(model_fingerprint(item) != model_fingerprint(chart) for item in variants)


@pytest.mark.parametrize("escaped", [r"\ud800", r"\udfff"])
def test_fingerprint_accepts_lone_surrogates_and_preserves_distinctions(escaped):
    name = json.loads('"' + escaped + '"')
    assert len(name) == 1 and 0xD800 <= ord(name) <= 0xDFFF
    chart = Chart(id="fixed", metadata={"name": name})
    assert model_fingerprint(chart) == model_fingerprint(
        Chart.model_validate(chart.model_dump(mode="json"))
    )
    assert model_fingerprint(chart) != model_fingerprint(Chart(id="fixed", metadata={"name": "é"}))
    assert model_fingerprint(chart) != model_fingerprint(
        Chart(id="fixed", metadata={"name": escaped})
    )


def test_high_and_low_surrogates_have_distinct_fingerprints():
    names = [json.loads(r'"\ud800"'), json.loads(r'"\udfff"'), "é"]
    assert (
        len({model_fingerprint(Chart(id="fixed", metadata={"name": name})) for name in names}) == 3
    )


def test_unicode_fingerprint_keeps_canonical_utf8_hash():
    chart = Chart(id="fixed", metadata={"name": "é"})
    assert model_fingerprint(chart) == canonical(chart)


def test_profile():
    assert DEFAULT_PROFILE == NativeHistoryLimits(
        NativeHistoryReadLimits(), 2_000_000, 64 * 1024 * 1024, 5_000_000, 16 * 1024 * 1024, 100_000
    )
    with pytest.raises(FrozenInstanceError):
        DEFAULT_PROFILE.max_chart_nodes = 1


@pytest.mark.parametrize(
    "name",
    [
        "max_chart_nodes",
        "max_current_bytes",
        "max_current_nodes",
        "max_index_bytes",
        "max_index_chart_refs",
    ],
)
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "2"])
def test_profile_rejects_invalid_numeric_limits(name, value):
    with pytest.raises((TypeError, ValueError)):
        NativeHistoryLimits(**{name: value})


def test_profile_rejects_invalid_read_limits():
    with pytest.raises(TypeError):
        NativeHistoryLimits(read_limits=None)


def test_profile_accepts_small_injected_limits():
    profile = NativeHistoryLimits(NativeHistoryReadLimits(), 1, 2, 3, 4, 5)
    assert (
        profile.max_chart_nodes,
        profile.max_current_bytes,
        profile.max_current_nodes,
        profile.max_index_bytes,
        profile.max_index_chart_refs,
    ) == (1, 2, 3, 4, 5)


@pytest.mark.parametrize("stop", [1, 2, 3, 10])
def test_cancellation_identity(stop):
    error = RuntimeError("cancel")
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == stop:
            raise error

    with pytest.raises(RuntimeError) as caught:
        model_fingerprint(sample_chart(), cancel=cancel)
    assert caught.value is error


def test_validated_chart_limits_and_cancellation():
    from ai_edda.native_history_index import NativeHistoryLimits, validated_chart

    raw = {"id": "00000000-0000-0000-0000-000000000001", "revision": 0, "grid_revision": 0}
    assert validated_chart(raw).events == ()
    with pytest.raises(ValueError):
        validated_chart(raw, limits=NativeHistoryLimits(max_chart_nodes=10))
    error = TypeError("cancel")

    def cancel():
        raise error

    with pytest.raises(TypeError) as caught:
        validated_chart(raw, cancel=cancel)
    assert caught.value is error
    assert "events" not in raw


def test_validated_chart_header_budget_exact_boundary_and_early_rejection():
    from ai_edda.native_history_index import validated_chart

    uid = "00000000-0000-0000-0000-000000000001"
    raw = {"id": uid, "revision": 0, "grid_revision": 0}

    # Count the normalized JSON independently: each value, container, and key
    # consumes one node. The empty chart has 37 nodes.
    def nodes(value):
        if isinstance(value, dict):
            return 1 + sum(1 + nodes(child) for child in value.values())
        if isinstance(value, list):
            return 1 + sum(nodes(child) for child in value)
        return 1

    normalized_nodes = nodes(Chart.model_validate(raw).model_dump(mode="json"))
    assert normalized_nodes == 37
    with pytest.raises(ValueError, match="node count"):
        validated_chart(raw, limits=NativeHistoryLimits(max_chart_nodes=normalized_nodes - 1))
    assert (
        validated_chart(raw, limits=NativeHistoryLimits(max_chart_nodes=normalized_nodes)).events
        == ()
    )


def bounded_chart_input():
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "revision": 0,
        "grid_revision": 0,
        "events": [
            {
                "id": "00000000-0000-0000-0000-000000000002",
                "time_ref": {"kind": "audio", "time_sec": -1},
                "notes": [{"id": "00000000-0000-0000-0000-000000000003", "lane": 0}],
            }
        ],
    }


def test_chart_header_budget_rejects_before_note_validation(monkeypatch):
    from ai_edda import native_history_index as index

    def unexpected_note(*args, **kwargs):
        pytest.fail("Note validation ran although the empty header exceeds the budget")

    monkeypatch.setattr(index.ChartNote, "model_validate", unexpected_note)
    with pytest.raises(ValueError, match="node count"):
        index.validated_chart(bounded_chart_input(), limits=NativeHistoryLimits(max_chart_nodes=36))


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("metadata",),
        ("provenance",),
        ("target_profile",),
        ("events", 0),
        ("events", 0, "provenance"),
        ("events", 0, "locks"),
        ("events", 0, "time_ref"),
        ("events", 0, "notes", 0),
        ("events", 0, "notes", 0, "provenance"),
        ("events", 0, "notes", 0, "locks"),
    ],
)
def test_chart_wide_malformed_records_stop_at_first_error(path):
    from pydantic import ValidationError

    from ai_edda.native_history_index import validated_chart

    raw = bounded_chart_input()
    raw["target_profile"] = {"id": raw["id"], "revision": 0}
    target = raw
    for part in path:
        if isinstance(part, int):
            target = target[part]
        else:
            target = target.setdefault(part, {})
    target.update({f"unknown_{i}": None for i in range(1000)})
    with pytest.raises(ValueError) as caught:
        validated_chart(raw)
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() == 1


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("events", 0),
        ("events", 0, "notes", 0),
    ],
)
def test_chart_malformed_source_ids_stop_at_first_error(path):
    from pydantic import ValidationError

    from ai_edda.native_history_index import validated_chart

    raw = bounded_chart_input()
    target = raw
    for part in path:
        target = target[part]
    target["provenance"] = {"source_ids": [None] * 1000}
    with pytest.raises(ValueError) as caught:
        validated_chart(raw)
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() == 1


def test_chart_guards_preserve_coercion_order_and_raw_input():
    from ai_edda.native_history_index import validated_chart

    raw = bounded_chart_input()
    raw["metadata"] = {"rank": "3", "speed": "1.5"}
    raw["events"][0]["locks"] = {"time": "true"}
    raw["events"][0]["notes"][0]["provenance"] = {
        "created_by": "imported",
        "source_ids": ["a", "b"],
    }
    before = deepcopy(raw)
    assert validated_chart(raw) == Chart.model_validate(raw)
    assert raw == before


def test_validated_chart_rejects_duplicate_and_bad_references():
    from ai_edda.native_history_index import validated_chart

    uid = "00000000-0000-0000-0000-000000000001"
    event = {"id": uid, "time_ref": {"kind": "audio", "time_sec": 0}}
    raw = {"id": uid, "revision": 0, "grid_revision": 0, "events": [event, event]}
    with pytest.raises(ValueError):
        validated_chart(raw)
    raw["events"] = [
        {
            "id": uid,
            "time_ref": {"kind": "audio", "time_sec": 0},
            "notes": [{"id": "bad", "lane": 0}],
        }
    ]
    with pytest.raises(ValueError):
        validated_chart(raw)
    raw["revision"] = True
    with pytest.raises(ValueError):
        validated_chart(raw)


def test_validated_grid_boundaries_and_semantics():
    from ai_edda.native_history_index import validated_grid

    raw = {
        "revision": 0,
        "segments": [
            {"start_time_sec": "0", "start_beat_q": 0, "bpm_quarter": "120", "grid_division": 4}
        ],
    }
    before = deepcopy(raw)

    def nodes(v):
        if isinstance(v, dict):
            return 1 + sum(1 + nodes(x) for x in v.values())
        if isinstance(v, list):
            return 1 + sum(nodes(x) for x in v)
        return 1

    size = nodes(TimingGrid.model_validate(raw).model_dump(mode="json"))
    assert validated_grid(
        raw, limits=NativeHistoryLimits(read_limits=NativeHistoryReadLimits(max_nodes=size))
    ) == TimingGrid.model_validate(raw)
    with pytest.raises(ValueError, match="node count"):
        validated_grid(
            raw, limits=NativeHistoryLimits(read_limits=NativeHistoryReadLimits(max_nodes=size - 1))
        )
    assert raw == before
    raw["segments"].append(
        {"start_time_sec": 5, "start_beat_q": 1, "bpm_quarter": 120, "grid_division": 4}
    )
    with pytest.raises(ValueError, match="GRID_DISCONTINUITY"):
        validated_grid(raw)


def test_validated_grid_rejects_wide_fields_and_preserves_cancel():
    from ai_edda.native_history_index import validated_grid

    raw = {
        "revision": 0,
        "segments": [
            {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4}
        ],
    }
    raw["segments"][0].update({f"extra{i}": 1 for i in range(1000)})
    with pytest.raises(ValueError, match="Unexpected grid field"):
        validated_grid(raw)
    error = RuntimeError("cancel")

    def cancel():
        raise error

    with pytest.raises(RuntimeError) as caught:
        validated_grid(raw, cancel=cancel)
    assert caught.value is error


def grid_input_with_anchors():
    return {
        "revision": 0,
        "segments": [
            {
                "start_time_sec": "0",
                "start_beat_q": "0",
                "bpm_quarter": "120",
                "grid_division": "4",
            },
            {"start_time_sec": "2", "start_beat_q": "4", "bpm_quarter": "60", "grid_division": "4"},
        ],
        "meter": {"numerator": "3", "denominator": "4", "bar_origin_beat_q": "0"},
        # Anchor order has no ordering constraint in the domain model.
        "anchors": [{"beat_q": "5", "time_sec": "3"}, {"beat_q": "0", "time_sec": "0"}],
    }


def test_grid_collections_preserve_order_coercion_and_exact_node_boundary():
    from ai_edda.native_history_index import validated_grid

    raw = grid_input_with_anchors()
    before = deepcopy(raw)
    expected = TimingGrid.model_validate(raw)

    def nodes(value):
        if isinstance(value, dict):
            return 1 + sum(1 + nodes(child) for child in value.values())
        if isinstance(value, list):
            return 1 + sum(nodes(child) for child in value)
        return 1

    size = nodes(expected.model_dump(mode="json"))
    limits = NativeHistoryLimits(read_limits=NativeHistoryReadLimits(max_nodes=size))
    assert validated_grid(raw, limits=limits) == expected
    assert [anchor.beat_q for anchor in validated_grid(raw).anchors] == [5, 0]
    with pytest.raises(ValueError, match="node count"):
        validated_grid(
            raw, limits=NativeHistoryLimits(read_limits=NativeHistoryReadLimits(max_nodes=size - 1))
        )
    assert raw == before


@pytest.mark.parametrize("path", [(), ("meter",), ("segments", 0), ("anchors", 0)])
def test_grid_wide_objects_reject_first_schema_error(path):
    from pydantic import ValidationError

    from ai_edda.native_history_index import validated_grid

    raw = grid_input_with_anchors()
    target = raw
    for part in path:
        target = target[part]
    target.update({f"unknown_{i}": 0 for i in range(1000)})
    with pytest.raises(ValueError) as caught:
        validated_grid(raw)
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() == 1


@pytest.mark.parametrize(
    "collection,model_name", [("segments", "TempoSegment"), ("anchors", "GridAnchor")]
)
def test_grid_bad_collection_stops_after_first_item(monkeypatch, collection, model_name):
    from pydantic import ValidationError

    from ai_edda import native_history_index as index

    raw = grid_input_with_anchors()
    raw[collection] = [{} for _ in range(1000)]
    model = getattr(index, model_name)
    original = model.model_validate
    calls = []

    def observed(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(model, "model_validate", observed)
    with pytest.raises(ValidationError) as caught:
        index.validated_grid(raw)
    assert len(calls) == 1
    assert caught.value.error_count() <= 4


def test_grid_header_budget_rejects_before_collection_models(monkeypatch):
    from ai_edda import native_history_index as index

    raw = {
        "revision": 0,
        "segments": [
            {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4},
        ],
    }

    def unexpected_segment(*args, **kwargs):
        pytest.fail("Segment validation ran although the normalized header exceeds the budget")

    monkeypatch.setattr(index.TempoSegment, "model_validate", unexpected_segment)
    with pytest.raises(ValueError, match="node count"):
        index.validated_grid(
            raw, limits=NativeHistoryLimits(read_limits=NativeHistoryReadLimits(max_nodes=16))
        )


@pytest.mark.parametrize("revision", [True, False, "0", 0.0, -1])
def test_grid_revision_stays_strict(revision):
    from ai_edda.native_history_index import validated_grid

    raw = grid_input_with_anchors()
    raw["revision"] = revision
    with pytest.raises(ValueError, match="revision"):
        validated_grid(raw)


def test_grid_inconsistent_anchor_uses_domain_rule():
    from ai_edda.native_history_index import validated_grid

    raw = grid_input_with_anchors()
    raw["anchors"][0]["time_sec"] = 4
    with pytest.raises(ValueError, match="GRID_ANCHOR_INCONSISTENT"):
        validated_grid(raw)


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, RecursionError])
def test_grid_cancellation_identity_through_collections(error_type):
    from ai_edda.native_history_index import validated_grid

    raw = grid_input_with_anchors()
    checkpoints = []
    validated_grid(raw, cancel=lambda: checkpoints.append(None))
    for stop in (1, len(checkpoints) // 2, len(checkpoints)):
        calls = 0
        error = error_type("cancel")

        def cancel():
            nonlocal calls
            calls += 1
            if calls == stop:
                raise error

        with pytest.raises(error_type) as caught:
            validated_grid(raw, cancel=cancel)
        assert caught.value is error
