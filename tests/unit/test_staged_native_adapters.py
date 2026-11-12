import importlib.util
import json
import runpy
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from ai_edda.native_history import _build_native_history_index

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = runpy.run_path(str(ROOT / "tests/unit/test_native_history_index_builder.py"))


def staged(name):
    spec = importlib.util.spec_from_file_location(
        "ai_edda._staged_" + name, ROOT / "src/ai_edda" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_catalogs_with_index(tmp_path):
    FIXTURES["history"](tmp_path)
    index = _build_native_history_index(tmp_path, FIXTURES["PROJECT"])
    assert staged("native_inputs").validate_native_inputs(tmp_path, index, {}, {}) == ({}, {})
    assert staged("import_receipts").validate_native_import_receipts(tmp_path, index) == {}
    assert staged("native_media").validate_native_media(tmp_path, index) == ({}, {}, {})


@pytest.fixture
def indexed_catalogs(tmp_path, monkeypatch):
    # Reuse the actual immutable publishers from the existing integration fixture.
    monkeypatch.syspath_prepend(str(ROOT / "tests/integration"))
    source = runpy.run_path(str(ROOT / "tests/integration/test_native_inputs_review.py"))
    media = source["media"].__wrapped__(tmp_path)
    data = source["inputs"].__wrapped__(media)
    index = _build_native_history_index(
        data["root"], data["history"].materialize_current().project.id
    )

    def unexpected(*args, **kwargs):
        pytest.fail("Metadata adapter loaded a historical chart/grid model")

    for name in ("load_chart", "load_grid", "materialize_current"):
        monkeypatch.setattr(index, name, unexpected)
    return data, index, source


def validate_inputs(data, index):
    return staged("native_inputs").validate_native_inputs(
        data["root"], index, data["audio_map"], data["analysis_map"]
    )


def test_real_catalogs_preserve_historical_scopes_targets_and_orphans(indexed_catalogs):
    data, index, _ = indexed_catalogs
    root = data["root"]
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    audio, covers, analyses = staged("native_media").validate_native_media(root, index)
    records, targets = validate_inputs(data, index)
    assert set(audio) == set(data["audio_ids"])
    assert data["cover_id"] in covers
    assert set(analyses) == {data["analysis_id"], data["orphan_analysis"]}
    assert set(records) == {data[name].id for name in ("old", "new", "orphan")}
    assert len(targets) == 3
    assert data["deleted"] not in {r.id for r in index.current_head.project.chart_heads}
    assert data["deleted"] in records[data["old"].id].anchors[0].target_scope
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("kind", ["inputs", "target"])
def test_missing_only_historical_catalog_reference_is_rejected(indexed_catalogs, kind):
    data, index, _ = indexed_catalogs
    if kind == "inputs":
        (data["root"] / "plan-inputs" / f"{data['old'].id}.json").unlink()
    else:
        target = data["targets"][0]
        (data["root"] / "targets" / f"{target.id}-{target.revision}.json").unlink()
    with pytest.raises(ValueError, match="historical"):
        validate_inputs(data, index)


def test_selecting_head_scope_uses_its_exact_selected_charts(indexed_catalogs):
    data, index, _ = indexed_catalogs
    changed = deepcopy(index.current_head.raw_head)
    changed["project"]["plan_inputs"] = {"id": data["old"].id, "revision": 0}
    index.record_head(changed)
    with pytest.raises(ValueError, match="selected anchor scope"):
        validate_inputs(data, index)


def test_unselected_input_cannot_reference_unknown_chart(indexed_catalogs):
    data, index, source = indexed_catalogs
    source["rewrite_input"](
        data, data["orphan"], lambda value: value["anchors"][0].update(target_scope=[str(uuid4())])
    )
    with pytest.raises(ValueError, match="scope"):
        validate_inputs(data, index)


def test_historical_analysis_stays_bound_to_its_own_audio(indexed_catalogs):
    data, index, _ = indexed_catalogs
    index.heads = [
        replace(
            head,
            project=head.project.model_copy(update={"audio": index.current_head.project.audio}),
        )
        if head.project.analysis is not None
        else head
        for head in index.heads
    ]
    with pytest.raises(ValueError, match="historical analysis"):
        staged("native_media").validate_native_media(data["root"], index)


def publish_test_import(data, index, *, project_id=None, revision=None):
    from ai_edda.import_receipts import build_import_receipt, publish_import_receipt

    receipt = build_import_receipt(
        {
            "request_id": str(uuid4()),
            "archive_kind": "native",
            "file_name": "backup.zip",
            "file_size": 1,
        },
        "import-job",
        "a" * 64,
        project_id or index.project_id,
        min(head.project.revision for head in index.heads) if revision is None else revision,
        "b" * 64,
        "c" * 64,
    )
    publish_import_receipt(data["root"], receipt)
    return receipt


def test_import_receipt_accepts_actual_historical_revision(indexed_catalogs):
    data, index, _ = indexed_catalogs
    receipt = publish_test_import(data, index)
    assert receipt.public_result.revision != index.current_head.project.revision
    records = staged("import_receipts").validate_native_import_receipts(data["root"], index)
    assert records[receipt.request_id].model_dump() == receipt.model_dump()


@pytest.mark.parametrize(
    "failure", ["wrong_project", "unknown_revision", "bool_revision", "bool_version"]
)
def test_import_receipt_rejects_invalid_membership_and_strict_types(indexed_catalogs, failure):
    data, index, _ = indexed_catalogs
    receipt = publish_test_import(
        data,
        index,
        project_id=str(uuid4()) if failure == "wrong_project" else None,
        revision=max(head.project.revision for head in index.heads) + 1
        if failure == "unknown_revision"
        else None,
    )
    if failure in ("bool_revision", "bool_version"):
        path = data["root"] / "imports" / f"{receipt.request_id}.json"
        raw = json.loads(path.read_text())
        if failure == "bool_revision":
            raw["public_result"]["revision"] = True
        else:
            raw["version"] = True
        path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        staged("import_receipts").validate_native_import_receipts(data["root"], index)
