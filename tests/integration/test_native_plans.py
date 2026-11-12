"""Validate plans made by the real publisher."""

from dataclasses import replace

import pytest
from test_native_inputs_review import inputs as inputs
from test_native_inputs_review import read, validate, write
from test_native_media_review import media as media

from ai_edda.domain import ObjectRef
from ai_edda.native_plans import validate_native_plans
from ai_edda.plan_catalog import resolve_plan


def publish(inputs):
    snapshot = inputs["history"].materialize_current()
    analysis = inputs["analysis_map"][inputs["analysis_id"]]
    project = snapshot.project.model_copy(
        update={
            "analysis": ObjectRef(id=analysis.id, revision=analysis.revision),
            "audio": analysis.audio,
        }
    )
    return resolve_plan(inputs["root"], replace(snapshot, project=project))


def test_published_plan_and_no_writes(inputs):
    root = inputs["root"]
    plan = publish(inputs)
    records, _ = validate(inputs)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert validate_native_plans(root, inputs["history"], inputs["analysis_map"], records) == {
        plan.id: plan
    }
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("field", ["algorithm", "analysis", "grid", "config", "plan_inputs"])
def test_tampered_identity_is_rejected(inputs, field):
    root = inputs["root"]
    plan = publish(inputs)
    records, _ = validate(inputs)
    path = root / "plans" / (plan.id + ".json")
    raw = read(path)
    raw["identity"][field] = None
    write(path, raw)
    with pytest.raises(ValueError):
        validate_native_plans(root, inputs["history"], inputs["analysis_map"], records)


def test_cancel_before_work(inputs):
    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        validate_native_plans(inputs["root"], inputs["history"], {}, {}, cancel)
