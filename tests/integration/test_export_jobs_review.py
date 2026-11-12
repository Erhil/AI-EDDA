import copy
import json
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_native_project import complete as complete
from test_native_project import package as package

from ai_edda import export_jobs
from ai_edda.application import ApplicationService
from ai_edda.domain import HitEvent
from ai_edda.export_jobs import make_export_handlers, project_export_worker, reserve_project_export
from ai_edda.game_export_catalog import resolve_game_export
from ai_edda.jobs import BusyError
from ai_edda.native_export_catalog import resolve_native_export
from ai_edda.native_zip import read_native_zip
from ai_edda.project_store import ProjectStore
from ai_edda.work_coordinator import WorkCoordinator


def counted_worker(payload, progress, cancel):
    from ai_edda.packaging import ArtifactBuilder

    count = 0
    original = ArtifactBuilder.build

    def build(*args, **kwargs):
        nonlocal count
        count += 1
        return original(*args, **kwargs)

    ArtifactBuilder.build = build
    try:
        return project_export_worker(payload, progress, cancel)
    finally:
        workspace = Path(payload["payload"]["workspace"])
        (workspace.parent.parent / ("builder-count-" + workspace.name)).write_text(str(count))


def cancellation_window_worker(payload, progress, cancel):
    cancel.wait(10)
    return project_export_worker(payload, progress, cancel)


class ProbeCoordinator:
    def __init__(self, root):
        self.root = root
        self.jobs = SimpleNamespace(lock=RLock(), jobs={})
        self.writing = False
        self.started, self.cancelled = [], []

    def reserve(self, kind, context):
        job = uuid4().hex
        self.jobs.jobs[job] = {
            "id": job,
            "kind": kind,
            "context": copy.deepcopy(context),
            "state": "reserved",
        }
        return job

    @contextmanager
    def writer(self, job):
        path = self.root / job
        path.mkdir(parents=True)
        self.writing = True
        try:
            yield path
        finally:
            self.writing = False

    def start(self, job, payload):
        assert not self.writing
        self.started.append((job, payload))

    def cancel(self, job):
        self.cancelled.append(job)
        self.jobs.jobs[job]["state"] = "cancelled"


@pytest.fixture
def source(complete, tmp_path):
    root, _ = complete
    with ProjectStore(root.parent) as store:
        service = ApplicationService(store)
        view = service.get(root.name)
        coordinator = ProbeCoordinator(tmp_path / "probe-workspaces")
        yield root, store, service, view, coordinator


def request(view, kind="native", **changes):
    result = {"request_id": str(uuid4()), "kind": kind, "project_revision": view.project.revision}
    if kind == "game":
        result.update(chart_ids=[chart.id for chart in view.charts], folder_name="song")
    return {**result, **changes}


def command(view, kind, payload):
    return {
        "command_id": str(uuid4()),
        "kind": kind,
        "payload": payload,
        "base_project_revision": view.project.revision,
        "expected_grid_revision": view.grid.revision,
        "expected_chart_revisions": {chart.id: chart.revision for chart in view.charts},
    }


def wait_job(coordinator, jid):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = coordinator.get(jid)
        if job["state"] in ("completed", "failed", "cancelled"):
            assert job["state"] == "completed", job
            return job
        time.sleep(0.05)
    raise AssertionError("export job did not complete")


