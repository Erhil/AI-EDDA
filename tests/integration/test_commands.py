import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from ai_edda.application import ApplicationService
from ai_edda.project_store import ProjectStore


def uid():
    return str(uuid.uuid4())


def command(view, kind="create_event", payload=None):
    chart = view.charts[0]
    return {
        "command_id": uid(),
        "base_project_revision": view.project.revision,
        "expected_grid_revision": view.grid.revision,
        "expected_chart_revisions": {chart.id: chart.revision},
        "kind": kind,
        "payload": payload
        if payload is not None
        else {"chart_id": chart.id, "time_ref": {"kind": "audio", "time_sec": 1}, "lanes": [0]},
    }


def test_create_retry_and_conflict_across_restart(tmp_path):
    request_id = uid()
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        first = service.create_project(request_id, {"title": "Song"})
        assert service.create_project(request_id, {"title": "Song"}) == first
        with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
            service.create_project(request_id, {"title": "Other"})
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        assert service.create_project(request_id, {"title": "Song"}) == first
        with pytest.raises(ValueError, match="REQUEST_ID_CONFLICT"):
            service.create_project(request_id, {"title": "Other"})


def test_retry_returns_original_result_and_rejects_changed_payload(tmp_path):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(uid(), {})
        create = command(initial)
        created = service.execute(initial.project.id, create)
        assert created == service.get(initial.project.id)
        note = created.charts[0].events[0].notes[0]
        delete = command(
            created, "delete_notes", {"chart_id": created.charts[0].id, "note_ids": [note.id]}
        )
        deleted = service.execute(initial.project.id, delete)
        assert all(note.id != n.id for e in deleted.charts[0].events for n in e.notes)
        assert service.execute(initial.project.id, create) == created
        changed = {**create, "payload": {**create["payload"], "lanes": [1]}}
        with pytest.raises(ValueError, match="COMMAND_ID_CONFLICT"):
            service.execute(initial.project.id, changed)
        assert service.get(initial.project.id) == deleted


@pytest.mark.parametrize("two_services", [False, True])
def test_concurrent_identical_commands_commit_once(tmp_path, two_services):
    with ProjectStore(tmp_path) as store:
        first = ApplicationService(store)
        second = ApplicationService(store) if two_services else first
        initial = first.create_project(uid(), {})
        cmd = command(initial)
        barrier = Barrier(2)

        def run(service):
            barrier.wait(timeout=5)
            return service.execute(initial.project.id, cmd)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (first, second)))
        assert results[0] == results[1] == first.get(initial.project.id)
        assert results[0].project.revision == 1
        assert len(results[0].charts[0].events) == 1
        assert len(list((tmp_path / initial.project.id).glob("receipt-*.json"))) == 1


@pytest.mark.parametrize(
    "field", ["base_project_revision", "expected_grid_revision", "expected_chart_revisions"]
)
def test_stale_revisions_reject_without_commit(tmp_path, field):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(uid(), {})
        cmd = command(initial)
        cmd[field] = {initial.charts[0].id: 1} if field == "expected_chart_revisions" else 1
        with pytest.raises(ValueError, match="STALE_REVISION"):
            service.execute(initial.project.id, cmd)
        assert service.get(initial.project.id) == initial


def test_unknown_payload_does_not_commit(tmp_path):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(uid(), {})
        cmd = command(initial, "unknown", {})
        with pytest.raises(ValueError, match="UNKNOWN_COMMAND"):
            service.execute(initial.project.id, cmd)
        assert service.get(initial.project.id) == initial
        assert not list((tmp_path / initial.project.id).glob("receipt-*.json"))


@pytest.mark.parametrize(
    ("kind", "payload_field", "bad_value"),
    [
        ("create_event", "lanes", [True]),
        ("create_event", "lanes", [1.0]),
        ("move_notes", "delta_lane", True),
        ("move_notes", "delta_lane", 1.0),
    ],
)
def test_non_integer_lane_inputs_reject_without_commit(tmp_path, kind, payload_field, bad_value):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(uid(), {})
        cmd = command(
            initial,
            kind,
            {
                "chart_id": initial.charts[0].id,
                "time_ref": {"kind": "audio", "time_sec": 1},
                "lanes": [0],
            },
        )
        if kind == "move_notes":
            created = service.execute(initial.project.id, command(initial))
            note = created.charts[0].events[0].notes[0]
            cmd = command(
                created,
                kind,
                {"chart_id": created.charts[0].id, "note_ids": [note.id], "delta_lane": 1},
            )
        cmd["payload"][payload_field] = bad_value
        with pytest.raises(ValueError):
            service.execute(initial.project.id, cmd)
        assert service.get(initial.project.id) == (created if kind == "move_notes" else initial)
        assert len(list((tmp_path / initial.project.id).glob("receipt-*.json"))) == (
            1 if kind == "move_notes" else 0
        )


def test_move_command_and_receipt_replay_after_restart(tmp_path):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(uid(), {})
        created = service.execute(initial.project.id, command(initial))
        note = created.charts[0].events[0].notes[0]
        move = command(
            created,
            "move_notes",
            {"chart_id": created.charts[0].id, "note_ids": [note.id], "delta_lane": 2},
        )
        moved = service.execute(initial.project.id, move)
        assert moved.charts[0].events[0].notes[0].lane == note.lane + 2
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        assert service.execute(initial.project.id, move) == moved
        assert service.get(initial.project.id) == moved


def test_malformed_known_payload_does_not_commit(tmp_path):
    with ProjectStore(tmp_path) as store:
        service = ApplicationService(store)
        initial = service.create_project(uid(), {})
        cmd = command(initial, "create_event", {"chart_id": initial.charts[0].id, "lanes": [0]})
        with pytest.raises(ValueError):
            service.execute(initial.project.id, cmd)
        assert service.get(initial.project.id) == initial
        assert not list((tmp_path / initial.project.id).glob("receipt-*.json"))
