"""Independent resource checks for bounded native generation validation."""

import json
import weakref
from collections import Counter
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_native_generation_review import generation as generation
from test_native_generation_review import inputs as inputs
from test_native_generation_review import media as media

from ai_edda import native_generation as module


def validate(data, cancel=None):
    return module.validate_native_generation(
        data["root"],
        data["history"],
        data["analysis_map"],
        data["plans_map"],
        data["targets_map"],
        cancel,
    )


@pytest.mark.parametrize(
    "location",
    [
        "context_chart_events",
        "context_chart_notes",
        "context_grid_segments",
        "draft_chart_events",
        "draft_output_events",
        "request_subdivisions",
    ],
)
def test_large_malformed_collections_reject_one_item(generation, location):
    directory = generation["root"] / "generation" / generation["draft_id"]
    filename = "draft.json" if location.startswith("draft_") else "context.json"
    path = directory / filename
    raw = json.loads(path.read_bytes())
    if location == "context_chart_events":
        raw["context"]["chart"]["events"] = [{} for _ in range(1000)]
    elif location == "context_chart_notes":
        raw["context"]["chart"]["events"][0]["notes"] = [{} for _ in range(1000)]
    elif location == "context_grid_segments":
        raw["context"]["grid"]["segments"] = [{} for _ in range(1000)]
    elif location == "draft_chart_events":
        raw["chart"]["events"] = [{} for _ in range(1000)]
    elif location == "draft_output_events":
        raw["generation_run"]["output"]["events"] = [{} for _ in range(1000)]
    else:
        raw["request"]["target"]["allowed_subdivisions"] = [None] * 1000
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        validate(generation)
    # Fixed-size errors for one malformed item are acceptable. A plain
    # ValueError from an earlier schema-local guard is also acceptable.
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() <= 8


@pytest.mark.parametrize("form", ["notes", "note_sources"])
def test_output_notes_reject_large_malformed_collections_at_first_item(generation, form):
    path = generation["root"] / "generation" / generation["draft_id"] / "draft.json"
    raw = json.loads(path.read_bytes())
    output = raw["generation_run"]["output"]
    event = module.HitEvent(
        id=str(uuid4()),
        time_ref={"kind": "audio", "time_sec": 0},
        notes=(module.ChartNote(id=str(uuid4()), lane=0),),
    ).model_dump(mode="json")
    output["events"] = [event]
    if form == "notes":
        event["notes"] = [{} for _ in range(1000)]
    else:
        event["notes"][0]["provenance"]["source_ids"] = [None] * 1000
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        validate(generation)
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() <= 8


@pytest.mark.parametrize(
    "time_ref",
    [
        {"kind": "audio", "time_sec": 0},
        {"kind": "beat", "beat_q": 0, "micro_offset_sec": 0},
    ],
)
def test_output_time_reference_rejects_wide_map_before_union_errors(generation, time_ref):
    path = generation["root"] / "generation" / generation["draft_id"] / "draft.json"
    raw = json.loads(path.read_bytes())
    event = module.HitEvent(
        id=str(uuid4()),
        time_ref=time_ref,
        notes=(module.ChartNote(id=str(uuid4()), lane=0),),
    ).model_dump(mode="json")
    event["time_ref"].update({f"unknown_{n}": None for n in range(1000)})
    raw["generation_run"]["output"]["events"] = [event]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        validate(generation)
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() <= 8


@pytest.mark.parametrize("location", ["request", "run_request", "output"])
def test_each_target_subdivision_loop_preserves_cancellation_identity(
    generation, monkeypatch, location
):
    original_reader = module.read_native_history_json
    active = False

    class ObservedList(list):
        def __iter__(self):
            nonlocal active
            active = True
            try:
                yield from super().__iter__()
            finally:
                active = False

    def reader(path, **kwargs):
        raw = original_reader(path, **kwargs)
        if location == "request" and path.name == "context.json":
            target = raw["request"]["target"]
        elif location == "run_request" and path.name == "draft.json":
            target = raw["generation_run"]["request"]["target"]
        elif location == "output" and path.name == "draft.json":
            target = raw["generation_run"]["output"]["target"]
        else:
            return raw
        target["allowed_subdivisions"] = ObservedList(target["allowed_subdivisions"])
        return raw

    monkeypatch.setattr(module, "read_native_history_json", reader)
    for error_type in (OSError, KeyError, RecursionError):
        error = error_type("cancel during target subdivision check")

        def cancel():
            if active:
                raise error

        with pytest.raises(error_type) as caught:
            validate(generation, cancel)
        assert caught.value is error


