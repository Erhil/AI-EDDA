import uuid
from types import SimpleNamespace

import pytest

from ai_edda.import_receipts import build_import_receipt
from ai_edda.project_imports import _marker, reconcile_project_import


def test_partial_wrapper_cleanup_and_foreign_preservation(tmp_path):
    job = "a" * 32
    request = {
        "request_id": str(uuid.uuid4()),
        "archive_kind": "native",
        "file_name": "project.zip",
        "file_size": 12,
    }
    receipt = build_import_receipt(request, job, "b" * 64, str(uuid.uuid4()), 0, "c" * 64, "d" * 64)
    store = SimpleNamespace(root=tmp_path, _dir=lambda project_id: tmp_path / project_id)
    context = {"request": request}
    result = {"receipt": receipt.model_dump(mode="json")}
    wrapper = tmp_path / (".import-" + job)
    wrapper.mkdir()
    (wrapper / "marker.json").write_bytes(b"foreign")
    with pytest.raises(RuntimeError):
        reconcile_project_import(store, job, context, result)
    assert wrapper.exists()
    (wrapper / "marker.json").write_bytes(_marker(job, receipt))
    assert reconcile_project_import(store, job, context, result) == (False, None)
    assert not wrapper.exists()
