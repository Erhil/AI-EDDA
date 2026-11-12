import copy
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_archive_upload_router_review import wait_job
from test_export_jobs_review import counted_worker
from test_native_project import complete as complete
from test_native_project import package as package

from ai_edda.access import OwnerAuth
from ai_edda.api import exports
from ai_edda.api.app import create_app
from ai_edda.native_capture import capture_inventory, hash_inventory
from ai_edda.native_zip import write_native_zip


@pytest.fixture
def app_source(complete, tmp_path):
    root, _ = complete
    config = tmp_path / "api-config"
    OwnerAuth.setup(config / "owner.json", "secret")
    app = create_app(root.parent, config)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/api/session", json={"password": "secret"}).status_code == 200
        view = client.get("/api/projects/" + root.name).json()
        lease = client.post(
            f"/api/projects/{root.name}/writer-lease",
            json={"tab_id": "owner-tab", "action": "acquire"},
        ).json()["lease_id"]
        app.state.coordinator.handlers["project_export"]["worker"] = counted_worker
        yield {
            "app": app,
            "client": client,
            "root": root,
            "config": config,
            "view": view,
            "headers": {"X-Tab-Id": "owner-tab", "X-Writer-Lease": lease},
        }


@pytest.fixture
def published(app_source, request):
    value = app_source
    kind = getattr(request, "param", "native")
    body = {
        "request_id": str(uuid4()),
        "kind": kind,
        "project_revision": value["view"]["project"]["revision"],
    }
    if kind == "game":
        body.update(
            chart_ids=[chart["id"] for chart in value["view"]["charts"]], folder_name="song"
        )
    base = f"/api/projects/{value['root'].name}/exports"
    response = value["client"].post(base, json=body, headers=value["headers"])
    assert response.status_code == 202, response.text
    job = wait_job(value["app"].state.coordinator, response.json()["job_id"])
    eid = response.json()["export_id"]
    folder = (
        value["root"] / "exports" / eid
        if kind == "game"
        else value["config"] / "exports" / value["root"].name / eid
    )
    return {
        **value,
        "kind": kind,
        "body": body,
        "job": job,
        "eid": eid,
        "folder": folder,
        "url": base + "/" + eid,
    }


@pytest.mark.parametrize("published", ["native", "game"], indirect=True)
def test_real_export_response_download_and_later_edit_without_regeneration(published, monkeypatch):
    value = published
    client, root, job = value["client"], value["root"], value["job"]
    original = client.get(value["url"])
    assert original.status_code == 200, original.text
    metadata = original.json()
    public = job["publication_result"]
    assert set(metadata) == set(public) | {"publication", "download_url", "server_folder"}
    assert {key: metadata[key] for key in public} == public
    assert metadata["publication"]["public_result"] == public
    assert metadata["download_url"] == value["url"] + "/download"
    assert metadata["server_folder"] == (
        str((value["folder"] / "song").resolve()) if value["kind"] == "game" else None
    )
    archive = value["folder"] / ("song.zip" if value["kind"] == "game" else "backup.zip")
    expected = archive.read_bytes()
    service = value["app"].state.service
    view = service.get(root.name)
    service.execute(
        root.name,
        {
            "command_id": str(uuid4()),
            "kind": "update_song_metadata",
            "payload": {"title": "Later"},
            "base_project_revision": view.project.revision,
            "expected_grid_revision": view.grid.revision,
            "expected_chart_revisions": {chart.id: chart.revision for chart in view.charts},
        },
    )
    # A different tab owns the current writer lease; immutable reads remain owner-only.
    takeover = client.post(
        f"/api/projects/{root.name}/writer-lease",
        json={"tab_id": "different", "action": "takeover"},
    )
    assert (
        client.post(
            f"/api/projects/{root.name}/exports", json=value["body"], headers=value["headers"]
        ).status_code
        == 409
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            service.store, "load", lambda *args: pytest.fail("replay read the latest head")
        )
        replay = client.post(
            f"/api/projects/{root.name}/exports",
            json=value["body"],
            headers={"X-Tab-Id": "different", "X-Writer-Lease": takeover.json()["lease_id"]},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json() == {"job_id": job["id"], "export_id": value["eid"]}
    original_read = Path.read_bytes

    def bounded_read(path):
        assert path.suffix != ".zip", "server must stream ZIPs rather than call read_bytes"
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", bounded_read)
    monkeypatch.setattr(
        exports,
        "reserve_project_export",
        lambda *args: pytest.fail("immutable read tried to regenerate"),
    )
    assert client.get(value["url"]).json() == metadata
    download = client.get(metadata["download_url"])
    assert download.status_code == 200 and download.content == expected
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]
    assert int((value["config"] / ("builder-count-" + job["id"])).read_text()) == (
        1 if value["kind"] == "game" else 0
    )
    client.delete("/api/session")
    assert client.get(value["url"]).status_code == 401
    assert client.get(metadata["download_url"]).status_code == 401