def test_native_generation_parses_every_record_once(generation, monkeypatch):
    original_reader = module.read_native_history_json
    original_loads = json.loads
    reads = Counter()
    parses = []
    expected = {p for p in (generation["root"] / "generation").rglob("*.json")}

    def reader(path, **kwargs):
        reads[path] += 1
        return original_reader(path, **kwargs)

    def loads(*args, **kwargs):
        parses.append(None)
        return original_loads(*args, **kwargs)

    monkeypatch.setattr(module, "read_native_history_json", reader)
    monkeypatch.setattr(json, "loads", loads)
    assert validate(generation) is None
    assert reads == Counter({path: 1 for path in expected})
    assert len(parses) == len(expected)


def test_models_and_raw_inputs_are_released_before_publication_and_next_entry(
    generation, monkeypatch
):
    original_reader = module.read_native_history_json
    models = []
    raw_inputs = []
    publication_inputs = []
    previous_directory = None

    class TrackedDict(dict):
        pass

    for model_type in (module.ArrangementRequest, module.ArrangementContext, module.ChartDraft):
        original = model_type.model_validate

        def observed(value, original=original):
            model = original(value)
            models.append(weakref.ref(model))
            charts = [getattr(model, name, None) for name in ("chart", "reference_chart")]
            for chart in charts:
                if chart is not None and hasattr(chart, "events"):
                    models.append(weakref.ref(chart))
                    models.extend(weakref.ref(event) for event in chart.events)
            return model

        monkeypatch.setattr(model_type, "model_validate", observed)

    def reader(path, **kwargs):
        nonlocal previous_directory
        if previous_directory is not None and path.parent != previous_directory:
            assert all(ref() is None for ref in models + raw_inputs + publication_inputs)
        previous_directory = path.parent
        if path.name == "publication.json":
            assert all(ref() is None for ref in models + raw_inputs)
        value = TrackedDict(original_reader(path, **kwargs))
        (raw_inputs if path.name in ("context.json", "draft.json") else publication_inputs).append(
            weakref.ref(value)
        )
        return value

    monkeypatch.setattr(module, "read_native_history_json", reader)
    assert validate(generation) is None
    assert all(ref() is None for ref in models + raw_inputs + publication_inputs)


@pytest.mark.parametrize(
    "stage",
    [
        "context_read",
        "draft_read",
        "publication_read",
        "rejection_read",
        "before_reassembly",
        "after_reassembly",
        "publication_check",
        "digest",
    ],
)
def test_cancellation_identity_across_generation_phases(generation, monkeypatch, stage):
    state = {"active": None, "reader": False, "reassembly_checks": 0}
    original_reader = module.read_native_history_json
    original_reassembly = module._check_generation_reassembly
    original_publication = module._check_generation_publication
    from pathlib import Path

    original_open = Path.open

    def reader(path, **kwargs):
        labels = {
            "context.json": "context_read",
            "draft.json": "draft_read",
            "publication.json": "publication_read",
            "rejection.json": "rejection_read",
        }
        state["active"], state["reader"] = labels[path.name], True
        try:
            return original_reader(path, **kwargs)
        finally:
            state["active"], state["reader"] = None, False

    def reassembly(*args, **kwargs):
        state["active"] = "reassembly"
        state["reassembly_checks"] = 0
        try:
            return original_reassembly(*args, **kwargs)
        finally:
            state["active"] = None

    def publication(*args, **kwargs):
        state["active"] = "publication_check"
        try:
            return original_publication(*args, **kwargs)
        finally:
            state["active"] = None

    class DigestStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            state["active"] = "digest"
            return self.stream

        def __exit__(self, *args):
            state["active"] = None
            return self.stream.__exit__(*args)

    def opened(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.name in ("context.json", "draft.json") and not state["reader"]:
            return DigestStream(stream)
        return stream

    monkeypatch.setattr(module, "read_native_history_json", reader)
    monkeypatch.setattr(module, "_check_generation_reassembly", reassembly)
    monkeypatch.setattr(module, "_check_generation_publication", publication)
    monkeypatch.setattr(Path, "open", opened)
    for error_type in (OSError, TypeError, KeyError, RecursionError):
        error = error_type("cancel during " + stage)

        def cancel():
            active = state["active"]
            if active == "reassembly":
                state["reassembly_checks"] += 1
                stop = 1 if stage == "before_reassembly" else 2
                if (
                    stage in ("before_reassembly", "after_reassembly")
                    and state["reassembly_checks"] == stop
                ):
                    raise error
            elif active == stage:
                raise error

        with pytest.raises(error_type) as caught:
            validate(generation, cancel)
        assert caught.value is error
