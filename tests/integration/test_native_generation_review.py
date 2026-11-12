"""Verify archived generation from real worker and immutable publications."""

import hashlib
import json
from threading import Event
from uuid import uuid4

import pytest
from test_native_inputs_review import inputs as inputs
from test_native_media_review import media as media
from test_native_media_review import read, write

from ai_edda.application import ApplicationService
from ai_edda.arrangement_domain import ArrangementRequest, TargetProfile
from ai_edda.domain import HitEvent, ObjectRef
from ai_edda.draft_decisions import reject_draft
from ai_edda.generation_catalog import publish_generation
from ai_edda.generation_context import prepare_generation
from ai_edda.generation_worker import generation_worker
from ai_edda.native_generation import validate_native_generation
from ai_edda.native_history import validate_native_history
from ai_edda.native_inputs import validate_native_inputs
from ai_edda.native_plans import validate_native_plans
from ai_edda.plan_catalog import resolve_plan
from ai_edda.project_store import ProjectStore
from ai_edda.target_catalog import publish_target, resolve_target


def make_publication(inputs, snapshot, target, seed):
    analysis = inputs["analysis_map"][inputs["analysis_id"]]
    plan = resolve_plan(inputs["root"], snapshot)
    request = ArrangementRequest(
        id=str(uuid4()),
        project_id=snapshot.project.id,
        generator_id="single_lane",
        generator_version="1",
        audio_asset_id=analysis.audio_asset_id,
        audio_sha256=analysis.audio.sha256,
        analysis={"id": analysis.id, "revision": analysis.revision},
        plan={"id": plan.id, "revision": 0},
        grid_revision=snapshot.grid.revision,
        chart={"id": snapshot.charts[0].id, "revision": snapshot.charts[0].revision},
        target=target,
        range_q={"start_q": 0, "end_q": 0.2},
        mode="single_lane",
        lane=0,
        seed=seed,
    )
    context = prepare_generation(inputs["root"], snapshot, request)
    draft_id = str(uuid4())
    envelope = {
        "project_id": snapshot.project.id,
        "request_id": request.id,
        "draft_id": draft_id,
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
    }
    result = generation_worker(envelope, lambda _: None, Event())
    public = publish_generation(inputs["root"], draft_id, uuid4().hex, envelope, result)
    return draft_id, public


@pytest.fixture
def generation(inputs):
    root = inputs["root"]
    target = {"id": str(uuid4()), "version": 1, "name": "supported", "allowed_subdivisions": [1]}
    ref = publish_target(root, target)
    with ProjectStore(root.parent) as store:
        snapshot = store.load(inputs["history"].materialize_current().project.id)
        analysis = inputs["analysis_map"][inputs["analysis_id"]]
        grid = snapshot.grid.model_copy(
            update={
                "review": "approved",
                "segments": tuple(
                    s.model_copy(update={"review": "approved"}) for s in snapshot.grid.segments
                ),
            }
        )
        # This invalid event lies outside the generated span and must remain storeable.
        chart = snapshot.charts[0].model_copy(
            update={
                "target_profile": ref,
                "events": (HitEvent(time_ref={"kind": "audio", "time_sec": 0.18}),),
            }
        )
        project = snapshot.project.model_copy(
            update={"audio": analysis.audio, "analysis": ObjectRef(id=analysis.id, revision=0)}
        )
        store.commit(
            snapshot.project.id,
            "generation-source",
            {},
            snapshot.project.revision,
            snapshot.grid.revision,
            {c.id: c.revision for c in snapshot.charts},
            project,
            grid,
            (chart,),
            {"ok": True},
        )
        source = store.load(snapshot.project.id)
    target_model = resolve_target(root, ref)
    first, public = make_publication(inputs, source, target_model, 1)
    rejected, _ = make_publication(inputs, source, target_model, 2)
    reject_draft(root, rejected, str(uuid4()))
    history = validate_native_history(root, source.project.id)
    records, targets = validate_native_inputs(
        root, history, inputs["audio_map"], inputs["analysis_map"]
    )
    plans = validate_native_plans(root, history, inputs["analysis_map"], records)
    return {
        **inputs,
        "history": history,
        "source": source,
        "plans_map": plans,
        "targets_map": targets,
        "inputs_map": records,
        "draft_id": first,
        "rejected_id": rejected,
        "public": public,
    }


