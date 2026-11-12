import uuid

import pytest

from ai_edda.application import ApplicationService
from ai_edda.project_store import ProjectStore


def command(view, kind, payload=None):
    return {
        "command_id": str(uuid.uuid4()),
        "base_project_revision": view.project.revision,
        "expected_grid_revision": view.grid.revision,
        "expected_chart_revisions": {c.id: c.revision for c in view.charts},
        "kind": kind,
        "payload": payload or {},
    }


def test_note_history_and_replay(tmp_path):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(str(uuid.uuid4()), {})
        pid = initial.project.id
        create = command(
            initial,
            "create_event",
            {
                "chart_id": initial.charts[0].id,
                "time_ref": {"kind": "audio", "time_sec": 1},
                "lanes": [0],
            },
        )
        created = service.execute(pid, create, "a")
        undo = command(created, "undo")
        undone = service.execute(pid, undo, "a")
        assert not undone.charts[0].events
        assert service.execute(pid, create, "a") == created
        assert service.execute(pid, undo, "a") == undone
        redone = service.execute(pid, command(undone, "redo"), "a")
        assert redone.charts[0].events == created.charts[0].events
        assert (
            initial.project.revision
            < created.project.revision
            < undone.project.revision
            < redone.project.revision
        )
        with pytest.raises(ValueError, match="HISTORY_EMPTY"):
            service.execute(pid, command(redone, "redo"), "a")


def test_sessions_redo_and_conflict(tmp_path):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(str(uuid.uuid4()), {})
        pid = initial.project.id
        first = service.execute(pid, command(initial, "update_song_metadata", {"title": "A"}), "a")
        other = service.execute(pid, command(first, "update_song_metadata", {"artist": "B"}), "b")
        undone = service.execute(pid, command(other, "undo"), "a")
        assert undone.project.metadata.artist == "B"
        assert undone.project.metadata.title == initial.project.metadata.title
        changed = service.execute(pid, command(undone, "update_song_metadata", {"title": "C"}), "a")
        with pytest.raises(ValueError, match="HISTORY_EMPTY"):
            service.execute(pid, command(changed, "redo"), "a")
        changed = service.execute(
            pid, command(changed, "update_song_metadata", {"title": "D"}), "b"
        )
        with pytest.raises(ValueError, match="UNDO_CONFLICT"):
            service.execute(pid, command(changed, "undo"), "a")
        assert service.get(pid) == changed


def test_commit_failure_and_receipt_reconciliation(tmp_path, monkeypatch):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(str(uuid.uuid4()), {})
        pid = initial.project.id
        original = store.commit

        def fail(*args, **kwargs):
            raise RuntimeError("before commit")

        monkeypatch.setattr(store, "commit", fail)
        with pytest.raises(RuntimeError):
            service.execute(pid, command(initial, "update_song_metadata", {"title": "A"}), "a")
        with pytest.raises(ValueError, match="HISTORY_EMPTY"):
            service.execute(pid, command(initial, "undo"), "a")

        def publish_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("after commit")

        monkeypatch.setattr(store, "commit", publish_then_fail)
        cmd = command(initial, "update_song_metadata", {"title": "A"})
        committed = service.execute(pid, cmd, "a")
        assert committed == service.get(pid) == service.execute(pid, cmd, "a")
        monkeypatch.setattr(store, "commit", original)
        undone = service.execute(pid, command(committed, "undo"), "a")
        with pytest.raises(ValueError, match="HISTORY_EMPTY"):
            service.execute(pid, command(undone, "undo"), "a")


def test_pending_history_survives_receipt_lookup_failure(tmp_path, monkeypatch):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(str(uuid.uuid4()), {})
        pid = initial.project.id
        original_commit = store.commit
        original_lookup = store.lookup_result

        def publish_then_fail(*args, **kwargs):
            original_commit(*args, **kwargs)
            raise RuntimeError("after commit")

        monkeypatch.setattr(store, "commit", publish_then_fail)
        lookup_calls = 0

        def fail_lookup(*args, **kwargs):
            nonlocal lookup_calls
            lookup_calls += 1
            if lookup_calls == 2:
                raise RuntimeError("lookup unavailable")
            return original_lookup(*args, **kwargs)

        monkeypatch.setattr(store, "lookup_result", fail_lookup)
        cmd = command(initial, "update_song_metadata", {"title": "A"})
        with pytest.raises(RuntimeError, match="lookup unavailable"):
            service.execute(pid, cmd, "a")
        monkeypatch.setattr(store, "lookup_result", original_lookup)
        committed = service.get(pid)
        assert committed.project.metadata.title == "A"
        replayed = service.execute(pid, cmd, "a")
        assert replayed == committed
        undone = service.execute(pid, command(replayed, "undo"), "a")
        assert undone.project.metadata.title == initial.project.metadata.title
