import uuid

import pytest

from ai_edda.application import ApplicationService
from ai_edda.project_store import ProjectStore


def cmd(view, kind, payload):
    return {
        "command_id": str(uuid.uuid4()),
        "base_project_revision": view.project.revision,
        "expected_grid_revision": view.grid.revision,
        "expected_chart_revisions": {c.id: c.revision for c in view.charts},
        "kind": kind,
        "payload": payload,
    }


def test_split_move_undo_and_previews(tmp_path):
    with ProjectStore(tmp_path) as store:
        s = ApplicationService(store)
        v = s.create_project(str(uuid.uuid4()), {})
        pid, cid = v.project.id, v.charts[0].id
        v = s.execute(
            pid,
            cmd(
                v,
                "create_event",
                {"chart_id": cid, "time_ref": {"kind": "audio", "time_sec": 1}, "lanes": [0, 1]},
            ),
            "a",
        )
        original = v.charts[0].events
        e = original[0]
        for kind, payload in [
            ("mirror_lanes", {"chart_id": cid, "note_ids": [e.notes[0].id]}),
            ("quantize_events", {"chart_id": cid, "event_ids": [e.id], "grid_division": 4}),
        ]:
            s.preview(pid, kind, payload)
            assert s.get(pid) == v
        v = s.execute(
            pid,
            cmd(
                v,
                "split_event",
                {"chart_id": cid, "event_id": e.id, "note_ids": [e.notes[0].id], "delta_lane": 2},
            ),
            "a",
        )
        assert len(v.charts[0].events) == 2
        v = s.execute(pid, cmd(v, "undo", {}), "a")
        assert v.charts[0].events == original


@pytest.mark.parametrize("lanes", [[True], [-1], [4], [1, 1]])
def test_invalid_lanes_do_not_write(tmp_path, lanes):
    with ProjectStore(tmp_path) as store:
        s = ApplicationService(store)
        v = s.create_project(str(uuid.uuid4()), {})
        pid, cid = v.project.id, v.charts[0].id
        v = s.execute(
            pid,
            cmd(
                v,
                "create_event",
                {"chart_id": cid, "time_ref": {"kind": "audio", "time_sec": 1}, "lanes": [0]},
            ),
        )
        before = {
            p.relative_to(tmp_path): p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file() and p.name != ".writer.lock"
        }
        with pytest.raises(ValueError):
            s.execute(
                pid,
                cmd(
                    v,
                    "set_event_lanes",
                    {"chart_id": cid, "event_id": v.charts[0].events[0].id, "lanes": lanes},
                ),
            )
        assert s.get(pid) == v
        assert {
            p.relative_to(tmp_path): p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file() and p.name != ".writer.lock"
        } == before