def validate(generation):
    return validate_native_generation(
        generation["root"],
        generation["history"],
        generation["analysis_map"],
        generation["plans_map"],
        generation["targets_map"],
    )


def repair_publication(directory):
    context, draft = read(directory / "context.json"), read(directory / "draft.json")

    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    receipt = read(directory / "publication.json")
    receipt["context_sha256"] = hashlib.sha256(canonical(context)).hexdigest()
    receipt["result_sha256"] = hashlib.sha256(canonical({"draft": draft})).hexdigest()
    receipt["public_result"] = draft
    receipt["artifacts"] = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("context.json", "draft.json")
    }
    write(directory / "publication.json", receipt)


def test_real_published_rejected_and_locally_invalid_drafts_are_preserved(generation):
    root = generation["root"]
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert validate(generation) is None
    publication = read(root / "generation" / generation["draft_id"] / "publication.json")
    assert "INVALID_EVENT_SIZE" in {d["code"] for d in publication["public_result"]["validation"]}
    assert {p: p.read_bytes() for p in before} == before


def test_historical_generation_survives_current_grid_change(generation):
    with ProjectStore(generation["root"].parent) as store:
        app = ApplicationService(store)
        view = app.get(generation["source"].project.id)
        grid = view.grid.model_dump(mode="json")
        grid["segments"][0]["bpm_quarter"] = 240
        app.execute(
            view.project.id,
            {
                "command_id": "new-grid-after-draft",
                "kind": "apply_grid",
                "payload": {"grid": grid},
                "base_project_revision": view.project.revision,
                "expected_grid_revision": view.grid.revision,
                "expected_chart_revisions": {c.id: c.revision for c in view.charts},
            },
        )
        generation["history"] = validate_native_history(generation["root"], view.project.id)
    assert generation["source"].grid != generation["history"].materialize_current().grid
    assert validate(generation) is None


def test_unbound_chart_preserves_ad_hoc_target_support(generation):
    with ProjectStore(generation["root"].parent) as store:
        snapshot = store.load(generation["source"].project.id)
        chart = snapshot.charts[0].model_copy(update={"target_profile": None})
        store.commit(
            snapshot.project.id,
            "unbound-chart",
            {},
            snapshot.project.revision,
            snapshot.grid.revision,
            {c.id: c.revision for c in snapshot.charts},
            snapshot.project,
            snapshot.grid,
            (chart,),
            {"ok": True},
        )
        snapshot = store.load(snapshot.project.id)
        generation["history"] = validate_native_history(generation["root"], snapshot.project.id)
    target = TargetProfile(id=str(uuid4()), version=3, name="Ad hoc", allowed_subdivisions=(1,))
    draft_id, _ = make_publication(generation, snapshot, target, 3)
    generation["plans_map"] = validate_native_plans(
        generation["root"],
        generation["history"],
        generation["analysis_map"],
        generation["inputs_map"],
    )
    assert (target.id, target.version) not in generation["targets_map"]
    assert (generation["root"] / "generation" / draft_id).is_dir()
    assert validate(generation) is None


@pytest.mark.parametrize("source", ["analysis_map", "plans_map", "targets_map"])
def test_missing_verified_source_is_rejected(generation, source):
    generation[source] = {}
    with pytest.raises(ValueError):
        validate(generation)


@pytest.mark.parametrize("source", ["grid", "chart"])
def test_unknown_historical_source_is_rejected(generation, source):
    history = generation["history"]
    from copy import copy

    history = copy(history)
    if source == "grid":
        history.grids = dict(history.grids)
        history.grids.pop(generation["source"].grid.revision)
    else:
        history.charts = dict(history.charts)
        chart = generation["source"].charts[0]
        history.charts.pop((chart.id, chart.revision))
    generation["history"] = history
    with pytest.raises(ValueError):
        validate(generation)


def test_chart_and_grid_cannot_mix_different_historical_revisions(generation):
    directory = generation["root"] / "generation" / generation["draft_id"]
    value = read(directory / "context.json")
    index = generation["history"]
    record = next(
        c for c in index.charts.values() if c.grid_revision != generation["source"].grid.revision
    )
    wrong = index.load_chart(record.chart_id, record.revision)
    value["context"]["chart"] = wrong.model_dump(mode="json")
    value["request"]["chart"] = {"id": wrong.id, "revision": wrong.revision}
    write(directory / "context.json", value)
    repair_publication(directory)
    with pytest.raises(ValueError, match="STALE_REVISION"):
        validate(generation)


