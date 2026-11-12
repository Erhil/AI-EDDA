import json
import os
import subprocess
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_native_generation_review import generation as generation
from test_native_generation_review import inputs as inputs
from test_native_generation_review import media as media

from ai_edda.access import OwnerAuth
from ai_edda.api import catalogs
from ai_edda.api.app import create_app
from ai_edda.api.catalogs import create_catalog_router
from ai_edda.application import ApplicationService
from ai_edda.arrangement_domain import RhythmAnalysis
from ai_edda.domain import AudioAsset
from ai_edda.project_store import ProjectStore, _json


def identifier(number):
    return str(UUID(int=number))


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def client_for(service, tmp_path):
    OwnerAuth.setup(tmp_path / "catalog-auth.json", "secret")
    auth = OwnerAuth(tmp_path / "catalog-auth.json")
    app = FastAPI()
    app.include_router(create_catalog_router(service, auth))
    client = TestClient(app)
    client.cookies.set("ai_edda_session", auth.login("secret"))
    return client


@pytest.fixture
def api(tmp_path):
    with ProjectStore(tmp_path / "projects") as store:
        service = ApplicationService(store)
        initial = service.create_project(str(uuid4()), {})
        with client_for(service, tmp_path) as client:
            root = store._dir(initial.project.id)
            yield client, service, initial, root, f"/api/projects/{initial.project.id}/artifacts"


def analysis(root, number=1):
    name = identifier(number)
    audio = AudioAsset(
        path=f"audio/{identifier(1000)}/song.ogg",
        sha256="a" * 64,
        sample_rate=100,
        channels=1,
        frames=100,
        duration_sec=1,
        subtype="VORBIS",
    )
    model = RhythmAnalysis(id=name, revision=0, audio_asset_id=identifier(1000), audio=audio)
    folder = root / "analysis" / name
    folder.mkdir(parents=True)
    (folder / "analysis.json").write_bytes(_json(model.model_dump(mode="json")))
    return model, folder


def test_route_is_registered_without_starting_jobs(tmp_path):
    assert (
        "/api/projects/{project_id}/artifacts"
        in create_app(tmp_path / "projects", tmp_path / "config").openapi()["paths"]
    )


@pytest.mark.parametrize("kind", ["analysis", "draft"])
def test_empty_catalog_and_owner_auth_require_no_lease_or_files(api, kind):
    client, _, _, root, url = api
    before = files(root)
    response = client.get(url, params={"kind": kind})
    assert response.status_code == 200
    assert response.json() == {"kind": kind, "items": [], "next_cursor": None}
    client.cookies.clear()
    assert client.get(url, params={"kind": kind}).status_code == 401
    client.cookies.set("ai_edda_session", "invalid")
    assert client.get(url, params={"kind": kind}).status_code == 401
    assert files(root) == before


