import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_edda import target_catalog
from ai_edda.access import OwnerAuth
from ai_edda.api.app import create_app
from ai_edda.api.targets import create_target_router
from ai_edda.application import ApplicationService
from ai_edda.project_store import ProjectStore


def command(view, profile):
    return {
        "command_id": str(uuid.uuid4()),
        "base_project_revision": view.project.revision,
        "expected_grid_revision": view.grid.revision,
        "expected_chart_revisions": {c.id: c.revision for c in view.charts},
        "kind": "set_chart_target",
        "payload": {"chart_id": view.charts[0].id, "target": profile},
    }


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def api(tmp_path):
    OwnerAuth.setup(tmp_path / "owner.json", "secret")
    auth = OwnerAuth(tmp_path / "owner.json")
    with ProjectStore(tmp_path / "projects") as store:
        service = ApplicationService(store)
        view = service.create_project(str(uuid.uuid4()), {})
        app = FastAPI()
        app.include_router(create_target_router(service, auth))
        with TestClient(app, raise_server_exceptions=False) as client:
            client.cookies.set("ai_edda_session", auth.login("secret"))
            url = f"/api/projects/{view.project.id}/charts/{view.charts[0].id}/target"
            yield client, service, view, store._dir(view.project.id), url


def test_app_registers_target_route(tmp_path):
    app = create_app(tmp_path / "projects", tmp_path / "config")
    assert "/api/projects/{project_id}/charts/{chart_id}/target" in app.openapi()["paths"]


def test_current_target_read_requires_no_lease_and_never_changes_head(api):
    client, service, initial, root, url = api
    before = files(root)
    response = client.get(url)
    assert response.status_code == 200
    assert response.json() == {
        "project_id": initial.project.id,
        "project_revision": initial.project.revision,
        "chart_id": initial.charts[0].id,
        "chart_revision": initial.charts[0].revision,
        "target_ref": None,
        "target": None,
    }
    assert files(root) == before
    profile = {
        "id": str(uuid.uuid4()),
        "version": 3,
        "name": "Current",
        "allowed_subdivisions": [2, 4],
    }
    assigned = service.execute(initial.project.id, command(initial, profile))
    before = files(root)
    response = client.get(url)
    assert response.status_code == 200
    result = response.json()
    assert result["project_revision"] == assigned.project.revision
    assert result["chart_revision"] == assigned.charts[0].revision
    assert result["target_ref"] == {"id": profile["id"], "revision": 3}
    assert result["target"] == target_catalog.resolve_target(root, result["target_ref"]).model_dump(
        mode="json"
    )
    assert files(root) == before
    cleared = service.execute(initial.project.id, command(assigned, None))
    assert client.get(url).json()["target"] is None
    assert client.get(url).json()["project_revision"] == cleared.project.revision


@pytest.mark.parametrize("token", [None, "invalid-session"])
def test_owner_authentication_precedes_lookup(api, token):
    client, _, _, root, url = api
    client.cookies.clear()
    if token:
        client.cookies.set("ai_edda_session", token)
    before = files(root)
    assert client.get(url).status_code == 401
    assert client.get("/api/projects/not-a-uuid/charts/not-a-uuid/target").status_code == 401
    assert files(root) == before


@pytest.mark.parametrize(
    "change,status",
    [("missing-project", 404), ("missing-chart", 404), ("bad-project", 422), ("bad-chart", 422)],
)
def test_missing_and_malformed_ids(api, change, status):
    client, _, view, root, _ = api
    pid = str(uuid.uuid4()) if change == "missing-project" else view.project.id
    cid = str(uuid.uuid4()) if change == "missing-chart" else view.charts[0].id
    if change == "bad-project":
        pid = "not-a-uuid"
    if change == "bad-chart":
        cid = "not-a-uuid"
    before = files(root)
    assert client.get(f"/api/projects/{pid}/charts/{cid}/target").status_code == status
    assert files(root) == before


@pytest.mark.parametrize(
    "corruption", ["missing", "json", "schema", "identity", "nonfinite", "huge-integer"]
)
def test_missing_and_corrupt_catalog_returns_422_without_writes(api, corruption):
    client, service, initial, root, url = api
    profile = {"id": str(uuid.uuid4()), "version": 1, "name": "Selected"}
    service.execute(initial.project.id, command(initial, profile))
    path = root / "targets" / f"{profile['id']}-1.json"
    if corruption == "missing":
        path.unlink()
    elif corruption == "json":
        path.write_bytes(b"{broken")
    else:
        change = {
            "schema": {"unknown": True},
            "identity": {"id": str(uuid.uuid4())},
            "nonfinite": {"target_hits_per_second": float("inf")},
            "huge-integer": {"target_hits_per_second": 10**400},
        }[corruption]
        path.write_text(json.dumps({**profile, **change}), encoding="utf-8")
    before = files(root)
    assert client.get(url).status_code == 422
    assert files(root) == before


@pytest.mark.parametrize("catalog_failure", [False, True])
def test_storage_failure_returns_503(api, monkeypatch, catalog_failure):
    client, service, initial, root, url = api

    def fail(*args, **kwargs):
        raise OSError("storage unavailable")

    if catalog_failure:
        service.execute(
            initial.project.id,
            command(initial, {"id": str(uuid.uuid4()), "version": 1, "name": "Selected"}),
        )
        monkeypatch.setattr(target_catalog.Path, "read_bytes", fail)
    else:
        monkeypatch.setattr(service.store, "load", fail)
    assert client.get(url).status_code == 503
