from uuid import uuid4

from fastapi.testclient import TestClient
from test_archive_upload_router_review import wait_job

from ai_edda.access import OwnerAuth
from ai_edda.api.app import create_app


def test_native_export_api(tmp_path):
    config = tmp_path / "config"
    OwnerAuth.setup(config / "owner.json", "secret")
    app = create_app(tmp_path / "projects", config)
    with TestClient(app) as client:
        assert client.get("/api/projects/bad/exports/bad").status_code == 401
        client.post("/api/session", json={"password": "secret"})
        view = client.post(
            "/api/projects", json={"request_id": str(uuid4()), "metadata": {}}
        ).json()
        pid = view["project"]["id"]
        base = f"/api/projects/{pid}"
        body = {"request_id": str(uuid4()), "kind": "native", "project_revision": 0}
        assert client.post(base + "/exports", json=body).status_code == 409
        lease = client.post(
            base + "/writer-lease", json={"tab_id": "a", "action": "acquire"}
        ).json()["lease_id"]
        headers = {"X-Tab-Id": "a", "X-Writer-Lease": lease}
        response = client.post(base + "/exports", json=body, headers=headers)
        assert response.status_code == 202, response.text
        job = wait_job(app.state.coordinator, response.json()["job_id"])
        assert job["state"] == "completed", job
        url = base + "/exports/" + response.json()["export_id"]
        metadata = client.get(url)
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["server_folder"] is None
        download = client.get(metadata.json()["download_url"])
        assert download.status_code == 200
        assert (
            download.content
            == (config / "exports" / pid / response.json()["export_id"] / "backup.zip").read_bytes()
        )
        assert client.get(base + "/exports/bad").status_code == 422
        assert client.get(base + "/exports/" + str(uuid4())).status_code == 404
