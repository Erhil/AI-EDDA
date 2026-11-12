"""Independent input/target validation with actual immutable publishers."""

import json
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from test_native_media_review import media as media
from test_native_media_review import read, write

from ai_edda.application import ApplicationService
from ai_edda.domain import ObjectRef
from ai_edda.native_history import validate_native_history
from ai_edda.native_inputs import validate_native_inputs
from ai_edda.native_media import validate_native_media
from ai_edda.plan_inputs import build_plan_inputs, publish_plan_inputs
from ai_edda.project_store import ProjectStore
from ai_edda.target_catalog import publish_target


@pytest.fixture
def inputs(media):
    root = media["root"]
    with ProjectStore(root.parent) as store:
        app = ApplicationService(store)
        snapshot = store.load(media["history"].materialize_current().project.id)
        pid = snapshot.project.id
        app.execute(
            pid,
            {
                "command_id": "second-chart",
                "kind": "create_chart",
                "payload": {"name": "deleted later"},
                "base_project_revision": snapshot.project.revision,
                "expected_grid_revision": snapshot.grid.revision,
                "expected_chart_revisions": {c.id: c.revision for c in snapshot.charts},
            },
        )
        snapshot = store.load(pid)
        deleted = snapshot.charts[1].id
        audio_map, _, analysis_map = validate_native_media(root, validate_native_history(root, pid))
        analysis = analysis_map[media["analysis_id"]]
        anchors = [
            {"id": str(uuid4()), "time_sec": 0.1, "tolerance_sec": 0.01, "target_scope": [deleted]}
        ]
        old = build_plan_inputs(
            analysis.audio_asset_id,
            analysis.audio,
            ObjectRef(id=analysis.id, revision=0),
            anchors,
            [c.id for c in snapshot.charts],
        )
        new = build_plan_inputs(
            analysis.audio_asset_id,
            analysis.audio,
            ObjectRef(id=analysis.id, revision=0),
            [],
            [snapshot.charts[0].id],
        )
        orphan = build_plan_inputs(
            analysis.audio_asset_id,
            analysis.audio,
            ObjectRef(id=analysis.id, revision=0),
            [{**anchors[0], "id": str(uuid4())}],
            [deleted],
        )
        for record in (old, new, orphan):
            publish_plan_inputs(root, record)
        tid = str(uuid4())
        targets = [
            publish_target(
                root,
                {
                    "id": tid,
                    "version": v,
                    "name": "test",
                    "movement_profile": "future-compatible-policy",
                },
            )
            for v in (1, 2)
        ]
        orphan_target = publish_target(
            root, {"id": str(uuid4()), "version": 1, "name": "unselected"}
        )
        for stage, (ref, charts) in enumerate(
            (
                (ObjectRef(id=old.id, revision=0), snapshot.charts),
                (ObjectRef(id=new.id, revision=0), snapshot.charts[:1]),
            )
        ):
            current = store.load(pid)
            charts = tuple(
                c.model_copy(update={"target_profile": targets[stage]}) if i == 0 else c
                for i, c in enumerate(charts)
            )
            project = current.project.model_copy(update={"plan_inputs": ref})
            store.commit(
                pid,
                str(uuid4()),
                {},
                current.project.revision,
                current.grid.revision,
                {c.id: c.revision for c in current.charts},
                project,
                current.grid,
                charts,
                {"ok": True},
            )
        history = validate_native_history(root, pid)
    return {
        **media,
        "history": history,
        "audio_map": audio_map,
        "analysis_map": analysis_map,
        "old": old,
        "new": new,
        "orphan": orphan,
        "deleted": deleted,
        "targets": targets,
        "orphan_target": orphan_target,
    }


def validate(inputs):
    return validate_native_inputs(
        inputs["root"], inputs["history"], inputs["audio_map"], inputs["analysis_map"]
    )