def test_sorted_pagination_defaults_and_fifty_item_limit_ignore_temporary_names(api):
    client, _, _, root, url = api
    for number in reversed(range(1, 56)):
        analysis(root, number)
    for name in (".staging-owned", "not-an-id", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"):
        (root / "analysis" / name).mkdir()
    before = files(root)
    first = client.get(url, params={"kind": "analysis"}).json()
    assert [item["id"] for item in first["items"]] == [identifier(n) for n in range(1, 21)]
    assert first["next_cursor"] == identifier(20)
    second = client.get(
        url, params={"kind": "analysis", "after": first["next_cursor"], "limit": 50}
    ).json()
    assert [item["id"] for item in second["items"]] == [identifier(n) for n in range(21, 56)]
    assert second["next_cursor"] is None
    maximum = client.get(url, params={"kind": "analysis", "limit": 50}).json()
    assert len(maximum["items"]) == 50 and maximum["next_cursor"] == identifier(50)
    assert (
        client.get(url, params={"kind": "analysis", "after": identifier(999)}).json()["items"] == []
    )
    assert files(root) == before


def test_only_requested_metadata_is_read_after_releasing_service_lock(api, monkeypatch):
    client, service, _, root, url = api
    for number in range(1, 6):
        analysis(root, number)
    read_paths = []
    original_read, original_load = catalogs._read, service.store.load

    def load(*args):
        assert service._lock._is_owned()
        return original_load(*args)

    def read(path, budget):
        assert not service._lock._is_owned()
        read_paths.append(path)
        return original_read(path, budget)

    monkeypatch.setattr(service.store, "load", load)
    monkeypatch.setattr(catalogs, "_read", read)
    response = client.get(url, params={"kind": "analysis", "after": identifier(1), "limit": 2})
    assert response.status_code == 200
    assert [path.parent.name for path in read_paths] == [identifier(2), identifier(3)]
    assert response.json()["next_cursor"] == identifier(3)


def test_missing_and_malformed_projects_are_reported_without_writes(api):
    client, _, _, root, _ = api
    before = files(root)
    assert (
        client.get("/api/projects/not-a-uuid/artifacts", params={"kind": "analysis"}).status_code
        == 422
    )
    assert (
        client.get(f"/api/projects/{uuid4()}/artifacts", params={"kind": "draft"}).status_code
        == 404
    )
    assert files(root) == before


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"kind": "other"},
        {"kind": "analysis", "limit": 0},
        {"kind": "analysis", "limit": 51},
        {"kind": "analysis", "limit": 1.5},
        {"kind": "analysis", "after": "bad"},
        {"kind": "analysis", "after": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
    ],
)
def test_bad_queries_are_422_without_writes(api, params):
    client, _, _, root, url = api
    before = files(root)
    assert client.get(url, params=params).status_code == 422
    assert files(root) == before


@pytest.mark.parametrize(
    "fault", ["missing", "json", "duplicate", "source-id", "identity", "coercion", "depth", "bytes"]
)
def test_damaged_canonical_analysis_remains_a_generic_item(api, monkeypatch, fault):
    client, _, _, root, url = api
    model, folder = analysis(root)
    path = folder / "analysis.json"
    raw = model.model_dump(mode="json")
    if fault == "missing":
        path.unlink()
    elif fault == "json":
        path.write_bytes(b"{broken with private path C:/private/secret")
    elif fault == "duplicate":
        path.write_bytes(b'{"duplicate":1,"duplicate":2,' + _json(raw)[1:])
    elif fault == "source-id":
        raw["audio_asset_id"] = "../private"
        path.write_bytes(_json(raw))
    elif fault == "identity":
        raw["id"] = str(uuid4())
        path.write_bytes(_json(raw))
    elif fault == "coercion":
        raw["audio"]["sample_rate"] = "100"
        path.write_bytes(_json(raw))
    elif fault == "depth":
        raw["extra"] = 0
        for _ in range(70):
            raw["extra"] = [raw["extra"]]
        path.write_bytes(_json(raw))
    else:
        monkeypatch.setattr(catalogs, "MAX_TOTAL_BYTES", 1)
    before = files(root)
    response = client.get(url, params={"kind": "analysis"})
    assert response.status_code == 200
    assert response.json()["items"] == [{"id": model.id, "error": "Artifact unavailable"}]
    assert "private" not in response.text and "secret" not in response.text
    assert files(root) == before


@pytest.mark.parametrize(
    "failure,status",
    [(FileNotFoundError(), 404), (KeyError(), 404), (OSError(), 503), (RuntimeError(), 503)],
)
def test_project_storage_errors_use_expected_codes(api, monkeypatch, failure, status):
    client, service, _, root, url = api

    def fail(*args):
        raise failure

    monkeypatch.setattr(service.store, "load", fail)
    before = files(root)
    assert client.get(url, params={"kind": "analysis"}).status_code == status
    assert files(root) == before


