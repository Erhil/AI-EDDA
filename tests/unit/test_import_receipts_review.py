"""Import receipt boundaries and immutable publication with owned fixtures."""

import hashlib
import json
from uuid import uuid4

import pytest

from ai_edda.application import ApplicationService
from ai_edda.import_receipts import (
    ImportReceipt,
    build_import_receipt,
    publish_import_receipt,
    resolve_import_receipt,
    validate_native_import_receipts,
)
from ai_edda.native_history import validate_native_history
from ai_edda.project_store import ProjectStore


@pytest.fixture
def context():
    return {
        "request_id": str(uuid4()),
        "archive_kind": "native",
        "file_name": "карта.zip",
        "file_size": 1,
    }


def build(context, project_id=None, revision=0):
    return build_import_receipt(
        context, "job-label", "a" * 64, project_id or str(uuid4()), revision, "b" * 64, "c" * 64
    )


def test_request_hash_matches_exact_canonical_request_metadata(context):
    expected = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    assert build(context).request_sha256 == expected
    reordered = dict(reversed(tuple(context.items())))
    assert build(reordered).request_sha256 == expected


@pytest.mark.parametrize(
    "field, value",
    [
        ("file_size", True),
        ("file_size", "1"),
        ("file_size", 1.0),
        ("file_size", 0),
        ("file_size", -1),
        ("file_size", 1024**3 + 1),
        ("archive_kind", "other"),
        ("request_id", "not-a-uuid"),
        ("file_name", ""),
        ("file_name", ".."),
        ("file_name", "../x.zip"),
        ("file_name", "C:x.zip"),
        ("file_name", "dir/x.zip"),
        ("file_name", "dir\\x.zip"),
        ("file_name", "\\server\\share.zip"),
        ("file_name", " x.zip"),
        ("file_name", "x.zip\n"),
        ("file_name", "x\x00.zip"),
    ],
)
def test_request_metadata_is_strict(context, field, value):
    with pytest.raises(ValueError):
        build({**context, field: value})


@pytest.mark.parametrize("size", [1, 1024**3])
def test_request_size_boundaries_are_inclusive(context, size):
    assert build({**context, "file_size": size}).request_sha256


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_request_context_has_exact_four_keys(context, change):
    if change == "missing":
        context.pop("file_name")
    else:
        context["extra"] = 0
    with pytest.raises(ValueError):
        build(context)


@pytest.mark.parametrize(
    "field, value",
    [
        ("version", True),
        ("version", 1.0),
        ("version", "1"),
        ("request_id", str(uuid4()).upper()),
        ("project_id", "bad"),
        ("job_id", ""),
        ("job_id", 1),
        ("archive_sha256", "A" * 64),
        ("request_sha256", "x" * 64),
        ("initial_head_sha256", "b" * 63),
        ("initial_inventory_sha256", 1),
    ],
)
def test_receipt_raw_schema_is_strict(context, field, value):
    raw = build(context).model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValueError):
        ImportReceipt.model_validate(raw)


@pytest.mark.parametrize(
    "field, value",
    [
        ("project_id", str(uuid4())),
        ("revision", True),
        ("revision", 0.0),
        ("revision", "0"),
        ("archive_kind", "unknown"),
    ],
)
def test_public_result_matches_receipt_schema(context, field, value):
    raw = build(context).model_dump(mode="json")
    raw["public_result"][field] = value
    with pytest.raises(ValueError):
        ImportReceipt.model_validate(raw)


@pytest.mark.parametrize(
    "mutation", ["version", "job_id", "hash", "nested_revision", "nested_kind"]
)
def test_publisher_revalidates_existing_and_constructed_models(tmp_path, context, mutation):
    record = build(context)
    if mutation == "nested_revision":
        record = record.model_copy(
            update={"public_result": record.public_result.model_copy(update={"revision": True})}
        )
    elif mutation == "nested_kind":
        record = record.model_copy(
            update={
                "public_result": record.public_result.model_copy(update={"archive_kind": "unknown"})
            }
        )
    else:
        update = (
            {"version": True}
            if mutation == "version"
            else {"job_id": 17}
            if mutation == "job_id"
            else {"archive_sha256": "bad"}
        )
        record = record.model_copy(update=update)
    with pytest.raises(ValueError):
        publish_import_receipt(tmp_path, record)
    assert not (tmp_path / "imports").exists()


