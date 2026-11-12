import pytest

from ai_edda.arrangement_domain import MusicalAnchor
from ai_edda.domain import Provenance, new_id
from ai_edda.plan_inputs import PlanInputs, _identity, remove_chart_scope


def record(anchors):
    data = {
        "revision": 0,
        "audio_asset_id": "audio",
        "audio_sha256": "a" * 64,
        "analysis": {"id": "analysis", "revision": 2},
        "anchors": [a.model_dump(mode="json") for a in anchors],
    }
    return PlanInputs(id=_identity(data), **data)


def test_deletes_only_affected_scopes_and_preserves_source_and_anchor_data():
    anchors = (
        MusicalAnchor(id=new_id(), time_sec=1, target_scope=()),
        MusicalAnchor(id=new_id(), time_sec=2, target_scope=("A",)),
        MusicalAnchor(id=new_id(), time_sec=3, strength="required", target_scope=("B",)),
        MusicalAnchor(
            id=new_id(),
            time_sec=4,
            strength="required",
            tolerance_sec=0.2,
            target_scope=("A", "B"),
            provenance=Provenance(created_by="imported"),
        ),
    )
    old = record(anchors)
    new = remove_chart_scope(old, "B", {"A"})
    assert [a.id for a in new.anchors] == [anchors[0].id, anchors[1].id, anchors[3].id]
    assert [a.target_scope for a in new.anchors] == [(), ("A",), ("A",)]
    assert new.anchors[2].model_copy(update={"target_scope": ("A", "B")}) == anchors[3]
    assert (new.audio_asset_id, new.audio_sha256, new.analysis, new.revision) == (
        old.audio_asset_id,
        old.audio_sha256,
        old.analysis,
        0,
    )
    assert old.anchors == anchors


def test_all_scoped_anchors_removed_but_global_retained():
    old = record((MusicalAnchor(id=new_id(), time_sec=1, target_scope=("B",)),))
    new = remove_chart_scope(old, "B", set())
    assert new.anchors == ()
    assert new.id != old.id


def test_unknown_remaining_scope_rejected_and_unaffected_returns_same_reference():
    old = record((MusicalAnchor(id=new_id(), time_sec=1, target_scope=("A",)),))
    assert remove_chart_scope(old, "B", {"A"}) is old
    with pytest.raises(ValueError, match="Invalid anchor scope"):
        remove_chart_scope(old, "B", set())


@pytest.mark.parametrize("strength", ["soft", "required"])
def test_deleting_last_chart_retains_only_existing_global_anchors(strength):
    global_anchor = MusicalAnchor(
        id=new_id(),
        time_sec=0.5,
        strength=strength,
        provenance=Provenance(created_by="manual", operation_id="created-global"),
    )
    scoped = MusicalAnchor(id=new_id(), time_sec=0.5, strength=strength, target_scope=("B",))
    old = record((global_anchor, scoped))
    updated = remove_chart_scope(old, "B", ())
    assert updated.anchors == (global_anchor,)
    assert updated.anchors[0].target_scope == ()
    assert all(anchor.id != scoped.id for anchor in updated.anchors)


@pytest.mark.parametrize("scope", [(), ("A",), ("C", "A")])
def test_no_affected_scope_returns_exact_original_model(scope):
    old = record((MusicalAnchor(id=new_id(), time_sec=1, target_scope=scope),))
    assert remove_chart_scope(old, "B", iter(("A", "C"))) is old


def test_empty_anchor_record_is_unchanged():
    old = record(())
    assert remove_chart_scope(old, "B", ()) is old


@pytest.mark.parametrize("strength", ["soft", "required"])
@pytest.mark.parametrize("scope", [("B", "unknown"), ("unknown",)])
def test_unknown_ids_are_rejected_instead_of_silently_repaired(strength, scope):
    old = record((MusicalAnchor(id=new_id(), time_sec=1, strength=strength, target_scope=scope),))
    before = old.model_dump(mode="json")
    with pytest.raises(ValueError, match="Invalid anchor scope"):
        remove_chart_scope(old, "B", {"A"})
    assert old.model_dump(mode="json") == before


def test_preserves_scope_order_anchor_order_and_complete_provenance():
    provenance = Provenance(
        created_by="generated",
        operation_id="operation",
        source_ids=("source-2", "source-1"),
        generation_run_id="run",
        last_modified_by="editor",
        last_modified_operation_id="edit",
    )
    anchors = tuple(
        MusicalAnchor(
            id=new_id(),
            time_sec=index + 0.25,
            tolerance_sec=0.125,
            strength="required" if index % 2 else "soft",
            target_scope=scope,
            provenance=provenance,
        )
        for index, scope in enumerate((("C", "B", "A"), ("B",), (), ("B", "C", "A")))
    )
    old = record(anchors)
    before = old.model_dump(mode="json")
    updated = remove_chart_scope(old, "B", {"A", "C"})
    assert tuple(anchor.id for anchor in updated.anchors) == (
        anchors[0].id,
        anchors[2].id,
        anchors[3].id,
    )
    assert tuple(anchor.target_scope for anchor in updated.anchors) == (("C", "A"), (), ("C", "A"))
    for retained, source in zip(updated.anchors, (anchors[0], anchors[2], anchors[3])):
        assert retained.model_dump(exclude={"target_scope"}) == source.model_dump(
            exclude={"target_scope"}
        )
    assert old.model_dump(mode="json") == before


def test_changed_identity_is_deterministic_over_complete_record_and_source_is_preserved():
    old = record((MusicalAnchor(id=new_id(), time_sec=1, target_scope=("B",)),))
    # Exercise values that must remain exact instead of being derived from current project state.
    data = old.model_dump(mode="json", exclude={"id"})
    data.update(
        audio_asset_id="old-audio",
        audio_sha256="AB" * 32,
        analysis={"id": "old-analysis", "revision": 17},
    )
    old = PlanInputs(id=_identity(data), **data)
    first = remove_chart_scope(old, "B", ())
    repeated = remove_chart_scope(old, "B", iter(()))
    expected = {**data, "anchors": []}
    assert first == repeated
    assert first.id == _identity(expected)
    assert first.id != old.id
    assert first.model_dump(mode="json", exclude={"id"}) == expected
    assert first.revision == 0


def test_serialized_record_uses_the_same_content_identity():
    old = record((MusicalAnchor(id=new_id(), time_sec=1, target_scope=("A", "B")),))
    assert remove_chart_scope(old.model_dump(mode="json"), "B", ("A",)) == remove_chart_scope(
        old, "B", {"A"}
    )


@pytest.mark.parametrize("affected", [False, True])
def test_bad_record_identity_is_rejected_even_when_cleanup_would_remove_the_anchor(affected):
    old = record((MusicalAnchor(id=new_id(), time_sec=1, target_scope=("B",) if affected else ()),))
    corrupt = old.model_copy(update={"id": new_id()})
    with pytest.raises(ValueError, match="identity mismatch"):
        remove_chart_scope(corrupt, "B", ())