@pytest.mark.parametrize(
    "fault,status", [("missing", 404), ("malformed", 422), ("uppercase", 422), ("traversal", 422)]
)
def test_missing_malformed_and_path_ids(published, fault, status):
    value = published
    eid = (
        str(uuid4())
        if fault == "missing"
        else ("bad" if fault == "malformed" else value["eid"].upper())
    )
    if fault == "traversal":
        eid = "..bad"
    url = f"/api/projects/{value['root'].name}/exports/{eid}"
    assert value["client"].get(url).status_code == status
    assert value["client"].get(url + "/download").status_code == status


@pytest.mark.parametrize("fault", ["publication-json", "archive-hash", "route-identity"])
def test_corrupt_export_returns_503_without_other_location_fallback(published, monkeypatch, fault):
    value = published
    if fault == "publication-json":
        (value["folder"] / "publication.json").write_bytes(b"{private-invalid")
    elif fault == "archive-hash":
        path = value["folder"] / "backup.zip"
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        metadata = value["client"].get(value["url"]).json()["publication"]
        metadata["public_result"]["export_id"] = str(uuid4())
        monkeypatch.setattr(exports, "resolve_native_export", lambda *args: metadata)
    assert value["client"].get(value["url"]).status_code == 503
    assert value["client"].get(value["url"] + "/download").status_code == 503


def test_both_export_locations_are_an_error_even_when_one_is_valid(published):
    value = published
    other = value["root"] / "exports" / value["eid"]
    other.mkdir(parents=True)
    assert value["client"].get(value["url"]).status_code == 503
    assert value["client"].get(value["url"] + "/download").status_code == 503


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
@pytest.mark.parametrize(
    "location", ["dangling-game-leaf", "game-parent", "project-parent", "native-parent"]
)
def test_reparse_destination_or_parent_never_falls_back_to_valid_native(
    published, tmp_path, location
):
    value = published
    root = value["root"]
    outside = tmp_path / "outside"
    outside.mkdir()
    holding = None
    if location in ("project-parent", "native-parent"):
        original = root if location == "project-parent" else value["folder"].parent
        holding = original.with_name("holding-" + uuid4().hex)
        original.rename(holding)
        junction, target = original, holding
    else:
        exports_dir = root / "exports"
        if location == "game-parent":
            junction, target = exports_dir, outside
        else:
            exports_dir.mkdir()
            junction, target = exports_dir / value["eid"], outside
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if location == "dangling-game-leaf":
        outside.rmdir()
    try:
        statuses = [
            value["client"].get(value["url"] + suffix).status_code for suffix in ("", "/download")
        ]
        assert statuses == [503, 503]
        assert junction.lstat().st_file_attributes & 0x400
    finally:
        junction.rmdir()
        if holding is not None:
            holding.rename(original)


