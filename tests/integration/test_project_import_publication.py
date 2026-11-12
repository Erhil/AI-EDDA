import uuid

import pytest

from ai_edda.import_receipts import build_import_receipt
from ai_edda.project_imports import _receipt


def test_receipt_binds_request_and_job():
    job = "a" * 32
    request = {
        "request_id": str(uuid.uuid4()),
        "archive_kind": "native",
        "file_name": "project.zip",
        "file_size": 12,
    }
    record = build_import_receipt(request, job, "b" * 64, str(uuid.uuid4()), 0, "c" * 64, "d" * 64)
    result = {"receipt": record.model_dump(mode="json")}
    assert _receipt({"request": request}, result, job) == (record, False)
    with pytest.raises(ValueError):
        _receipt({"request": request}, result, "e" * 32)
    assert _receipt({"request": request, "prior_receipt": record}, result, "e" * 32) == (
        record,
        True,
    )
    with pytest.raises(ValueError):
        _receipt({"request": {**request, "file_size": 13}}, result, job)
