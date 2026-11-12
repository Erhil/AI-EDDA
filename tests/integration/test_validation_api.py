import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_edda.access import OwnerAuth, WriterLeases
from ai_edda.api import core
from ai_edda.api.core import create_router
from ai_edda.application import ApplicationService
from ai_edda.domain import (
    AudioAsset,
    AudioTimeRef,
    Chart,
    ChartNote,
    HitEvent,
    SongProject,
    TempoSegment,
    TimingGrid,
)
from ai_edda.project_store import ProjectStore


def envelope(view, kind, payload=None):
    return {
        "command_id": str(uuid.uuid4()),
        "base_project_revision": view.project.revision,
        "expected_grid_revision": view.grid.revision,
        "expected_chart_revisions": {c.id: c.revision for c in view.charts},
        "kind": kind,
        "payload": payload or {},
    }


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def validation_api(tmp_path):
    OwnerAuth.setup(tmp_path / "auth.json", "secret")
    with ProjectStore(tmp_path / "store") as store:

        def event(time, lanes):
            return HitEvent(
                time_ref=AudioTimeRef(kind="audio", time_sec=time),
                notes=tuple(ChartNote(lane=lane) for lane in lanes),
            )

        clean = Chart(review="approved", events=(event(0.1, (0,)), event(0.2, (0,))))
        invalid = Chart(events=(event(3, ()), event(1, (0, 1, 2)), event(1, (0,))))
        audio = AudioAsset(
            path="audio/not-read/song.ogg",
            sha256="a" * 64,
            sample_rate=100,
            channels=1,
            frames=200,
            duration_sec=2,
            subtype="VORBIS",
        )
        project = SongProject(audio=audio)
        grid = TimingGrid(
            segments=(
                TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),
            )
        )
        store.create(project, grid, (clean, invalid))
        service = ApplicationService(store)
        auth, leases = OwnerAuth(tmp_path / "auth.json"), WriterLeases()
        app = FastAPI()
        app.include_router(create_router(service, auth, leases))
        with TestClient(app) as client:
            client.cookies.set("ai_edda_session", auth.login("secret"))
            yield client, service, store, service.get(project.id), store._dir(project.id), leases


def test_validation_and_preview_are_read_only(tmp_path):
    OwnerAuth.setup(tmp_path / "auth.json", "secret")
    with ProjectStore(tmp_path / "store") as store:
        service = ApplicationService(store)
        app = FastAPI()
        app.include_router(
            create_router(service, OwnerAuth(tmp_path / "auth.json"), WriterLeases())
        )
        client = TestClient(app)
        path = "/api/projects/" + str(uuid.uuid4())
        assert client.get(path + "/validation").status_code == 401
        assert (
            client.post(path + "/preview-validation", json={"kind": "x", "payload": {}}).status_code
            == 401
        )
        client.post("/api/session", json={"password": "secret"})
        assert client.get(path + "/validation").status_code == 404
        view = client.post(
            "/api/projects", json={"request_id": str(uuid.uuid4()), "metadata": {}}
        ).json()
        path = "/api/projects/" + view["project"]["id"]
        result = client.get(path + "/validation")
        assert result.status_code == 200
        assert result.json()["view"] == view
        before = {
            p.relative_to(tmp_path): p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file() and p.name != ".writer.lock"
        }
        result = client.post(
            path + "/preview-validation",
            json={"kind": "update_song_metadata", "payload": {"title": "Candidate"}},
        )
        assert result.status_code == 200
        assert result.json()["view"]["project"]["metadata"]["title"] == "Candidate"
        assert client.get(path).json() == view
        assert before == {
            p.relative_to(tmp_path): p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file() and p.name != ".writer.lock"
        }
        assert (
            client.post(
                path + "/preview-validation", json={"kind": "unknown", "payload": {}}
            ).status_code
            == 422
        )


def test_saved_invalid_charts_are_reported_with_exact_source_ids_without_lease_or_writes(
    validation_api, monkeypatch
):
    client, service, _, initial, root, leases = validation_api

    def forbidden(*args):
        raise AssertionError("read-only validation must not require a writer lease")

    monkeypatch.setattr(leases, "require", forbidden)
    before = files(root)
    response = client.get(f"/api/projects/{initial.project.id}/validation")
    assert response.status_code == 200
    result = response.json()
    assert result["view"] == initial.model_dump(mode="json")
    diagnostics = result["diagnostics"]
    bad = initial.charts[1]
    assert {item["chart_id"] for item in diagnostics} == {bad.id}
    assert {
        "OUTSIDE_AUDIO_DURATION",
        "INVALID_EVENT_SIZE",
        "DUPLICATE_LANE_TIME",
        "TOO_MANY_SIMULTANEOUS_NOTES",
        "NEEDS_REVIEW",
    } <= {item["code"] for item in diagnostics}
    for item in diagnostics:
        assert set(item["event_ids"]) <= {event.id for event in bad.events}
        assert set(item["note_ids"]) <= {note.id for event in bad.events for note in event.notes}
        assert item["severity"] in ("error", "warning")
        assert item["message"]
    assert files(root) == before and service.get(initial.project.id) == initial
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(initial.project.id, envelope(initial, "undo"))


