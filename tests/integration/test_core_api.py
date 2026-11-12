import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_edda.access import OwnerAuth, WriterLeases
from ai_edda.api.core import create_router
from ai_edda.application import ApplicationService
from ai_edda.project_store import ProjectStore


def test_core_flow(tmp_path):
    OwnerAuth.setup(tmp_path / "auth.json", "secret")
    with ProjectStore(tmp_path / "store") as store:
        app = FastAPI()
        app.include_router(
            create_router(
                ApplicationService(store), OwnerAuth(tmp_path / "auth.json"), WriterLeases()
            )
        )
        client = TestClient(app)
        assert client.get("/api/projects").status_code == 401
        assert client.post("/api/session", json={"password": "wrong"}).status_code == 401
        login = client.post("/api/session", json={"password": "secret"})
        assert login.status_code == 200 and "httponly" in login.headers["set-cookie"].lower()
        assert client.get("/api/session").status_code == 200
        view = client.post(
            "/api/projects", json={"request_id": str(uuid.uuid4()), "metadata": {}}
        ).json()
        pid = view["project"]["id"]
        base = "/api/projects/" + pid
        assert client.get(base + "/charts/" + view["charts"][0]["id"]).status_code == 200
        assert (
            client.post(
                base + "/writer-lease", json={"tab_id": "", "action": "acquire"}
            ).status_code
            == 422
        )
        a = client.post(base + "/writer-lease", json={"tab_id": "a", "action": "acquire"}).json()[
            "lease_id"
        ]
        assert (
            client.post(
                base + "/writer-lease", json={"tab_id": "b", "action": "acquire"}
            ).status_code
            == 409
        )
        b = client.post(base + "/writer-lease", json={"tab_id": "b", "action": "takeover"}).json()[
            "lease_id"
        ]
        for kind, payload in [
            ("duplicate_chart", {"chart_id": "missing"}),
            ("delete_chart", {"chart_id": "missing"}),
            ("update_chart_metadata", {"chart_id": "missing", "name": "renamed"}),
        ]:
            missing = {
                "command_id": str(uuid.uuid4()),
                "base_project_revision": 0,
                "expected_grid_revision": 0,
                "expected_chart_revisions": {c["id"]: c["revision"] for c in view["charts"]},
                "kind": kind,
                "payload": payload,
            }
            response = client.post(
                base + "/commands", json=missing, headers={"X-Tab-Id": "b", "X-Writer-Lease": b}
            )
            assert response.status_code == 404
        envelope = {
            "command_id": str(uuid.uuid4()),
            "base_project_revision": 0,
            "expected_grid_revision": 0,
            "expected_chart_revisions": {c["id"]: c["revision"] for c in view["charts"]},
            "kind": "update_song_metadata",
            "payload": {"title": "Song"},
        }
        assert (
            client.post(
                base + "/commands", json=envelope, headers={"X-Tab-Id": "a", "X-Writer-Lease": a}
            ).status_code
            == 409
        )
        before = client.get(base).json()
        assert (
            client.post(
                base + "/preview", json={"kind": envelope["kind"], "payload": envelope["payload"]}
            ).status_code
            == 200
        )
        assert client.get(base).json() == before
        headers = {"X-Tab-Id": "b", "X-Writer-Lease": b}
        assert client.post(base + "/commands", json=envelope, headers=headers).status_code == 200
        envelope["command_id"] = str(uuid.uuid4())
        assert client.post(base + "/commands", json=envelope, headers=headers).status_code == 409
        assert client.delete("/api/session").status_code == 200
        assert client.get("/api/projects").status_code == 401