@pytest.mark.parametrize("kind", ["native", "game"])
def test_real_windows_spawn_pinned_export_publication_and_headless_recovery(source, tmp_path, kind):
    root, store, service, view, _ = source
    if kind == "native":
        current = store.load(view.project.id)
        invalid = current.charts[0].model_copy(
            update={"events": (HitEvent(time_ref={"kind": "audio", "time_sec": 0.1}),)}
        )
        store.commit(
            view.project.id,
            str(uuid4()),
            {},
            current.project.revision,
            current.grid.revision,
            {chart.id: chart.revision for chart in current.charts},
            current.project,
            current.grid,
            (invalid, *current.charts[1:]),
            {"ok": True},
        )
        view = service.get(view.project.id)
    captured_head = (root / "project.json").read_bytes()
    captured_files = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    config = tmp_path / "export-config"
    handlers = make_export_handlers(store, config)
    handlers["project_export"]["worker"] = counted_worker
    coordinator = WorkCoordinator(config, handlers)
    try:
        assert coordinator.jobs.ctx.get_start_method() == "spawn"
        req = request(view, kind)
        admitted = reserve_project_export(coordinator, service, view.project.id, req)
        later = service.execute(
            view.project.id, command(view, "update_song_metadata", {"title": "Later source title"})
        )
        job = wait_job(coordinator, admitted["job_id"])
        public = job["publication_result"]
        assert public["project_revision"] == view.project.revision < later.project.revision
        assert int((config / ("builder-count-" + admitted["job_id"])).read_text()) == (
            1 if kind == "game" else 0
        )
        if kind == "native":
            record = resolve_native_export(config, view.project.id, admitted["export_id"])
            archive = config / "exports" / view.project.id / admitted["export_id"] / "backup.zip"
            _, restored = read_native_zip(archive, tmp_path / "restored-backup")
            assert (restored / "project.json").read_bytes() == captured_head
            assert {
                path.relative_to(restored).as_posix(): path.read_bytes()
                for path in restored.rglob("*")
                if path.is_file()
            } == captured_files
        else:
            record = resolve_game_export(root, admitted["export_id"])
            info = json.loads(
                (root / "exports" / admitted["export_id"] / "song" / "info.dat").read_bytes()
            )
            assert info["_songName"] == view.project.metadata.title
        assert record["public_result"] == public
        workspace = coordinator._workspace(admitted["job_id"])
        deadline = time.monotonic() + 5
        while workspace.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not workspace.exists()
        head = root / "project.json"
        head_bytes = head.read_bytes()
        head.unlink()
        try:
            found, recovered = handlers["project_export"]["reconcile"](
                job["id"], job["context"], job["result"]
            )
            assert found and recovered == public
        finally:
            head.write_bytes(head_bytes)
    finally:
        coordinator.close()


@pytest.mark.parametrize(
    "state",
    ["reserved", "running", "ready", "completed", "publication_pending", "interrupted-result"],
)
def test_exact_replay_precedes_any_current_head_or_capture(source, monkeypatch, state):
    _, _, service, view, coordinator = source
    req = request(view)
    first = reserve_project_export(coordinator, service, view.project.id, req)
    job = coordinator.jobs.jobs[first["job_id"]]
    job["state"] = "interrupted" if state == "interrupted-result" else state
    if state == "interrupted-result":
        job["result"] = {"stored": True}

    def forbidden(*args):
        raise AssertionError("exact replay inspected the latest source")

    monkeypatch.setattr(service.store, "load", forbidden)
    monkeypatch.setattr(export_jobs, "capture_inventory", forbidden)
    assert reserve_project_export(coordinator, service, view.project.id, req) == first
    assert len(coordinator.started) == 1


@pytest.mark.parametrize("state", ["failed", "cancelled", "interrupted"])
def test_retry_after_failure_creates_new_job_and_export(source, state):
    _, _, service, view, coordinator = source
    req = request(view)
    first = reserve_project_export(coordinator, service, view.project.id, req)
    coordinator.jobs.jobs[first["job_id"]]["state"] = state
    second = reserve_project_export(coordinator, service, view.project.id, req)
    assert second["job_id"] != first["job_id"] and second["export_id"] != first["export_id"]


@pytest.mark.parametrize(
    "foreign_kind", ["project_import", "audio_import", "analysis", "generation", "other"]
)
def test_request_uuid_is_global_across_job_kinds(source, foreign_kind):
    _, _, service, view, coordinator = source
    req = request(view)
    coordinator.jobs.jobs["old"] = {
        "id": "old",
        "kind": foreign_kind,
        "state": "completed",
        "context": {"request_id": req["request_id"]},
    }
    with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
        reserve_project_export(coordinator, service, view.project.id, req)
    assert not coordinator.started