@pytest.mark.parametrize("document", [None, [], {}, {"project_id": "bad"}])
def test_context_envelope_shape_is_strict(generation, document):
    write(generation["root"] / "generation" / generation["draft_id"] / "context.json", document)
    with pytest.raises(ValueError):
        validate(generation)


@pytest.mark.parametrize(
    "location", ["request_revision", "context_revision", "context_metadata", "draft_revision"]
)
def test_nested_types_cannot_coerce_even_after_publication_hash_repair(generation, location):
    directory = generation["root"] / "generation" / generation["draft_id"]
    path = directory / ("draft.json" if location == "draft_revision" else "context.json")
    value = read(path)
    if location == "request_revision":
        value["request"]["analysis"]["revision"] = False
    elif location == "context_revision":
        value["context"]["analysis"]["revision"] = False
    elif location == "context_metadata":
        value["context"]["analysis"]["audio"]["channels"] = True
    else:
        value["base_chart"]["revision"] = str(value["base_chart"]["revision"])
    write(path, value)
    repair_publication(directory)
    with pytest.raises(ValueError):
        validate(generation)


@pytest.mark.parametrize("location", ["draft", "run", "event"])
def test_draft_ids_are_canonical(generation, location):
    directory = generation["root"] / "generation" / generation["draft_id"]
    path = directory / "draft.json"
    value = read(path)
    if location == "draft":
        value["id"] = value["id"].upper()
    elif location == "run":
        value["generation_run"]["id"] = value["generation_run"]["id"].upper()
    else:
        value["chart"]["events"][0]["id"] = value["chart"]["events"][0]["id"].upper()
    write(path, value)
    repair_publication(directory)
    with pytest.raises(ValueError):
        validate(generation)


def test_rejection_requires_matching_draft_hash(generation):
    path = generation["root"] / "generation" / generation["rejected_id"] / "rejection.json"
    value = read(path)
    value["draft_sha256"] = "0" * 64
    write(path, value)
    with pytest.raises(ValueError):
        validate(generation)


@pytest.mark.parametrize("change", ["extra", "directory"])
def test_exact_regular_file_set_is_required(generation, change):
    directory = generation["root"] / "generation" / generation["draft_id"]
    if change == "extra":
        write(directory / "unexpected.json", {})
    else:
        (directory / "draft.json").unlink()
        (directory / "draft.json").mkdir()
    with pytest.raises(ValueError):
        validate(generation)


def test_exact_aggregate_json_budget_and_early_rejection(generation, monkeypatch):
    import ai_edda.native_generation as module

    total = sum(p.stat().st_size for p in (generation["root"] / "generation").rglob("*.json"))
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", total)
    assert validate(generation) is None
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", total - 1)
    with pytest.raises(ValueError, match="limit"):
        validate(generation)
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 1)
    monkeypatch.setattr(
        module,
        "_check_generation_reassembly",
        lambda *args, **kwargs: pytest.fail("Model work reached before preflight"),
    )
    with pytest.raises(ValueError, match="limit"):
        validate(generation)


def test_cancel_preserves_publications(generation):
    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        validate_native_generation(generation["root"], generation["history"], {}, {}, {}, cancel)

@pytest.mark.parametrize("name", ["context.json", "draft.json", "publication.json", "rejection.json"])
@pytest.mark.parametrize("field", ["max_bytes", "max_nodes", "max_depth"])
def test_generation_record_limits(generation, monkeypatch, name, field):
    from dataclasses import replace

    import ai_edda.native_generation as module

    path = generation["root"] / "generation" / generation["rejected_id"] / name
    value = read(path)
    stack = [(value, 0)]
    nodes = depth = 0
    while stack:
        item, level = stack.pop()
        nodes += 1
        depth = max(depth, level)
        if isinstance(item, dict):
            nodes += len(item)
            stack.extend((child, level + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, level + 1) for child in item)
    boundary = {"max_bytes": path.stat().st_size, "max_nodes": nodes, "max_depth": depth}[field]
    original = module.read_native_history_json
    limit = [boundary]

    def bounded(record, *, limits, cancel):
        if record == path:
            limits = replace(limits, **{field: limit[0]})
        return original(record, limits=limits, cancel=cancel)

    monkeypatch.setattr(module, "read_native_history_json", bounded)
    assert validate(generation) is None
    limit[0] -= 1
    with pytest.raises(ValueError, match="limit"):
        validate(generation)