def test_quantize_collision_is_visible_only_in_read_only_candidate_and_old_preview_schema_is_preserved(
    validation_api,
):
    client, service, _, initial, root, _ = validation_api
    chart = initial.charts[0]
    body = {
        "kind": "quantize_events",
        "payload": {
            "chart_id": chart.id,
            "event_ids": [event.id for event in chart.events],
            "grid_division": 1,
        },
    }
    base = f"/api/projects/{initial.project.id}"
    before = files(root)
    result = client.post(base + "/preview-validation", json=body)
    assert result.status_code == 200
    candidate = result.json()
    collision = [
        item
        for item in candidate["diagnostics"]
        if item["chart_id"] == chart.id and item["code"] == "DUPLICATE_LANE_TIME"
    ]
    assert len(collision) == 1
    assert set(collision[0]["event_ids"]) == {event.id for event in chart.events}
    assert set(collision[0]["note_ids"]) == {
        note.id for event in chart.events for note in event.notes
    }
    assert candidate["view"]["project"] == initial.project.model_dump(mode="json")
    assert candidate["view"]["charts"][0]["revision"] == chart.revision
    assert candidate["view"]["charts"][0]["events"][0]["time_ref"] == {
        "kind": "beat",
        "beat_q": 0,
        "micro_offset_sec": 0,
    }
    old = client.post(base + "/preview", json=body)
    assert old.status_code == 200
    assert set(old.json()) == {"project", "grid", "charts"}
    assert "view" not in old.json() and "diagnostics" not in old.json()
    assert files(root) == before and service.get(initial.project.id) == initial
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(initial.project.id, envelope(initial, "undo"))


def test_missing_audio_uses_unbounded_internal_duration_without_serializing_infinity(
    validation_api,
):
    client, service, store, initial, root, _ = validation_api
    current = store.load(initial.project.id)
    store.commit(
        initial.project.id,
        str(uuid.uuid4()),
        {},
        current.project.revision,
        current.grid.revision,
        {chart.id: chart.revision for chart in current.charts},
        current.project.model_copy(update={"audio": None}),
        current.grid,
        current.charts,
        lambda actual: actual.project.model_dump(mode="json"),
    )
    before = files(root)
    response = client.get(f"/api/projects/{initial.project.id}/validation")
    assert response.status_code == 200
    assert response.json()["view"]["project"]["audio"] is None
    assert not any(
        item["code"] == "OUTSIDE_AUDIO_DURATION" for item in response.json()["diagnostics"]
    )
    assert "Infinity" not in response.text and "NaN" not in response.text
    assert any(item["code"] == "INVALID_EVENT_SIZE" for item in response.json()["diagnostics"])
    assert files(root) == before


def test_saving_conflicting_chart_keeps_complete_navigation_ids(validation_api):
    client, service, _, initial, _, _ = validation_api
    chart = initial.charts[0]
    base = f"/api/projects/{initial.project.id}"
    lease = client.post(
        base + "/writer-lease", json={"tab_id": "validation-test", "action": "acquire"}
    )
    assert lease.status_code == 200
    response = client.post(
        base + "/commands",
        json=envelope(
            initial,
            "create_event",
            {
                "chart_id": chart.id,
                "time_ref": {"kind": "audio", "time_sec": 0.1},
                "lanes": [0, 1],
            },
        ),
        headers={"x-tab-id": "validation-test", "x-writer-lease": lease.json()["lease_id"]},
    )
    assert response.status_code == 200
    saved = service.get(initial.project.id)
    changed = next(item for item in saved.charts if item.id == chart.id)
    assert len(changed.events) == len(chart.events) + 1
    response = client.get(base + "/validation")
    assert response.status_code == 200
    conflicts = [
        item
        for item in response.json()["diagnostics"]
        if item["chart_id"] == chart.id
        and item["code"] in {"DUPLICATE_LANE_TIME", "TOO_MANY_SIMULTANEOUS_NOTES"}
    ]
    assert len(conflicts) == 2
    coincident = [event for event in changed.events if event.time_ref.time_sec == 0.1]
    for item in conflicts:
        assert item["event_ids"] == [event.id for event in coincident]
        expected_notes = [
            note.id
            for event in coincident
            for note in event.notes
            if item["code"] == "TOO_MANY_SIMULTANEOUS_NOTES" or note.lane == 0
        ]
        assert item["note_ids"] == expected_notes
    assert service.get(initial.project.id) == saved