def rewrite_input(inputs, record, mutate):
    old_path = inputs["root"] / "plan-inputs" / f"{record.id}.json"
    value = read(old_path)
    mutate(value)
    data = {k: v for k, v in value.items() if k != "id"}
    value["id"] = str(
        uuid5(
            NAMESPACE_URL,
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    )
    path = old_path.with_name(value["id"] + ".json")
    old_path.unlink()
    write(path, value)
    return path


def test_publishers_preserve_historical_scopes_and_orphans(inputs):
    root = inputs["root"]
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    records, targets = validate(inputs)
    assert set(records) == {inputs[k].id for k in ("old", "new", "orphan")}
    assert len(targets) == 3
    assert inputs["deleted"] not in {c.id for c in inputs["history"].materialize_current().charts}
    assert inputs["deleted"] in records[inputs["old"].id].anchors[0].target_scope
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_current_selecting_state_cannot_reuse_deleted_scope(inputs):
    from dataclasses import replace

    current = inputs["history"].materialize_current()
    changed = replace(
        current,
        project=current.project.model_copy(
            update={"plan_inputs": ObjectRef(id=inputs["old"].id, revision=0)}
        ),
    )
    from copy import copy

    history = copy(inputs["history"])
    history.heads = [*history.heads, replace(history.current_head, project=changed.project)]
    with pytest.raises(ValueError):
        validate_native_inputs(inputs["root"], history, inputs["audio_map"], inputs["analysis_map"])


@pytest.mark.parametrize("field", ["audio_asset_id", "audio_sha256", "analysis"])
def test_orphan_inputs_require_their_own_valid_media(inputs, field):
    def mutate(value):
        value[field] = (
            str(uuid4())
            if field == "audio_asset_id"
            else "f" * 64
            if field == "audio_sha256"
            else {"id": str(uuid4()), "revision": 0}
        )

    rewrite_input(inputs, inputs["orphan"], mutate)
    with pytest.raises(ValueError):
        validate(inputs)


@pytest.mark.parametrize(
    "field, replacement",
    [("time_sec", 0.2), ("time_sec", -1), ("tolerance_sec", -1), ("target_scope", [str(uuid4())])],
)
def test_orphan_anchor_bounds_and_scopes_are_validated(inputs, field, replacement):
    rewrite_input(
        inputs, inputs["orphan"], lambda value: value["anchors"][0].update({field: replacement})
    )
    with pytest.raises(ValueError):
        validate(inputs)


@pytest.mark.parametrize("form", ["uppercase", "hex"])
def test_anchor_uuid_is_canonical_even_with_valid_content_identity(inputs, form):
    def mutate(value):
        original = value["anchors"][0]["id"]
        value["anchors"][0]["id"] = (
            original.upper() if form == "uppercase" else original.replace("-", "")
        )

    rewrite_input(inputs, inputs["orphan"], mutate)
    with pytest.raises(ValueError):
        validate(inputs)


def test_missing_historical_input_is_rejected(inputs):
    (inputs["root"] / "plan-inputs" / f"{inputs['old'].id}.json").unlink()
    with pytest.raises(ValueError):
        validate(inputs)


def test_missing_target_used_only_by_historical_chart_is_rejected(inputs):
    ref = inputs["targets"][0]
    (inputs["root"] / "targets" / f"{ref.id}-{ref.revision}.json").unlink()
    with pytest.raises(ValueError):
        validate(inputs)


@pytest.mark.parametrize("document", [None, [], 42, {"anchors": None}])
def test_malformed_unselected_input_is_validation_error(inputs, document):
    write(inputs["root"] / "plan-inputs" / f"{inputs['orphan'].id}.json", document)
    with pytest.raises(ValueError):
        validate(inputs)


@pytest.mark.parametrize("family", ["plan-inputs", "targets"])
def test_bounded_json_precedes_existing_resolvers(inputs, family, monkeypatch):
    import ai_edda.native_inputs as module

    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 1)
    if family == "targets":
        for path in (inputs["root"] / "plan-inputs").iterdir():
            path.unlink()
    resolver = "resolve_plan_inputs" if family == "plan-inputs" else "resolve_target"
    monkeypatch.setattr(
        module, resolver, lambda *args: pytest.fail("Unbounded input reached resolver")
    )
    with pytest.raises(ValueError, match="limit"):
        validate(inputs)


def test_aggregate_json_budget_counts_each_catalog_file_once(inputs, monkeypatch):
    import ai_edda.native_inputs as module

    total = sum(
        p.stat().st_size
        for family in ("plan-inputs", "targets")
        for p in (inputs["root"] / family).iterdir()
    )
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", total)
    assert len(validate(inputs)[0]) == 3
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", total - 1)
    with pytest.raises(ValueError, match="limit"):
        validate(inputs)


@pytest.mark.parametrize("family", ["plan-inputs", "targets"])
def test_unknown_catalog_file_is_rejected(inputs, family):
    write(inputs["root"] / family / "unknown.json", {})
    with pytest.raises(ValueError):
        validate(inputs)


@pytest.mark.parametrize("family", ["plan-inputs", "targets"])
def test_catalog_requires_regular_files(inputs, family):
    path = next((inputs["root"] / family).iterdir())
    path.unlink()
    path.mkdir()
    with pytest.raises(ValueError):
        validate(inputs)


def test_duplicate_input_json_key_is_rejected(inputs):
    path = inputs["root"] / "plan-inputs" / f"{inputs['orphan'].id}.json"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw[:-1] + ',"revision":0}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        validate(inputs)


@pytest.mark.parametrize("revision", [True, "0", 0.0])
def test_input_revision_is_exact_integer(inputs, revision):
    path = inputs["root"] / "plan-inputs" / f"{inputs['orphan'].id}.json"
    value = read(path)
    value["revision"] = revision
    write(path, value)
    with pytest.raises(ValueError):
        validate(inputs)


@pytest.mark.parametrize("version", [True, "1", 1.0])
def test_unselected_target_version_is_exact_integer(inputs, version):
    ref = inputs["orphan_target"]
    path = inputs["root"] / "targets" / f"{ref.id}-{ref.revision}.json"
    value = read(path)
    value["version"] = version
    write(path, value)
    with pytest.raises(ValueError):
        validate(inputs)