@pytest.mark.parametrize("entry_junction", [False, True])
@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
def test_windows_junction_catalog_and_entry_cannot_expose_outside_files(
    api, tmp_path, entry_junction
):
    client, _, _, root, url = api
    outside = tmp_path / "outside"
    outside.mkdir()
    model, folder = analysis(outside)
    catalog = root / "analysis"
    if entry_junction:
        catalog.mkdir()
    junction = catalog / model.id if entry_junction else catalog
    target = folder if entry_junction else outside / "analysis"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    before = files(outside)
    try:
        response = client.get(url, params={"kind": "analysis"})
        assert response.status_code == (200 if entry_junction else 503)
        if entry_junction:
            assert response.json()["items"] == [{"id": model.id, "error": "Artifact unavailable"}]
        assert files(outside) == before
    finally:
        junction.rmdir()


def test_real_publishers_list_historical_sources_without_reading_media_or_applicability(
    generation, tmp_path, monkeypatch
):
    root = generation["root"]
    with ProjectStore(root.parent) as store:
        service = ApplicationService(store)
        current = service.get(generation["source"].project.id)
        changed_project = current.project.model_copy(
            update={
                "metadata": current.project.metadata.model_copy(
                    update={"title": "Changed after publication"}
                ),
                "audio": None,
                "analysis": None,
                "plan_inputs": None,
            }
        )
        store.commit(
            current.project.id,
            str(uuid4()),
            {},
            current.project.revision,
            current.grid.revision,
            {chart.id: chart.revision for chart in current.charts},
            changed_project,
            current.grid,
            (),
            {"ok": True},
        )
        assert not service.get(current.project.id).charts
        before = files(root)
        original_open = type(root).open

        def guarded_open(path, *args, **kwargs):
            assert path.suffix not in (".npz", ".ogg", ".wav"), (
                "summary listing must not load media arrays"
            )
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(type(root), "open", guarded_open)
        with client_for(service, tmp_path) as client:
            base = f"/api/projects/{current.project.id}/artifacts"
            analyses = client.get(base, params={"kind": "analysis"}).json()
            drafts = client.get(base, params={"kind": "draft"}).json()
        monkeypatch.setattr(type(root), "open", original_open)
        assert {item["id"] for item in analyses["items"]} == {
            generation["analysis_id"],
            generation["orphan_analysis"],
        }
        assert all(
            item["audio_asset_id"] == generation["audio_ids"][0] and item["revision"] == 0
            for item in analyses["items"]
        )
        summaries = {item["id"]: item for item in drafts["items"]}
        assert set(summaries) == {generation["draft_id"], generation["rejected_id"]}
        assert summaries[generation["draft_id"]]["rejected"] is False
        assert summaries[generation["rejected_id"]]["rejected"] is True
        assert summaries[generation["draft_id"]]["event_count"] == len(
            generation["public"]["chart"]["events"]
        )
        assert summaries[generation["draft_id"]]["chart_id"] == generation["source"].charts[0].id
        assert all("applicable" not in item for item in summaries.values())
        assert files(root) == before


@pytest.mark.parametrize("fault", ["digest", "schema", "decision-id"])
def test_corrupt_rejection_does_not_hide_canonical_draft(generation, tmp_path, fault):
    root = generation["root"]
    path = root / "generation" / generation["rejected_id"] / "rejection.json"
    record = json.loads(path.read_bytes())
    if fault == "digest":
        record["draft_sha256"] = "0" * 64
    elif fault == "schema":
        record["extra"] = "private"
    else:
        record["decision_id"] = "not-a-uuid"
    path.write_bytes(_json(record))
    with (
        ProjectStore(root.parent) as store,
        client_for(ApplicationService(store), tmp_path) as client,
    ):
        response = client.get(
            f"/api/projects/{generation['source'].project.id}/artifacts", params={"kind": "draft"}
        )
    assert response.status_code == 200
    summaries = {item["id"]: item for item in response.json()["items"]}
    assert summaries[generation["rejected_id"]] == {
        "id": generation["rejected_id"],
        "error": "Artifact unavailable",
    }
    assert "error" not in summaries[generation["draft_id"]]
