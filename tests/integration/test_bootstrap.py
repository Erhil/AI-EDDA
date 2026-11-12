import uuid

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ai_edda.access import OwnerAuth
from ai_edda.api.app import create_app
from ai_edda.cli import app
from ai_edda.project_store import ProjectStore


def test_init_is_idempotent(tmp_path):
    runner = CliRunner()
    args = [
        "init",
        "--project-root",
        str(tmp_path / "projects"),
        "--config-root",
        str(tmp_path / "config"),
    ]
    first = runner.invoke(app, args, input="secret\nsecret\n")
    assert first.exit_code == 0, first.output
    owner = tmp_path / "config" / "owner.json"
    original = owner.read_bytes()
    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert owner.read_bytes() == original
    assert OwnerAuth(owner).login("secret")


def test_factory_login_create_and_lock(tmp_path):
    config, projects = tmp_path / "config", tmp_path / "projects"
    OwnerAuth.setup(config / "owner.json", "secret")
    with TestClient(create_app(projects, config)) as client:
        assert client.post("/api/session", json={"password": "secret"}).status_code == 200
        response = client.post(
            "/api/projects", json={"request_id": str(uuid.uuid4()), "metadata": {"title": "Song"}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["project"]["metadata"]["title"] == "Song"
        with pytest.raises(RuntimeError, match="locked"):
            with TestClient(create_app(projects, config)):
                pass
    store = ProjectStore(projects)
    store.close()


def test_missing_init(tmp_path):
    result = CliRunner().invoke(app, ["serve", "--config-root", str(tmp_path)])
    assert result.exit_code != 0
    assert "init" in result.output