def test_invalid_constructed_request_id_cannot_escape_imports_directory(tmp_path, context):
    record = build(context).model_copy(update={"request_id": "../escaped"})
    with pytest.raises(ValueError):
        publish_import_receipt(tmp_path, record)
    assert not (tmp_path / "escaped.json").exists()


def test_failure_cleanup_never_removes_existing_receipt(tmp_path, context, monkeypatch):
    import ai_edda.import_receipts as module

    record = build(context)
    publish_import_receipt(tmp_path, record)
    final = tmp_path / "imports" / f"{record.request_id}.json"
    before = final.read_bytes()
    for operation in ("fsync", "link"):
        with monkeypatch.context() as patch:

            def fail(*args, **kwargs):
                raise OSError("injected publication failure")

            patch.setattr(module.os, operation, fail)
            with pytest.raises(OSError, match="injected"):
                publish_import_receipt(tmp_path, record)
        assert final.read_bytes() == before
        assert list(final.parent.iterdir()) == [final]


def test_exact_retry_is_readable_and_conflict_preserves_original(tmp_path, context):
    record = build(context)
    assert publish_import_receipt(tmp_path, record) == record
    before = (tmp_path / "imports" / f"{record.request_id}.json").read_bytes()
    assert publish_import_receipt(tmp_path, record.model_dump(mode="json")) == record
    with pytest.raises(ValueError, match="conflict"):
        publish_import_receipt(tmp_path, record.model_copy(update={"archive_sha256": "d" * 64}))
    assert resolve_import_receipt(tmp_path, record.request_id) == record
    assert (tmp_path / "imports" / f"{record.request_id}.json").read_bytes() == before


@pytest.fixture
def historical(tmp_path, context):
    with ProjectStore(tmp_path / "store") as store:
        app = ApplicationService(store)
        view = app.create_project(str(uuid4()), {})
        root = store._dir(view.project.id)
        record = build(context, view.project.id, revision=0)
        publish_import_receipt(root, record)
        for index in range(2):
            view = app.execute(
                view.project.id,
                {
                    "command_id": str(index),
                    "kind": "update_song_metadata",
                    "payload": {"title": str(index)},
                    "base_project_revision": view.project.revision,
                    "expected_grid_revision": view.grid.revision,
                    "expected_chart_revisions": {c.id: c.revision for c in view.charts},
                },
            )
        history = validate_native_history(root, view.project.id)
    return root, history, record


def test_import_receipt_binds_historical_revision_without_rechecking_current_hash(historical):
    root, history, record = historical
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert history.materialize_current().project.revision == 2
    assert validate_native_import_receipts(root, history) == {record.request_id: record}
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("change", ["project", "revision"])
def test_import_receipt_requires_own_project_and_a_preserved_revision(historical, change):
    root, history, record = historical
    path = root / "imports" / f"{record.request_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if change == "project":
        raw["project_id"] = raw["public_result"]["project_id"] = str(uuid4())
    else:
        raw["public_result"]["revision"] = 99
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="history"):
        validate_native_import_receipts(root, history)


def test_native_receipt_budget_counts_each_file_once(historical, context, monkeypatch):
    import ai_edda.import_receipts as module

    root, history, record = historical
    publish_import_receipt(
        root, build({**context, "request_id": str(uuid4())}, record.project_id, revision=1)
    )
    total = sum(p.stat().st_size for p in (root / "imports").iterdir())
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", total)
    assert len(validate_native_import_receipts(root, history)) == 2
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", total - 1)
    with pytest.raises(ValueError, match="limit"):
        validate_native_import_receipts(root, history)


def test_cancel_callback_raises_during_native_enumeration(historical):
    root, history, _ = historical

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        validate_native_import_receipts(root, history, cancel)


def test_duplicate_json_and_nonregular_files_are_rejected(tmp_path, context):
    record = build(context)
    publish_import_receipt(tmp_path, record)
    path = tmp_path / "imports" / f"{record.request_id}.json"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw[:-1] + ',"version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        resolve_import_receipt(tmp_path, record.request_id)
    path.unlink()
    path.mkdir()
    with pytest.raises(ValueError, match="regular"):
        resolve_import_receipt(tmp_path, record.request_id)


def test_resolver_checks_unsafe_ancestor(tmp_path, context):
    record = build(context)
    project = tmp_path / "project"
    project.mkdir()
    publish_import_receipt(project, record)
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(project, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlink privilege is unavailable")
    with pytest.raises(ValueError, match="Unsafe"):
        resolve_import_receipt(redirected, record.request_id)