@pytest.mark.parametrize("preview", [False, True])
def test_one_captured_view_is_used_when_canonical_revision_changes_during_validation(
    validation_api, monkeypatch, preview
):
    client, service, _, initial, _, _ = validation_api
    captured, changed = threading.Event(), threading.Event()
    failures, validated = [], []
    original = core.validate_chart

    def validate(chart, grid, duration):
        validated.append((chart.id, chart.revision, grid.revision, duration))
        if len(validated) == 1:
            captured.set()
            assert changed.wait(5), "concurrent writer did not finish"
        return original(chart, grid, duration)

    def writer():
        try:
            assert captured.wait(5)
            current = service.store.load(initial.project.id)
            updated = current.project.model_copy(
                update={
                    "metadata": current.project.metadata.model_copy(
                        update={"title": "Concurrent change"}
                    ),
                    "audio": current.project.audio.model_copy(
                        update={"duration_sec": 0.5, "frames": 50}
                    ),
                }
            )
            service.store.commit(
                initial.project.id,
                str(uuid.uuid4()),
                {},
                current.project.revision,
                current.grid.revision,
                {chart.id: chart.revision for chart in current.charts},
                updated,
                current.grid,
                current.charts,
                lambda actual: actual.project.model_dump(mode="json"),
            )
        except BaseException as exc:
            failures.append(exc)
        finally:
            changed.set()

    monkeypatch.setattr(core, "validate_chart", validate)
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        base = f"/api/projects/{initial.project.id}"
        response = (
            client.post(
                base + "/preview-validation",
                json={"kind": "update_song_metadata", "payload": {"artist": "Preview"}},
            )
            if preview
            else client.get(base + "/validation")
        )
        assert response.status_code == 200
    finally:
        thread.join(5)
    assert not thread.is_alive() and not failures
    result = response.json()
    assert result["view"]["project"]["revision"] == initial.project.revision
    assert result["view"]["project"]["metadata"]["title"] == initial.project.metadata.title
    assert result["view"]["project"]["metadata"]["artist"] == (
        "Preview" if preview else initial.project.metadata.artist
    )
    assert validated == [
        (chart.id, chart.revision, initial.grid.revision, 2) for chart in initial.charts
    ]
    assert {
        event_id
        for item in result["diagnostics"]
        if item["code"] == "OUTSIDE_AUDIO_DURATION"
        for event_id in item["event_ids"]
    } == {initial.charts[1].events[0].id}
    assert service.get(initial.project.id).project.metadata.title == "Concurrent change"
    assert service.get(initial.project.id).project.audio.duration_sec == 0.5


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize(
    "failure,status",
    [
        (FileNotFoundError(), 404),
        (KeyError(), 404),
        (ValueError("invalid"), 422),
        (ValueError("STALE_REVISION"), 409),
        (OSError(), 503),
        (RuntimeError(), 503),
    ],
)
def test_validation_endpoints_preserve_existing_error_mapping(
    validation_api, monkeypatch, preview, failure, status
):
    client, service, _, initial, root, _ = validation_api

    def fail(*args):
        raise failure

    monkeypatch.setattr(service, "preview" if preview else "get", fail)
    before = files(root)
    base = f"/api/projects/{initial.project.id}"
    response = (
        client.post(
            base + "/preview-validation", json={"kind": "update_song_metadata", "payload": {}}
        )
        if preview
        else client.get(base + "/validation")
    )
    assert response.status_code == status
    assert files(root) == before


@pytest.mark.parametrize("token", [None, "invalid"])
def test_both_validation_routes_require_owner_authentication(validation_api, token):
    client, _, _, initial, root, _ = validation_api
    client.cookies.clear()
    if token:
        client.cookies.set("ai_edda_session", token)
    before = files(root)
    base = f"/api/projects/{initial.project.id}"
    assert client.get(base + "/validation").status_code == 401
    assert (
        client.post(
            base + "/preview-validation", json={"kind": "unknown", "payload": {}}
        ).status_code
        == 401
    )
    assert files(root) == before
