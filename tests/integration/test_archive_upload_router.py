from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_edda.api import archives


def test_upload_closes_writer_before_start(tmp_path, monkeypatch):
    state = {"writing": False}

    class Coordinator:
        @contextmanager
        def writer(self, job_id):
            state["writing"] = True
            try:
                yield tmp_path
            finally:
                state["writing"] = False

        def cancel(self, job_id):
            state["cancelled"] = job_id

    monkeypatch.setattr(
        archives, "reserve_project_import", lambda *args: {"job_id": "job", "upload_required": True}
    )

    def start(coordinator, job_id):
        assert not state["writing"]
        assert (tmp_path / "source.zip").read_bytes() == b"zip"
        state["started"] = job_id

    monkeypatch.setattr(archives, "start_project_import", start)
    app = FastAPI()
    app.include_router(
        archives.create_archive_router(
            SimpleNamespace(store=None),
            SimpleNamespace(authenticate=lambda token: True),
            Coordinator(),
        )
    )
    response = TestClient(app).post(
        "/api/projects/imports",
        content=b"zip",
        cookies={"ai_edda_session": "token"},
        headers={
            "content-type": "application/octet-stream",
            "X-Request-Id": str(uuid4()),
            "X-Archive-Kind": "native",
            "X-File-Name": "test.zip",
            "X-File-Size": "3",
        },
    )
    assert response.status_code == 202
    assert state["started"] == "job"
