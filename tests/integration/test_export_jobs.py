import uuid

import pytest
from pydantic import ValidationError

from ai_edda.export_jobs import ExportRequest


def test_export_request_strict_selection():
    request_id = str(uuid.uuid4())
    chart = str(uuid.uuid4())
    assert ExportRequest(request_id=request_id, kind="native", project_revision=0).chart_ids == []
    assert ExportRequest(
        request_id=request_id,
        kind="game",
        project_revision=0,
        chart_ids=[chart],
        folder_name="song",
    ).chart_ids == [chart]
    for value in [
        dict(request_id=request_id.upper(), kind="native", project_revision=0),
        dict(request_id=request_id, kind="native", project_revision=True),
        dict(request_id=request_id, kind="native", project_revision=0, extra=True),
        dict(
            request_id=request_id,
            kind="game",
            project_revision=0,
            chart_ids=[chart, chart],
            folder_name="song",
        ),
        dict(
            request_id=request_id,
            kind="game",
            project_revision=0,
            chart_ids=[chart],
            folder_name="Song",
        ),
    ]:
        with pytest.raises(ValidationError):
            ExportRequest(**value)


def test_replay_conflict_retry_and_writer_order(tmp_path):
    import threading
    from contextlib import contextmanager
    from types import SimpleNamespace

    from ai_edda.domain import SongProject, TempoSegment, TimingGrid
    from ai_edda.export_jobs import reserve_project_export
    from ai_edda.project_store import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    project = SongProject()
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    store.create(project, grid)

    class Coordinator:
        def __init__(self):
            self.jobs = SimpleNamespace(lock=threading.RLock(), jobs={})
            self.open = False

        def reserve(self, kind, context):
            job_id = uuid.uuid4().hex
            self.jobs.jobs[job_id] = dict(id=job_id, kind=kind, context=context, state="reserved")
            return job_id

        @contextmanager
        def writer(self, job_id):
            self.open = True
            try:
                yield tmp_path
            finally:
                self.open = False

        def start(self, job_id, payload):
            assert not self.open
            self.payload = payload

        def cancel(self, job_id):
            self.jobs.jobs[job_id]["state"] = "cancelled"

    coordinator = Coordinator()
    service = SimpleNamespace(_lock=threading.RLock(), store=store)
    request = dict(request_id=str(uuid.uuid4()), kind="native", project_revision=0)
    first = reserve_project_export(coordinator, service, project.id, request)
    head = store._dir(project.id) / "project.json"
    captured = coordinator.payload["payload"]["inventory"].head
    head.write_text(head.read_text(encoding="utf8") + " ", encoding="utf8")
    assert coordinator.payload["payload"]["inventory"].head == captured
    assert reserve_project_export(coordinator, service, project.id, request) == first
    with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
        reserve_project_export(coordinator, service, project.id, {**request, "project_revision": 1})
    coordinator.jobs.jobs[first["job_id"]]["state"] = "failed"
    second = reserve_project_export(coordinator, service, project.id, request)
    assert second["export_id"] != first["export_id"]
    with pytest.raises(ValueError, match="STALE_REVISION"):
        reserve_project_export(
            coordinator,
            service,
            project.id,
            {**request, "request_id": str(uuid.uuid4()), "project_revision": 1},
        )
    store.close()


def test_game_selection_and_global_request_conflict(tmp_path):
    import threading
    from contextlib import contextmanager
    from types import SimpleNamespace

    from ai_edda.domain import (
        AudioAsset,
        Chart,
        ChartMetadata,
        CoverAsset,
        SongProject,
        TempoSegment,
        TimingGrid,
    )
    from ai_edda.export_jobs import reserve_project_export
    from ai_edda.project_store import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    chart = Chart(metadata=ChartMetadata(rank=2, speed=10))
    project = SongProject(
        audio=AudioAsset(
            path="a",
            sha256="a",
            sample_rate=1,
            channels=1,
            frames=1,
            duration_sec=1,
            subtype="VORBIS",
        ),
        cover=CoverAsset(path="c", sha256="b", width=512, height=512),
    )
    store.create(
        project,
        TimingGrid(
            segments=(
                TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),
            )
        ),
        (chart,),
    )

    class Coordinator:
        jobs = SimpleNamespace(lock=threading.RLock(), jobs={})

        def reserve(self, kind, context):
            job_id = uuid.uuid4().hex
            self.jobs.jobs[job_id] = dict(id=job_id, kind=kind, context=context, state="reserved")
            return job_id

        @contextmanager
        def writer(self, job_id):
            yield tmp_path

        def start(self, job_id, payload):
            self.payload = payload

        def cancel(self, job_id):
            pass

    coordinator = Coordinator()
    service = SimpleNamespace(_lock=threading.RLock(), store=store)
    request = dict(
        request_id=str(uuid.uuid4()),
        kind="game",
        project_revision=0,
        chart_ids=[chart.id],
        folder_name="song",
    )
    reserve_project_export(coordinator, service, project.id, request)
    frozen = coordinator.payload["payload"]["snapshot"]
    assert frozen["selected_charts"][0]["id"] == chart.id
    assert frozen["audio"]["path"] == "a"
    with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
        reserve_project_export(
            coordinator,
            service,
            project.id,
            dict(request_id=request["request_id"], kind="native", project_revision=0),
        )
    with pytest.raises(ValueError, match="Unknown chart"):
        reserve_project_export(
            coordinator,
            service,
            project.id,
            {**request, "request_id": str(uuid.uuid4()), "chart_ids": [str(uuid.uuid4())]},
        )
    store.close()