@pytest.mark.parametrize(
    "changes",
    [
        {"project_revision": True},
        {"project_revision": 0.0},
        {"project_revision": "0"},
        {"kind": "game", "chart_ids": []},
        {"chart_ids": [str(uuid4())]},
        {"extra": 1},
    ],
)
def test_request_numbers_and_selection_are_strict_before_source_access(
    source, monkeypatch, changes
):
    _, _, service, view, coordinator = source
    monkeypatch.setattr(
        service.store, "load", lambda *args: pytest.fail("invalid request read the source")
    )
    with pytest.raises(ValueError):
        reserve_project_export(coordinator, service, view.project.id, request(view, **changes))
    assert not coordinator.started


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "duplicate",
        "too-many",
        "audio",
        "cover",
        "rank",
        "speed",
        "collision",
        "stale-grid",
    ],
)
def test_invalid_game_selection_and_chart_content_rejected_before_reserving(
    source, monkeypatch, fault
):
    _, store, service, view, coordinator = source
    req = request(view, "game")
    snapshot = store.load(view.project.id)
    if fault == "unknown":
        req["chart_ids"] = [str(uuid4())]
    elif fault == "duplicate":
        req["chart_ids"] = [view.charts[0].id] * 2
    elif fault == "too-many":
        req["chart_ids"] = [str(uuid4()) for _ in range(4)]
    elif fault in ("audio", "cover"):
        snapshot = replace(snapshot, project=snapshot.project.model_copy(update={fault: None}))
    else:
        chart = snapshot.charts[0]
        if fault == "rank":
            chart = chart.model_copy(
                update={"metadata": chart.metadata.model_copy(update={"rank": 11})}
            )
        elif fault == "speed":
            chart = chart.model_copy(
                update={"metadata": chart.metadata.model_copy(update={"speed": -1})}
            )
        elif fault == "stale-grid":
            chart = chart.model_copy(update={"grid_revision": snapshot.grid.revision + 1})
        else:
            event = chart.events[0]
            other = event.model_copy(
                update={
                    "id": str(uuid4()),
                    "notes": (event.notes[0].model_copy(update={"id": str(uuid4())}),),
                }
            )
            chart = chart.model_copy(update={"events": (*chart.events, other)})
        snapshot = replace(snapshot, charts=(chart, *snapshot.charts[1:]))
    monkeypatch.setattr(store, "load", lambda *args: snapshot)
    with pytest.raises(ValueError):
        reserve_project_export(coordinator, service, view.project.id, req)
    assert not coordinator.jobs.jobs and not coordinator.started


@pytest.mark.parametrize("revision", [False, 0.0])
def test_replay_does_not_treat_corrupt_stored_revision_as_exact_metadata(source, revision):
    _, _, service, view, coordinator = source
    req = request(view)
    first = reserve_project_export(coordinator, service, view.project.id, req)
    coordinator.jobs.jobs[first["job_id"]]["context"]["project_revision"] = revision
    with pytest.raises(ValueError):
        reserve_project_export(coordinator, service, view.project.id, req)


def test_game_capture_is_detached_and_native_head_and_filenames_are_pinned(source):
    root, _, service, view, coordinator = source
    reserve_project_export(coordinator, service, view.project.id, request(view, "game"))
    game = coordinator.started[-1][1]["payload"]["snapshot"]
    original = copy.deepcopy(game)
    view = service.execute(
        view.project.id, command(view, "update_song_metadata", {"title": "Changed"})
    )
    assert game == original
    reserve_project_export(coordinator, service, view.project.id, request(view))
    inventory = coordinator.started[-1][1]["payload"]["inventory"]
    pinned_head, pinned_names = inventory.head, tuple(item[0] for item in inventory.files)
    service.execute(
        view.project.id, command(view, "update_song_metadata", {"artist": "Changed again"})
    )
    assert (
        inventory.head == pinned_head and tuple(item[0] for item in inventory.files) == pinned_names
    )
    assert (root / "project.json").read_bytes() != pinned_head


