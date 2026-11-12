import importlib.util
import weakref
from dataclasses import replace
from pathlib import Path

import pytest
from test_native_generation_review import (
    generation as generation,
)
from test_native_generation_review import (
    inputs as inputs,
)
from test_native_generation_review import (
    media as media,
)
from test_native_generation_review import (
    read,
    write,
)
from test_native_inputs_review import validate as input_records
from test_native_plans import publish
from test_native_plans_review import rehash

from ai_edda.native_history import _build_native_history_index

ROOT = Path(__file__).resolve().parents[2]


def staged(name):
    path = ROOT / "src/ai_edda" / (name + ".py")
    spec = importlib.util.spec_from_file_location("ai_edda.staged_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def index_for(data):
    return _build_native_history_index(
        data["root"], data["history"].materialize_current().project.id
    )


def test_real_plan_and_noncanonical_grid(inputs):
    module = staged("native_plans")
    plan = publish(inputs)
    records, _ = input_records(inputs)
    index = index_for(inputs)
    assert module.validate_native_plans(inputs["root"], index, inputs["analysis_map"], records) == {
        plan.id: plan
    }
    rehash(inputs, plan, lambda value: value["identity"]["grid"].pop("anchors"))
    with pytest.raises(ValueError, match="Unknown grid"):
        module.validate_native_plans(inputs["root"], index, inputs["analysis_map"], records)


def test_real_generation_checks_every_draft_and_chart_fingerprint(generation, monkeypatch):
    module = staged("native_generation")
    index = index_for(generation)
    seen = []
    original = module._check_generation_reassembly

    def check(draft_id, request, context, draft, draft_value, **kwargs):
        seen.append(draft_id)
        if draft_id == generation["draft_id"]:
            assert "INVALID_EVENT_SIZE" in {
                item["code"] for item in draft_value["validation"]
            }
        return original(draft_id, request, context, draft, draft_value, **kwargs)

    monkeypatch.setattr(module, "_check_generation_reassembly", check)
    args = (
        generation["root"],
        index,
        generation["analysis_map"],
        generation["plans_map"],
        generation["targets_map"],
    )
    assert module.validate_native_generation(*args) is None
    assert set(seen) == {generation["draft_id"], generation["rejected_id"]}
    chart = generation["source"].charts[0]
    key = (chart.id, chart.revision)
    index.charts[key] = replace(index.charts[key], fingerprint="0" * 64)
    with pytest.raises(ValueError, match="Unknown historical source"):
        module.validate_native_generation(*args)


def test_rejected_draft_is_checked(generation):
    module = staged("native_generation")
    path = generation["root"] / "generation" / generation["rejected_id"] / "rejection.json"
    value = read(path)
    value["draft_sha256"] = "0" * 64
    write(path, value)
    with pytest.raises(ValueError):
        module.validate_native_generation(
            generation["root"],
            index_for(generation),
            generation["analysis_map"],
            generation["plans_map"],
            generation["targets_map"],
        )


def test_changed_embedded_grid_is_rejected(inputs):
    module = staged("native_plans")
    plan = publish(inputs)
    records, _ = input_records(inputs)
    index = index_for(inputs)
    rehash(
        inputs,
        plan,
        lambda value: value["identity"]["grid"]["segments"][0].update({"bpm_quarter": 17}),
    )
    with pytest.raises(ValueError, match="Unknown grid"):
        module.validate_native_plans(inputs["root"], index, inputs["analysis_map"], records)


def test_malformed_embedded_grid_rejects_before_aggregate_error_allocation(inputs):
    from pydantic import ValidationError

    module = staged("native_plans")
    plan = publish(inputs)
    records, _ = input_records(inputs)
    index = index_for(inputs)
    rehash(
        inputs,
        plan,
        lambda value: value["identity"]["grid"].update(segments=[{} for _ in range(1000)]),
    )
    with pytest.raises(ValueError) as caught:
        module.validate_native_plans(inputs["root"], index, inputs["analysis_map"], records)
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() <= 4


def test_generation_releases_draft_events_before_next_directory(generation, monkeypatch):
    module = staged("native_generation")
    index = index_for(generation)
    original_validate = module.ChartDraft.model_validate
    original_read = module.read_native_history_json
    events = []
    previous_directory = None

    def validated(value):
        draft = original_validate(value)
        events.extend(
            weakref.ref(event)
            for event in (*draft.chart.events, *draft.generation_run.output.events)
        )
        return draft

    def read(path, **kwargs):
        nonlocal previous_directory
        if previous_directory is not None and path.parent != previous_directory:
            assert all(ref() is None for ref in events), (
                "Previous draft event remains live at next directory"
            )
        previous_directory = path.parent
        return original_read(path, **kwargs)

    monkeypatch.setattr(module.ChartDraft, "model_validate", validated)
    monkeypatch.setattr(module, "read_native_history_json", read)
    module.validate_native_generation(
        generation["root"],
        index,
        generation["analysis_map"],
        generation["plans_map"],
        generation["targets_map"],
    )
    assert all(ref() is None for ref in events)


def test_staged_plan_generation_use_keyed_metadata_without_source_loads(generation, monkeypatch):
    index = index_for(generation)

    def unexpected(*args, **kwargs):
        pytest.fail("Adapter loaded an indexed chart/grid model")

    monkeypatch.setattr(index, "load_chart", unexpected)
    monkeypatch.setattr(index, "load_grid", unexpected)
    monkeypatch.setattr(index, "materialize_current", unexpected)
    plans = staged("native_plans").validate_native_plans(
        generation["root"], index, generation["analysis_map"], generation["inputs_map"]
    )
    assert (
        staged("native_generation").validate_native_generation(
            generation["root"], index, generation["analysis_map"], plans, generation["targets_map"]
        )
        is None
    )


@pytest.mark.parametrize("name", ["native_plans", "native_generation"])
def test_staged_adapter_runtime_cancellation_identity(generation, name):
    module = staged(name)
    index = index_for(generation)
    error = RuntimeError("cancel")

    def cancel():
        raise error

    args = (
        (generation["analysis_map"], generation["inputs_map"])
        if name == "native_plans"
        else (generation["analysis_map"], generation["plans_map"], generation["targets_map"])
    )
    with pytest.raises(RuntimeError) as caught:
        getattr(module, "validate_" + name)(generation["root"], index, *args, cancel=cancel)
    assert caught.value is error