def test_each_record_is_parsed_once(generation, monkeypatch):
    import ai_edda.native_generation as module

    original = module.read_native_history_json
    seen = []

    def counted(path, **kwargs):
        seen.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(module, "read_native_history_json", counted)
    assert validate(generation) is None
    assert sorted(seen) == sorted((generation["root"] / "generation").rglob("*.json"))


def test_repaired_hashes_do_not_hide_wrong_reassembly(generation):
    directory = generation["root"] / "generation" / generation["draft_id"]
    path = directory / "draft.json"
    value = read(path)
    value["chart"]["metadata"]["name"] = "changed"
    write(path, value)
    repair_publication(directory)
    with pytest.raises(ValueError, match="Generation draft mismatch"):
        validate(generation)


@pytest.mark.parametrize("name", ["publication.json", "rejection.json"])
def test_malformed_late_record_is_rejected(generation, name):
    path = generation["root"] / "generation" / generation["rejected_id"] / name
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        validate(generation)


@pytest.mark.parametrize("stage", ["read", "canonical", "reassembly_before", "reassembly_after", "digest", "publication"])
@pytest.mark.parametrize("exception", [OSError, KeyError])
def test_late_cancel_preserves_exception_identity(generation, monkeypatch, stage, exception):
    import ai_edda.native_generation as module

    error = exception("cancelled")
    armed = [False]
    original_read = module.read_native_history_json
    original_reassembly = module._check_generation_reassembly
    original_publication = module._check_generation_publication
    original_json = module._json

    def cancel():
        if armed[0]:
            raise error

    def read_record(path, **kwargs):
        if stage == "read" and path.name == "publication.json":
            armed[0] = True
        result = original_read(path, **kwargs)
        if stage == "digest" and path.name == "publication.json":
            armed[0] = True
        return result

    def canonical(value):
        result = original_json(value)
        if stage == "canonical":
            armed[0] = True
        return result

    def reassemble(*args, **kwargs):
        if stage == "reassembly_before":
            armed[0] = True
        result = original_reassembly(*args, **kwargs)
        if stage == "reassembly_after":
            armed[0] = True
            kwargs["cancel"]()
        return result

    def publication(*args, **kwargs):
        if stage == "publication":
            armed[0] = True
        return original_publication(*args, **kwargs)

    monkeypatch.setattr(module, "read_native_history_json", read_record)
    monkeypatch.setattr(module, "_json", canonical)
    monkeypatch.setattr(module, "_check_generation_reassembly", reassemble)
    monkeypatch.setattr(module, "_check_generation_publication", publication)
    with pytest.raises(exception) as caught:
        validate_native_generation(generation["root"], generation["history"], generation["analysis_map"],
                                   generation["plans_map"], generation["targets_map"], cancel)
    assert caught.value is error


def test_models_released_before_next_entry(generation, monkeypatch):
    import gc
    import weakref

    import ai_edda.native_generation as module

    original = module._check_generation_reassembly
    original_read = module.read_native_history_json
    previous = []
    last_entry = [None]

    def read_record(path, **kwargs):
        if path.name == "context.json" and last_entry[0] is not None:
            gc.collect()
            assert all(ref() is None for ref in previous)
        if path.name == "context.json":
            last_entry[0] = path.parent
        return original_read(path, **kwargs)

    def checked(draft_id, request, context, draft, value, **kwargs):
        gc.collect()
        assert all(ref() is None for ref in previous)
        previous[:] = [weakref.ref(model) for model in (request, context, draft)]
        return original(draft_id, request, context, draft, value, **kwargs)

    monkeypatch.setattr(module, "_check_generation_reassembly", checked)
    monkeypatch.setattr(module, "read_native_history_json", read_record)
    assert validate(generation) is None
    assert len(previous) == 3


def test_malformed_large_draft_collection_stops_at_first_error(generation):
    from pydantic import ValidationError

    path = generation["root"] / "generation" / generation["draft_id"] / "draft.json"
    value = read(path)
    value["chart"]["events"] = [{"id": str(uuid4())} for _ in range(1000)]
    write(path, value)
    with pytest.raises(ValidationError) as caught:
        validate(generation)
    assert caught.value.error_count() == 1