def test_jobs_service_store_lock_order_and_writer_close_before_start(source):
    _, store, service, view, coordinator = source
    trace = []

    class Lock:
        def __init__(self, name):
            self.name, self.inner = name, RLock()

        def __enter__(self):
            self.inner.acquire()
            trace.append("enter-" + self.name)
            return self

        def __exit__(self, *args):
            trace.append("exit-" + self.name)
            self.inner.release()

    coordinator.jobs.lock, service._lock, store._mutex = (
        Lock("jobs"),
        Lock("service"),
        Lock("store"),
    )
    reserve_project_export(coordinator, service, view.project.id, request(view))
    assert trace[:3] == ["enter-jobs", "enter-service", "enter-store"]
    assert trace[-3:] == ["exit-store", "exit-service", "exit-jobs"]
    assert not coordinator.writing and len(coordinator.started) == 1


def test_start_failure_cancels_only_new_reservation(source, monkeypatch):
    _, _, service, view, coordinator = source

    def fail(*args):
        raise OSError("spawn failed")

    monkeypatch.setattr(coordinator, "start", fail)
    with pytest.raises(OSError, match="spawn failed"):
        reserve_project_export(coordinator, service, view.project.id, request(view))
    assert coordinator.cancelled == list(coordinator.jobs.jobs)
    assert not coordinator.writing


def test_busy_slot_does_not_cancel_or_replace_foreign_job(source, tmp_path):
    _, store, service, view, _ = source
    config = tmp_path / "busy-config"
    coordinator = WorkCoordinator(config, make_export_handlers(store, config))
    try:
        foreign = coordinator.reserve("project_export", {"request_id": str(uuid4())})
        before = coordinator.get(foreign)
        with pytest.raises(BusyError):
            reserve_project_export(coordinator, service, view.project.id, request(view))
        assert coordinator.get(foreign) == before and coordinator.jobs.current == foreign
        assert list(coordinator.jobs.jobs) == [foreign]
    finally:
        coordinator.close()


@pytest.mark.parametrize("kind", ["native", "game"])
def test_cancellation_of_started_export_releases_slot_and_never_publishes(source, tmp_path, kind):
    root, store, service, view, _ = source
    config = tmp_path / "cancel-config"
    handlers = make_export_handlers(store, config)
    handlers["project_export"]["worker"] = cancellation_window_worker
    coordinator = WorkCoordinator(config, handlers)
    try:
        admitted = reserve_project_export(
            coordinator, service, view.project.id, request(view, kind)
        )
        assert coordinator.cancel(admitted["job_id"])
        assert coordinator.get(admitted["job_id"])["state"] == "cancelled"
        assert coordinator.jobs.current is None
        assert not (config / "exports" / view.project.id / admitted["export_id"]).exists()
        assert not (root / "exports" / admitted["export_id"]).exists()
    finally:
        coordinator.close()


@pytest.mark.parametrize("field", ["kind", "revision", "selection", "folder", "project"])
def test_same_uuid_with_changed_metadata_conflicts_before_source_read(source, monkeypatch, field):
    _, _, service, view, coordinator = source
    req = request(view, "game")
    reserve_project_export(coordinator, service, view.project.id, req)
    project_id = view.project.id
    changed = copy.deepcopy(req)
    if field == "kind":
        changed.update(kind="native", chart_ids=[], folder_name=None)
    elif field == "revision":
        changed["project_revision"] += 1
    elif field == "selection":
        changed["chart_ids"] = changed["chart_ids"][:1]
    elif field == "folder":
        changed["folder_name"] = "different"
    else:
        project_id = str(uuid4())
    monkeypatch.setattr(
        service.store, "load", lambda *args: pytest.fail("metadata conflict read source")
    )
    with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
        reserve_project_export(coordinator, service, project_id, changed)
