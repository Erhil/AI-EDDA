import os
import threading
import uuid

import pytest

from ai_edda.domain import SongProject, TempoSegment, TimingGrid
from ai_edda.project_store import ProjectStore


def test_create_commit_retry_restart(tmp_path):
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    project = SongProject()
    with ProjectStore(tmp_path) as store:
        store.create(project, grid)
        assert store.load(project.id).project == project
        assert store.commit(
            project.id, "command", {"x": 1}, 0, 0, {}, project, grid, (), {"ok": True}
        ) == {"ok": True}
    with ProjectStore(tmp_path) as store:
        assert store.commit(
            project.id, "command", {"x": 1}, 0, 0, {}, project, grid, (), {"ok": False}
        ) == {"ok": True}
        assert store.load(project.id).project.revision == 1


def test_result_factory_receives_allocated_snapshot_and_retry_uses_receipt(tmp_path):
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    project = SongProject()
    calls = []
    with ProjectStore(tmp_path) as store:
        store.create(project, grid)

        def factory(snapshot):
            calls.append(snapshot)
            return {"revision": snapshot.project.revision, "grid": snapshot.grid.revision}

        assert store.commit(
            project.id, "first", {"x": 1}, 0, 0, {}, project, grid, (), factory
        ) == {"revision": 1, "grid": 1}
        assert store.commit(
            project.id, "second", {"x": 2}, 1, 1, {}, project, grid, (), {"ok": True}
        ) == {"ok": True}
    with ProjectStore(tmp_path) as store:
        assert store.commit(
            project.id,
            "first",
            {"x": 1},
            0,
            0,
            {},
            project,
            grid,
            (),
            lambda _: pytest.fail("factory called on retry"),
        ) == {"grid": 1, "revision": 1}
    assert len(calls) == 1


def test_result_factory_failure_keeps_head_and_allows_retry(tmp_path):
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    project = SongProject()
    with ProjectStore(tmp_path) as store:
        store.create(project, grid)
        with pytest.raises(ValueError):
            store.commit(
                project.id, "command", {"x": 1}, 0, 0, {}, project, grid, (), lambda _: float("nan")
            )
        assert store.load(project.id).project.revision == 0
        assert store.commit(
            project.id,
            "command",
            {"x": 1},
            0,
            0,
            {},
            project,
            grid,
            (),
            lambda snapshot: {"revision": snapshot.project.revision},
        ) == {"revision": 1}


def test_create_failure_can_retry_but_existing_project_rejects(tmp_path, monkeypatch):
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    project = SongProject()
    with ProjectStore(tmp_path) as store:
        original = store._write
        failed = False

        def fail_head(path, value, replace=False):
            nonlocal failed
            if path.name == "project.json" and not failed:
                failed = True
                raise OSError("injected create failure")
            return original(path, value, replace)

        monkeypatch.setattr(store, "_write", fail_head)
        with pytest.raises(OSError, match="injected"):
            store.create(project, grid)
        assert not (tmp_path / project.id).exists()
        monkeypatch.setattr(store, "_write", original)
        store.create(project, grid)
        with pytest.raises(FileExistsError):
            store.create(project, grid)


def test_close_waits_for_active_commit(tmp_path, monkeypatch):
    grid = TimingGrid(
        segments=(TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),)
    )
    store = ProjectStore(tmp_path)
    project = SongProject()
    store.create(project, grid)
    entered = threading.Event()
    resume = threading.Event()
    original_write = store._write

    def blocked_write(path, value, replace=False):
        if path.name == "project.json" and replace:
            entered.set()
            assert resume.wait(5)
        return original_write(path, value, replace)

    monkeypatch.setattr(store, "_write", blocked_write)
    errors = []

    def commit():
        try:
            store.commit(project.id, "command", {"x": 1}, 0, 0, {}, project, grid, (), {"ok": True})
        except Exception as exc:
            errors.append(exc)

    writer = threading.Thread(target=commit)
    writer.start()
    assert entered.wait(5)
    closer = threading.Thread(target=store.close)
    closer.start()
    closer.join(0.05)
    assert closer.is_alive()
    assert not store._lockfile.closed
    resume.set()
    writer.join(5)
    closer.join(5)
    assert not writer.is_alive()
    assert not closer.is_alive()
    assert errors == []
    with pytest.raises(RuntimeError, match="Store is closed"):
        store.commit(project.id, "later", {"x": 2}, 1, 0, {}, project, grid, (), {"ok": True})


def test_dir_rejects_uuid_junction_outside_root(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    pid = str(uuid.uuid4())
    junction = root / pid
    try:
        os.symlink(outside, junction, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlink privilege is unavailable")
    with ProjectStore(root) as store:
        with pytest.raises(ValueError, match="reparse point|escapes store root"):
            store._dir(pid)
