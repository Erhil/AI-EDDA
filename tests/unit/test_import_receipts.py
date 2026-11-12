from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_edda.import_receipts import (
    build_import_receipt,
    publish_import_receipt,
    resolve_import_receipt,
    validate_native_import_receipts,
)


def receipt():
    project = str(uuid4())
    context = {
        "request_id": str(uuid4()),
        "archive_kind": "native",
        "file_name": "a.zip",
        "file_size": 1,
    }
    return build_import_receipt(context, "job", "a" * 64, project, 0, "b" * 64, "c" * 64)


def test_publish_retry_conflict_and_cleanup(tmp_path):
    record = receipt()
    publish_import_receipt(tmp_path, record)
    publish_import_receipt(tmp_path, record)
    assert resolve_import_receipt(tmp_path, record.request_id) == record
    with pytest.raises(ValueError):
        publish_import_receipt(tmp_path, record.model_copy(update={"job_id": "other"}))
    assert len(list((tmp_path / "imports").iterdir())) == 1


def test_strict_types_and_historical_revision(tmp_path):
    record = receipt()
    with pytest.raises(ValueError):
        type(record).model_validate({**record.model_dump(), "version": True})
    publish_import_receipt(tmp_path, record)

    def snapshot(rev):
        return SimpleNamespace(project=SimpleNamespace(id=record.project_id, revision=rev))

    history = SimpleNamespace(project_id=record.project_id, heads=(snapshot(0), snapshot(2)))
    assert validate_native_import_receipts(tmp_path, history)[record.request_id] == record
    with pytest.raises(RuntimeError):
        validate_native_import_receipts(
            tmp_path, history, cancel=lambda: (_ for _ in ()).throw(RuntimeError())
        )
