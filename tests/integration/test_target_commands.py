import uuid

import pytest

from ai_edda.application import ApplicationService
from ai_edda.domain import (
    AudioAsset,
    AudioTimeRef,
    Chart,
    ChartMetadata,
    ChartNote,
    CoverAsset,
    HitEvent,
    ObjectRef,
    SongProject,
    TempoSegment,
    TimingGrid,
)
from ai_edda.project_store import ProjectStore
from ai_edda.target_catalog import publish_target, resolve_target


def command(view, kind, payload=None):
    return {
        "command_id": str(uuid.uuid4()),
        "base_project_revision": view.project.revision,
        "expected_grid_revision": view.grid.revision,
        "expected_chart_revisions": {c.id: c.revision for c in view.charts},
        "kind": kind,
        "payload": payload or {},
    }


def target(**changes):
    return {
        "id": str(uuid.uuid4()),
        "version": 1,
        "name": "Target",
        "movement_profile": "future-backend-control",
        **changes,
    }


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def project(tmp_path):
    with ProjectStore(tmp_path) as store:
        event = HitEvent(
            time_ref=AudioTimeRef(kind="audio", time_sec=1), notes=(ChartNote(lane=2),)
        )
        first = Chart(
            metadata=ChartMetadata(name="Selected", rank=5, speed=15),
            events=(event,),
            review="approved",
        )
        second = Chart(
            metadata=ChartMetadata(name="Other", rank=7),
            target_profile=ObjectRef(id=str(uuid.uuid4()), revision=4),
            review="approved",
        )
        song = SongProject(
            audio=AudioAsset(
                path="audio/source/song.ogg",
                sha256="a" * 64,
                sample_rate=48000,
                channels=2,
                frames=96000,
                duration_sec=2,
                subtype="VORBIS",
            ),
            cover=CoverAsset(path="cover/source/cover.png", sha256="b" * 64, width=16, height=16),
            analysis=ObjectRef(id=str(uuid.uuid4()), revision=2),
            plan_inputs=ObjectRef(id=str(uuid.uuid4()), revision=0),
        )
        grid = TimingGrid(
            segments=(
                TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),
            )
        )
        store.create(song, grid, (first, second))
        service = ApplicationService(store)
        initial = service.get(song.id)
        yield store, service, initial, store._dir(song.id)


def content(chart):
    return chart.model_dump(exclude={"revision", "grid_revision"})


def test_preview_has_no_writes_or_history_and_apply_preserves_other_state(project):
    store, service, initial, root = project
    profile = target()
    payload = {"chart_id": initial.charts[0].id, "target": profile}
    before = files(root)
    preview = service.preview(initial.project.id, "set_chart_target", payload)
    assert files(root) == before
    assert service.get(initial.project.id) == initial
    assert preview.charts[0].target_profile == ObjectRef(id=profile["id"], revision=1)
    assert preview.charts[0].review == "needs_review"
    assert preview.charts[1] == initial.charts[1]
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(initial.project.id, command(initial, "undo"))
    result = service.execute(initial.project.id, command(initial, "set_chart_target", payload))
    assert (
        resolve_target(root, result.charts[0].target_profile).movement_profile
        == profile["movement_profile"]
    )
    assert result.project.revision == initial.project.revision + 1
    assert content(result.charts[1]) == content(initial.charts[1])
    assert result.charts[0].model_dump(
        exclude={"revision", "grid_revision", "target_profile", "review"}
    ) == initial.charts[0].model_dump(
        exclude={"revision", "grid_revision", "target_profile", "review"}
    )
    assert result.project.model_dump(
        exclude={"revision", "grid_revision", "chart_heads"}
    ) == initial.project.model_dump(exclude={"revision", "grid_revision", "chart_heads"})
    assert result.grid.model_dump(exclude={"revision"}) == initial.grid.model_dump(
        exclude={"revision"}
    )


def test_set_clear_each_has_one_undo_step_and_redo(project):
    _, service, initial, root = project
    pid, cid = initial.project.id, initial.charts[0].id
    assign = command(initial, "set_chart_target", {"chart_id": cid, "target": target()})
    assigned = service.execute(pid, assign)
    assert service.execute(pid, assign) == assigned
    undone = service.execute(pid, command(assigned, "undo"))
    assert undone.charts[0].target_profile is None
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(pid, command(undone, "undo"))
    redone = service.execute(pid, command(undone, "redo"))
    assert redone.charts[0].target_profile == assigned.charts[0].target_profile
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(pid, command(redone, "redo"))
    catalog_before = files(root / "targets")
    cleared = service.execute(
        pid, command(redone, "set_chart_target", {"chart_id": cid, "target": None})
    )
    assert cleared.charts[0].target_profile is None
    assert cleared.charts[0].review == "needs_review"
    assert files(root / "targets") == catalog_before
    restored = service.execute(pid, command(cleared, "undo"))
    assert restored.charts[0].target_profile == assigned.charts[0].target_profile
    recleared = service.execute(pid, command(restored, "redo"))
    assert recleared.charts[0].target_profile is None


