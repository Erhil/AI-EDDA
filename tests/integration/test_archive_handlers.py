import copy

import pytest

from ai_edda import archive_handlers
from ai_edda.import_worker import import_worker


def test_projection_and_worker(monkeypatch, tmp_path):
    context = {"request_id": "id", "archive_kind": "native", "file_name": "a.zip", "file_size": 1}
    original = copy.deepcopy(context)
    calls = []
    monkeypatch.setattr(
        archive_handlers, "publish_project_import", lambda *args: calls.append(args)
    )
    monkeypatch.setattr(
        archive_handlers, "reconcile_project_import", lambda *args: calls.append(args)
    )
    handlers = archive_handlers.make_import_handlers("store")["project_import"]
    assert handlers["worker"] is import_worker
    handlers["publish"](context, "result", tmp_path)
    handlers["reconcile"]("job", context, "result")
    assert calls == [
        ("store", {"request": original}, "result", tmp_path),
        ("store", "job", {"request": original}, "result"),
    ]
    assert context == original


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"request": {}},
        {
            "request_id": "id",
            "archive_kind": "native",
            "file_name": "a",
            "file_size": 1,
            "extra": True,
        },
    ],
)
def test_invalid_shape_is_rejected_before_publication(monkeypatch, context, tmp_path):
    monkeypatch.setattr(
        archive_handlers, "publish_project_import", lambda *args: pytest.fail("published")
    )
    with pytest.raises(ValueError):
        archive_handlers.make_import_handlers(None)["project_import"]["publish"](
            context, {}, tmp_path
        )
