"""Verify historical sources and content independently of stored identity hashes."""

import json
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

import pytest
from test_native_inputs_review import inputs as inputs
from test_native_inputs_review import validate as input_records
from test_native_media_review import media as media
from test_native_media_review import read, write
from test_native_plans import publish

from ai_edda.application import ApplicationService
from ai_edda.domain import ObjectRef
from ai_edda.musical_plan import ProjectionConfig
from ai_edda.native_history import validate_native_history
from ai_edda.native_plans import validate_native_plans
from ai_edda.plan_catalog import resolve_plan
from ai_edda.project_store import ProjectStore


def validate(inputs):
    records, _ = input_records(inputs)
    return validate_native_plans(inputs["root"], inputs["history"], inputs["analysis_map"], records)


def publish_for_snapshot(inputs, snapshot, config=ProjectionConfig()):
    analysis = inputs["analysis_map"][inputs["analysis_id"]]
    project = snapshot.project.model_copy(
        update={
            "audio": analysis.audio,
            "analysis": ObjectRef(id=analysis.id, revision=analysis.revision),
        }
    )
    return resolve_plan(inputs["root"], replace(snapshot, project=project), config)


def rehash(inputs, plan, mutate):
    path = inputs["root"] / "plans" / f"{plan.id}.json"
    value = read(path)
    mutate(value)
    content = {k: v for k, v in value["plan"].items() if k != "id"}
    canonical = json.dumps(
        {"identity": value["identity"], "plan": content},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    value["plan"]["id"] = str(uuid5(NAMESPACE_URL, canonical))
    path.unlink()
    write(path.with_name(value["plan"]["id"] + ".json"), value)


def test_historical_scoped_and_orphan_plans_survive_new_grid(inputs):
    head = next(
        s
        for s in inputs["history"].heads
        if s.project.plan_inputs is not None and s.project.plan_inputs.id == inputs["old"].id
    )
    from ai_edda.project_store import ProjectSnapshot

    index = inputs["history"]
    old = ProjectSnapshot(
        head.project,
        index.load_grid(head.project.grid_revision),
        tuple(index.load_chart(r.id, r.revision) for r in head.project.chart_heads),
    )
    historical = publish_for_snapshot(inputs, old)
    orphan_snapshot = replace(
        inputs["history"].materialize_current(),
        project=inputs["history"]
        .materialize_current()
        .project.model_copy(update={"plan_inputs": None}),
    )
    orphan = publish_for_snapshot(
        inputs, orphan_snapshot, ProjectionConfig(min_rms=0, support_window_sec=0)
    )
    with ProjectStore(inputs["root"].parent) as store:
        app = ApplicationService(store)
        view = app.get(inputs["history"].materialize_current().project.id)
        changed = view.grid.model_dump(mode="json")
        changed["segments"][0]["bpm_quarter"] = 240
        app.execute(
            view.project.id,
            {
                "command_id": "change-grid",
                "kind": "apply_grid",
                "payload": {"grid": changed},
                "base_project_revision": view.project.revision,
                "expected_grid_revision": view.grid.revision,
                "expected_chart_revisions": {c.id: c.revision for c in view.charts},
            },
        )
        inputs["history"] = validate_native_history(inputs["root"], view.project.id)
    before = {p: p.read_bytes() for p in inputs["root"].rglob("*") if p.is_file()}
    assert validate(inputs) == {historical.id: historical, orphan.id: orphan}
    assert historical.musical_anchors == inputs["old"].anchors
    assert historical.grid_revision != inputs["history"].materialize_current().grid.revision
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize(
    "location",
    [
        "analysis_revision",
        "analysis_audio_channels",
        "grid_revision",
        "inputs_revision",
        "inputs_analysis_revision",
        "plan_analysis_revision",
    ],
)
def test_rehashed_identity_cannot_hide_nested_revision_or_metadata_coercion(inputs, location):
    snapshot = inputs["history"].materialize_current()
    if location == "grid_revision":
        snapshot = replace(snapshot, grid=inputs["history"].load_grid(0))
    plan = publish_for_snapshot(inputs, snapshot)
    assert plan.id in validate(inputs)

    def mutate(value):
        identity = value["identity"]
        if location == "analysis_revision":
            identity["analysis"]["revision"] = False
        elif location == "analysis_audio_channels":
            identity["analysis"]["audio"]["channels"] = True
        elif location == "grid_revision":
            assert identity["grid"]["revision"] == 0
            identity["grid"]["revision"] = False
        elif location == "inputs_revision":
            identity["plan_inputs"]["revision"] = False
        elif location == "inputs_analysis_revision":
            identity["plan_inputs"]["analysis"]["revision"] = False
        else:
            value["plan"]["analysis"]["revision"] = False

    rehash(inputs, plan, mutate)
    with pytest.raises(ValueError):
        validate(inputs)


@pytest.mark.parametrize(
    "location", ["section", "anchors", "grid", "analysis", "algorithm", "input_ref"]
)
def test_rehashed_bad_plan_or_identity_is_rejected(inputs, location):
    plan = publish(inputs)

    def mutate(value):
        if location == "section":
            value["plan"]["sections"][0]["kind"] = "forged"
        elif location == "anchors":
            value["plan"]["musical_anchors"] = [inputs["old"].anchors[0].model_dump(mode="json")]
        elif location == "grid":
            value["identity"]["grid"]["segments"][0]["bpm_quarter"] = 17
        elif location == "analysis":
            value["identity"]["analysis"]["audio"]["sha256"] = "f" * 64
        elif location == "algorithm":
            value["identity"]["algorithm"] = "unknown"
        else:
            value["identity"]["plan_inputs_ref"]["id"] = inputs["old"].id

    rehash(inputs, plan, mutate)
    with pytest.raises(ValueError):
        validate(inputs)


@pytest.mark.parametrize(
    "config",
    [
        {"min_rms": True, "support_window_sec": 0.05},
        {"min_rms": "0", "support_window_sec": 0.05},
        {"min_rms": -1, "support_window_sec": 0.05},
        {"min_rms": 0, "support_window_sec": 0.05, "extra": 1},
        {"min_rms": 0},
    ],
)
def test_rehashed_config_schema_is_strict(inputs, config):
    plan = publish(inputs)
    rehash(inputs, plan, lambda value: value["identity"].update({"config": config}))
    with pytest.raises(ValueError):
        validate(inputs)


@pytest.mark.parametrize("document", [None, [], {}, {"identity": None, "plan": {}}])
def test_bad_plan_shape_is_validation_error(inputs, document):
    plan = publish(inputs)
    write(inputs["root"] / "plans" / f"{plan.id}.json", document)
    with pytest.raises(ValueError):
        validate(inputs)


def test_plan_json_budget_exact_boundary_and_early_rejection(inputs, monkeypatch):
    import ai_edda.native_plans as module

    plan = publish(inputs)
    path = inputs["root"] / "plans" / f"{plan.id}.json"
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", path.stat().st_size)
    assert plan.id in validate(inputs)
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", path.stat().st_size - 1)
    monkeypatch.setattr(
        module, "resolve_analysis", lambda *args: pytest.fail("Unbounded plan reached resolver")
    )
    with pytest.raises(ValueError, match="limit"):
        validate(inputs)


def test_cancel_before_plan_projection_preserves_files(inputs):
    plan = publish(inputs)
    path = inputs["root"] / "plans" / f"{plan.id}.json"
    before = path.read_bytes()
    calls = []

    def cancel():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        validate_native_plans(inputs["root"], inputs["history"], inputs["analysis_map"], {}, cancel)
    assert path.read_bytes() == before