def test_store_directory_failure_is_reported_as_storage_503(published, monkeypatch):
    value = published

    def fail(*args):
        raise OSError("store unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(value["app"].state.service.store, "_dir", fail)
        statuses = [
            value["client"].get(value["url"] + suffix).status_code for suffix in ("", "/download")
        ]
        assert statuses == [503, 503]


@pytest.mark.parametrize("project_id,status", [("missing", 404), ("bad", 422)])
def test_project_route_ids_on_immutable_reads(app_source, project_id, status):
    value = app_source
    project_id = str(uuid4()) if project_id == "missing" else project_id
    url = f"/api/projects/{project_id}/exports/{uuid4()}"
    for suffix in ("", "/download"):
        assert value["client"].get(url + suffix).status_code == status


def test_post_error_codes_auth_lease_stale_busy_global_conflict_and_strict_numbers(app_source):
    value = app_source
    client, root, app = value["client"], value["root"], value["app"]
    url = f"/api/projects/{root.name}/exports"
    body = {
        "request_id": str(uuid4()),
        "kind": "native",
        "project_revision": value["view"]["project"]["revision"],
    }
    assert client.post(url, json=body).status_code == 409
    assert (
        client.post(
            url, json={**body, "project_revision": True}, headers=value["headers"]
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, json={**body, "project_revision": 999}, headers=value["headers"]
        ).status_code
        == 409
    )
    foreign = app.state.coordinator.reserve(
        "project_import",
        {
            "request_id": body["request_id"],
            "archive_kind": "native",
            "file_name": "a.zip",
            "file_size": 1,
        },
    )
    assert client.post(url, json=body, headers=value["headers"]).status_code == 409
    fresh = {**body, "request_id": str(uuid4())}
    assert client.post(url, json=fresh, headers=value["headers"]).status_code == 409
    assert app.state.coordinator.get(foreign)["state"] == "reserved"
    client.delete("/api/session")
    assert client.post(url, json=fresh, headers=value["headers"]).status_code == 401


@pytest.mark.parametrize("published", ["game"], indirect=True)
def test_imported_game_export_is_readable_without_original_local_export_job(published, tmp_path):
    value = published
    root = value["root"]
    inventory = capture_inventory(root, root.name, (root / "project.json").read_bytes())
    archive = tmp_path / "with-game-export.zip"
    write_native_zip(archive, inventory, hash_inventory(inventory))
    expected = value["client"].get(value["url"] + "/download").content
    config = tmp_path / "second-config"
    OwnerAuth.setup(config / "owner.json", "secret")
    other = create_app(tmp_path / "second-projects", config)
    with TestClient(other) as client:
        client.post("/api/session", json={"password": "secret"})
        body = archive.read_bytes()
        response = client.post(
            "/api/projects/imports",
            content=body,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Request-Id": str(uuid4()),
                "X-Archive-Kind": "native",
                "X-File-Name": "with-game-export.zip",
                "X-File-Size": str(len(body)),
            },
        )
        assert response.status_code == 202
        wait_job(other.state.coordinator, response.json()["job_id"])
        assert not any(job["kind"] == "project_export" for job in other.state.jobs.jobs.values())
        metadata = client.get(value["url"])
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["server_folder"] != str(value["folder"] / "song")
        assert client.get(value["url"] + "/download").content == expected


@pytest.mark.parametrize("kind", ["native", "game"])
def test_export_handlers_are_registered_before_pending_startup_recovery(complete, tmp_path, kind):
    root, _ = complete
    config = tmp_path / "startup-config"
    OwnerAuth.setup(config / "owner.json", "secret")
    first = create_app(root.parent, config)
    with TestClient(first) as client:
        client.post("/api/session", json={"password": "secret"})
        view = first.state.service.get(root.name)
        lease = client.post(
            f"/api/projects/{root.name}/writer-lease", json={"tab_id": "tab", "action": "acquire"}
        ).json()["lease_id"]
        body = {"request_id": str(uuid4()), "kind": kind, "project_revision": view.project.revision}
        if kind == "game":
            body.update(chart_ids=[chart.id for chart in view.charts], folder_name="song")
        response = client.post(
            f"/api/projects/{root.name}/exports",
            json=body,
            headers={"X-Tab-Id": "tab", "X-Writer-Lease": lease},
        )
        assert response.status_code == 202
        job = wait_job(first.state.coordinator, response.json()["job_id"])
        first.state.service.execute(
            root.name,
            {
                "command_id": str(uuid4()),
                "kind": "update_song_metadata",
                "payload": {"title": "Later"},
                "base_project_revision": view.project.revision,
                "expected_grid_revision": view.grid.revision,
                "expected_chart_revisions": {chart.id: chart.revision for chart in view.charts},
            },
        )
    pending = copy.deepcopy(job)
    pending["state"] = "publication_pending"
    pending.pop("publication_result", None)
    (config / "jobs" / (job["id"] + ".json")).write_text(json.dumps(pending), encoding="utf-8")
    restarted = create_app(root.parent, config)
    with TestClient(restarted):
        recovered = restarted.state.coordinator.get(job["id"])
        assert recovered["state"] == "completed", recovered
        assert recovered["publication_result"] == job["publication_result"]
        assert not restarted.state.coordinator.blocked
