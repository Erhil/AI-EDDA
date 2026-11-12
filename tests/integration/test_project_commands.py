import uuid

import pytest

from ai_edda.application import ApplicationService
from ai_edda.project_store import ProjectStore


def run(service, view, kind, payload):
    return service.execute(
        view.project.id,
        {
            "command_id": str(uuid.uuid4()),
            "base_project_revision": view.project.revision,
            "expected_grid_revision": view.grid.revision,
            "expected_chart_revisions": {c.id: c.revision for c in view.charts},
            "kind": kind,
            "payload": payload,
        },
    )


def test_metadata_and_charts(tmp_path):
    with ProjectStore(tmp_path) as store:
        s = ApplicationService(store)
        v = s.create_project(str(uuid.uuid4()), {})
        v = run(s, v, "update_song_metadata", {"title": "Song"})
        assert v.project.metadata.title == "Song"
        v = run(s, v, "create_chart", {"name": "New"})
        v = run(s, v, "update_chart_metadata", {"chart_id": v.charts[-1].id, "rank": 8})
        assert v.charts[-1].metadata.rank == 8
        with pytest.raises(ValueError):
            run(s, v, "update_chart_metadata", {"chart_id": v.charts[-1].id, "rank": 11})
        v = run(s, v, "duplicate_chart", {"chart_id": v.charts[-1].id})
        assert len(v.charts) == 3
        v = run(s, v, "delete_chart", {"chart_id": v.charts[-1].id})
        assert len(v.charts) == 2


def test_grid_preview_and_reinterpret(tmp_path):
    with ProjectStore(tmp_path) as store:
        s = ApplicationService(store)
        v = s.create_project(str(uuid.uuid4()), {})
        v = run(s, v, "create_chart", {})
        grid = v.grid.model_dump(mode="json")
        grid["segments"][0]["bpm_quarter"] = 150
        before = {
            p.name: p.read_bytes() for p in (tmp_path / v.project.id).iterdir() if p.is_file()
        }
        preview = s.preview(v.project.id, "apply_grid", {"grid": grid})
        assert all(c.review == "needs_review" for c in preview.charts)
        assert before == {
            p.name: p.read_bytes() for p in (tmp_path / v.project.id).iterdir() if p.is_file()
        }
        v = run(s, v, "apply_grid", {"grid": grid})
        v = run(s, v, "reinterpret_tempo", {"factor": 2})
        assert v.grid.segments[0].bpm_quarter == 300
        assert all(c.grid_revision == v.grid.revision for c in v.charts)


@pytest.mark.parametrize("speed", [True, "15"])
def test_chart_speed_rejects_bool_and_string_without_writes(tmp_path, speed):
    with ProjectStore(tmp_path) as store:
        s = ApplicationService(store)
        v = s.create_project(str(uuid.uuid4()), {})
        v = run(s, v, "create_chart", {})
        project_dir = tmp_path / v.project.id
        before = {p.name: p.read_bytes() for p in project_dir.iterdir() if p.is_file()}
        with pytest.raises(ValueError):
            run(s, v, "update_chart_metadata", {"chart_id": v.charts[0].id, "speed": speed})
        assert before == {p.name: p.read_bytes() for p in project_dir.iterdir() if p.is_file()}


@pytest.mark.parametrize("speed", [15, 15.5])
def test_chart_speed_accepts_positive_numeric_values(tmp_path, speed):
    with ProjectStore(tmp_path) as store:
        s = ApplicationService(store)
        v = s.create_project(str(uuid.uuid4()), {})
        v = run(s, v, "create_chart", {})
        v = run(s, v, "update_chart_metadata", {"chart_id": v.charts[0].id, "speed": speed})
        assert v.charts[0].metadata.speed == speed
