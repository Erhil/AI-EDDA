from fastapi.testclient import TestClient

from ai_edda.access import OwnerAuth
from ai_edda.api.app import create_app


def test_health_and_frontend(tmp_path):
    OwnerAuth.setup(tmp_path / "config" / "owner.json", "secret")
    with TestClient(create_app(tmp_path / "projects", tmp_path / "config")) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/missing").status_code == 404
        assert client.get("/").status_code == 200
        assert client.get("/assets/missing.js").status_code == 404