@pytest.mark.parametrize(
    "change",
    [
        {"chart_id": str(uuid.uuid4())},
        {"chart_id": "../chart"},
        {"chart_id": True},
        {"target": False},
        {"target": []},
        {"target": {"version": True, "name": "bad"}},
        {"target": {"id": "../target", "version": 1, "name": "bad"}},
        {"extra": "forbidden"},
    ],
)
def test_bad_payloads_and_missing_chart_leave_project_unchanged(project, change):
    _, service, initial, root = project
    payload = {"chart_id": initial.charts[0].id, "target": target(), **change}
    before = files(root)
    for operation in ("preview", "execute"):
        with pytest.raises(ValueError):
            if operation == "preview":
                service.preview(initial.project.id, "set_chart_target", payload)
            else:
                service.execute(initial.project.id, command(initial, "set_chart_target", payload))
        assert service.get(initial.project.id) == initial
        assert files(root) == before


@pytest.mark.parametrize("missing", ["chart_id", "target"])
def test_payload_requires_both_fields(project, missing):
    _, service, initial, root = project
    payload = {"chart_id": initial.charts[0].id, "target": None}
    del payload[missing]
    before = files(root)
    with pytest.raises(ValueError):
        service.execute(initial.project.id, command(initial, "set_chart_target", payload))
    assert files(root) == before


@pytest.mark.parametrize("corrupt", [False, True])
def test_preview_is_pure_but_apply_rejects_existing_catalog_conflict(project, corrupt):
    _, service, initial, root = project
    profile = target()
    publish_target(root, profile)
    path = root / "targets" / f"{profile['id']}-1.json"
    if corrupt:
        path.write_bytes(b"{broken")
    payload = {"chart_id": initial.charts[0].id, "target": {**profile, "name": "Different"}}
    before = files(root)
    preview = service.preview(initial.project.id, "set_chart_target", payload)
    assert preview.charts[0].target_profile == ObjectRef(id=profile["id"], revision=1)
    assert files(root) == before
    with pytest.raises(ValueError, match="INVALID_TARGET_CATALOG"):
        service.execute(initial.project.id, command(initial, "set_chart_target", payload))
    assert files(root) == before
    assert service.get(initial.project.id) == initial


def test_failed_commit_leaves_only_valid_catalog_record_and_retry_is_safe(project, monkeypatch):
    store, service, initial, root = project
    profile = target()
    cmd = command(
        initial, "set_chart_target", {"chart_id": initial.charts[0].id, "target": profile}
    )
    head_before = (root / "project.json").read_bytes()

    def fail(*args, **kwargs):
        # Target publication must complete before the project can reference it.
        assert resolve_target(root, {"id": profile["id"], "revision": 1}).name == profile["name"]
        raise OSError("commit unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(store, "commit", fail)
        with pytest.raises(OSError, match="commit unavailable"):
            service.execute(initial.project.id, cmd)
    assert service.get(initial.project.id) == initial
    assert (root / "project.json").read_bytes() == head_before
    assert not list(root.glob("receipt-*.json"))
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(initial.project.id, command(initial, "undo"))
    retried = service.execute(initial.project.id, cmd)
    assert retried.charts[0].target_profile == ObjectRef(id=profile["id"], revision=1)
    assert len(list((root / "targets").iterdir())) == 1


def test_receipt_replay_after_later_changes_never_reapplies_target(project):
    store, service, initial, root = project
    pid, cid = initial.project.id, initial.charts[0].id
    profile = target()
    cmd = command(initial, "set_chart_target", {"chart_id": cid, "target": profile})
    assigned = service.execute(pid, cmd, "target-session")
    changed = service.execute(
        pid, command(assigned, "update_song_metadata", {"title": "Later"}), "other-session"
    )
    cleared = service.execute(
        pid,
        command(changed, "set_chart_target", {"chart_id": cid, "target": None}),
        "other-session",
    )
    # Receipt replay must not republish or validate the old catalog record.
    (root / "targets" / f"{profile['id']}-1.json").write_bytes(b"{broken")
    before = files(root)
    assert service.execute(pid, cmd, "target-session") == assigned
    assert ApplicationService(store).execute(pid, cmd) == assigned
    assert service.get(pid) == cleared
    assert files(root) == before
    with pytest.raises(ValueError, match="COMMAND_ID_CONFLICT"):
        service.execute(pid, {**cmd, "payload": {"chart_id": cid, "target": None}})


def test_commit_response_failure_reconciles_one_history_step(project, monkeypatch):
    store, service, initial, root = project
    pid = initial.project.id
    cmd = command(
        initial, "set_chart_target", {"chart_id": initial.charts[0].id, "target": target()}
    )
    original = store.commit

    def commit_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("response unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(store, "commit", commit_then_fail)
        assigned = service.execute(pid, cmd)
    assert service.get(pid) == assigned
    assert service.execute(pid, cmd) == assigned
    assert len(list(root.glob("receipt-*.json"))) == 1
    undone = service.execute(pid, command(assigned, "undo"))
    assert undone.charts[0].target_profile is None
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(pid, command(undone, "undo"))


def test_undo_preserves_later_changes_from_other_writer(project):
    _, service, initial, _ = project
    pid = initial.project.id
    assigned = service.execute(
        pid,
        command(
            initial, "set_chart_target", {"chart_id": initial.charts[0].id, "target": target()}
        ),
        "target-session",
    )
    changed = service.execute(
        pid,
        command(
            assigned,
            "update_chart_metadata",
            {"chart_id": initial.charts[1].id, "name": "Later chart"},
        ),
        "other-session",
    )
    undone = service.execute(pid, command(changed, "undo"), "target-session")
    assert undone.charts[0].target_profile is None
    assert undone.charts[1].metadata.name == "Later chart"
    assert undone.project.audio == initial.project.audio
    assert undone.project.analysis == initial.project.analysis
    assert undone.project.plan_inputs == initial.project.plan_inputs
