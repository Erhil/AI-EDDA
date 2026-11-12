import json
import uuid

import pytest

from ai_edda import plan_input_commands
from ai_edda.application import ApplicationService
from ai_edda.domain import AudioAsset, Chart, ObjectRef, SongProject, TempoSegment, TimingGrid
from ai_edda.plan_inputs import (
    PlanInputs,
    _identity,
    build_plan_inputs,
    publish_plan_inputs,
    resolve_plan_inputs,
)
from ai_edda.project_store import ProjectStore


def command(service, view, kind, payload, command_id=None):
    return service.execute(
        view.project.id,
        {
            "command_id": command_id or str(uuid.uuid4()),
            "base_project_revision": view.project.revision,
            "expected_grid_revision": view.grid.revision,
            "expected_chart_revisions": {c.id: c.revision for c in view.charts},
            "kind": kind,
            "payload": payload,
        },
    )


def test_delete_chart_cleans_scopes_preview_undo_redo_without_analysis(tmp_path):
    with ProjectStore(tmp_path) as store:
        app = ApplicationService(store)
        view = app.create_project(str(uuid.uuid4()), {})
        view = command(app, view, "create_chart", {})
        a, b = (c.id for c in view.charts)
        record = build_plan_inputs(
            "audio",
            {
                "path": "old.ogg",
                "sha256": "a" * 64,
                "duration_sec": 1,
                "sample_rate": 100,
                "channels": 1,
                "frames": 100,
                "subtype": "VORBIS",
            },
            ObjectRef(id=str(uuid.uuid4()), revision=0),
            [
                {
                    "id": str(uuid.uuid4()),
                    "time_sec": 0.1,
                    "tolerance_sec": 0.01,
                    "target_scope": [a, b],
                },
                {
                    "id": str(uuid.uuid4()),
                    "time_sec": 0.2,
                    "tolerance_sec": 0.01,
                    "target_scope": [b],
                },
                {
                    "id": str(uuid.uuid4()),
                    "time_sec": 0.3,
                    "tolerance_sec": 0.01,
                    "target_scope": [],
                },
            ],
            [a, b],
        )
        root = store._dir(view.project.id)
        ref = publish_plan_inputs(root, record)
        current = store.load(view.project.id)
        store.commit(
            view.project.id,
            str(uuid.uuid4()),
            {},
            current.project.revision,
            current.grid.revision,
            {c.id: c.revision for c in current.charts},
            current.project.model_copy(update={"plan_inputs": ref}),
            current.grid,
            current.charts,
            lambda actual: actual.project.model_dump(mode="json"),
        )
        view = app.get(view.project.id)
        before_files = set((root / "plan-inputs").iterdir())
        preview = app.preview(view.project.id, "delete_chart", {"chart_id": b})
        assert preview.project.plan_inputs.id != ref.id
        assert set((root / "plan-inputs").iterdir()) == before_files
        deleted = command(app, view, "delete_chart", {"chart_id": b}, "delete-b")
        assert deleted.project.plan_inputs == preview.project.plan_inputs
        assert deleted.project.analysis is None
        changed_path = root / "plan-inputs" / f"{deleted.project.plan_inputs.id}.json"
        changed = json.loads(changed_path.read_text())
        assert [x["target_scope"] for x in changed["anchors"]] == [[a], []]
        undone = command(app, deleted, "undo", {})
        assert len(undone.charts) == 2 and undone.project.plan_inputs == ref
        redone = command(app, undone, "redo", {})
        assert len(redone.charts) == 1 and redone.project.plan_inputs == deleted.project.plan_inputs
        retry = command(app, view, "delete_chart", {"chart_id": b}, "delete-b")
        assert retry.project.plan_inputs == deleted.project.plan_inputs


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
def deletion(tmp_path):
    with ProjectStore(tmp_path) as store:
        a, b = Chart(), Chart()
        audio = AudioAsset(
            path="original.ogg",
            sha256="a" * 64,
            duration_sec=2,
            sample_rate=100,
            channels=1,
            frames=200,
            subtype="VORBIS",
        )
        record = build_plan_inputs(
            "original-audio",
            audio,
            ObjectRef(id=str(uuid.uuid4()), revision=4),
            [
                {
                    "id": str(uuid.uuid4()),
                    "time_sec": 0.1,
                    "strength": "required",
                    "target_scope": [a.id, b.id],
                },
                {
                    "id": str(uuid.uuid4()),
                    "time_sec": 0.2,
                    "strength": "required",
                    "target_scope": [b.id],
                },
                {"id": str(uuid.uuid4()), "time_sec": 0.3, "target_scope": []},
                {"id": str(uuid.uuid4()), "time_sec": 0.4, "target_scope": [a.id]},
            ],
            [a.id, b.id],
        )
        project = SongProject(plan_inputs=ObjectRef(id=record.id, revision=0))
        grid = TimingGrid(
            segments=(
                TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),
            )
        )
        store.create(project, grid, (a, b))
        root = store._dir(project.id)
        publish_plan_inputs(root, record)
        service = ApplicationService(store)
        yield store, service, service.get(project.id), root, record


def test_preview_is_pure_and_one_history_step_restores_both_changes(deletion, monkeypatch):
    store, service, initial, root, old = deletion
    pid, b = initial.project.id, initial.charts[1].id
    before = files(root)
    preview = service.preview(pid, "delete_chart", {"chart_id": b})
    assert files(root) == before
    assert service.get(pid) == initial
    assert len(preview.charts) == 1
    assert preview.project.plan_inputs != initial.project.plan_inputs
    calls = []
    original = store.commit

    def commit(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "commit", commit)
    deleted = service.execute(pid, envelope(initial, "delete_chart", {"chart_id": b}))
    assert len(calls) == len(list(root.glob("receipt-*.json"))) == 1
    assert deleted.project.plan_inputs == preview.project.plan_inputs
    updated = resolve_plan_inputs(root, deleted.project.plan_inputs)
    assert tuple(anchor.target_scope for anchor in updated.anchors) == (
        (initial.charts[0].id,),
        (),
        (initial.charts[0].id,),
    )
    assert updated.model_dump(exclude={"id", "anchors"}) == old.model_dump(
        exclude={"id", "anchors"}
    )
    undone = service.execute(pid, envelope(deleted, "undo"))
    assert len(undone.charts) == 2 and undone.project.plan_inputs == initial.project.plan_inputs
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(pid, envelope(undone, "undo"))
    redone = service.execute(pid, envelope(undone, "redo"))
    assert len(redone.charts) == 1 and redone.project.plan_inputs == deleted.project.plan_inputs
    assert resolve_plan_inputs(root, redone.project.plan_inputs) == updated
    for path, contents in before.items():
        if path != "project.json":
            assert (root / path).read_bytes() == contents


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_selected_record_blocks_deletion_without_head_changes(deletion, corrupt):
    _, service, initial, root, old = deletion
    path = root / "plan-inputs" / f"{old.id}.json"
    if corrupt:
        path.write_bytes(b"{broken")
    else:
        path.unlink()
    before = files(root)
    for mode in ("preview", "execute"):
        with pytest.raises((ValueError, FileNotFoundError)):
            if mode == "preview":
                service.preview(
                    initial.project.id, "delete_chart", {"chart_id": initial.charts[1].id}
                )
            else:
                service.execute(
                    initial.project.id,
                    envelope(initial, "delete_chart", {"chart_id": initial.charts[1].id}),
                )
        assert files(root) == before
        assert service.get(initial.project.id) == initial


@pytest.mark.parametrize("point", ["publication", "commit"])
def test_failure_retains_head_and_history_and_retry_is_safe(deletion, monkeypatch, point):
    store, service, initial, root, old = deletion
    pid = initial.project.id
    cmd = envelope(initial, "delete_chart", {"chart_id": initial.charts[1].id})
    before = files(root)

    def fail(*args, **kwargs):
        raise OSError("injected failure")

    with monkeypatch.context() as patch:
        if point == "publication":
            patch.setattr(plan_input_commands, "publish_plan_inputs", fail)
        else:
            patch.setattr(store, "commit", fail)
        with pytest.raises(OSError, match="injected failure"):
            service.execute(pid, cmd)
    assert service.get(pid) == initial
    assert (root / "project.json").read_bytes() == before["project.json"]
    assert not list(root.glob("receipt-*.json"))
    assert resolve_plan_inputs(root, initial.project.plan_inputs) == old
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(pid, envelope(initial, "undo"))
    deleted = service.execute(pid, cmd)
    assert len(deleted.charts) == 1
    assert len(list((root / "plan-inputs").iterdir())) == 2


def test_receipt_reconciliation_replay_and_later_changes_do_not_duplicate_deletion(
    deletion, monkeypatch
):
    store, service, initial, root, _ = deletion
    pid = initial.project.id
    cmd = envelope(initial, "delete_chart", {"chart_id": initial.charts[1].id})
    original = store.commit

    def lost_reply(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("reply lost after commit")

    with monkeypatch.context() as patch:
        patch.setattr(store, "commit", lost_reply)
        deleted = service.execute(pid, cmd)
    later = service.execute(
        pid, envelope(deleted, "update_song_metadata", {"title": "Later"}), "other"
    )
    before = files(root)
    assert service.execute(pid, cmd) == deleted
    assert ApplicationService(store).execute(pid, cmd) == deleted
    assert service.get(pid) == later
    assert files(root) == before
    undone = service.execute(pid, envelope(later, "undo"))
    assert len(undone.charts) == 2 and undone.project.plan_inputs == initial.project.plan_inputs
    assert undone.project.metadata.title == "Later"
    with pytest.raises(ValueError, match="HISTORY_EMPTY"):
        service.execute(pid, envelope(undone, "undo"))


def test_missing_chart_validation_runs_before_input_catalog_access(deletion, monkeypatch):
    _, service, initial, root, _ = deletion

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid structural command must not inspect input catalogs")

    monkeypatch.setattr(plan_input_commands, "resolve_plan_inputs", forbidden)
    before = files(root)
    with pytest.raises(KeyError):
        service.execute(
            initial.project.id, envelope(initial, "delete_chart", {"chart_id": str(uuid.uuid4())})
        )
    assert files(root) == before


def select_record(store, service, view, record):
    ref = publish_plan_inputs(store._dir(view.project.id), record)
    current = store.load(view.project.id)
    store.commit(
        view.project.id,
        str(uuid.uuid4()),
        {},
        current.project.revision,
        current.grid.revision,
        {c.id: c.revision for c in current.charts},
        current.project.model_copy(update={"plan_inputs": ref}),
        current.grid,
        current.charts,
        lambda actual: actual.project.model_dump(mode="json"),
    )
    return service.get(view.project.id)


def changed_anchors(record, anchors):
    data = record.model_dump(mode="json", exclude={"id", "anchors"})
    data["anchors"] = [anchor.model_dump(mode="json") for anchor in anchors]
    return PlanInputs(id=_identity(data), **data)


def test_absent_plan_inputs_performs_ordinary_last_chart_deletion_without_catalog_io(
    deletion, monkeypatch
):
    store, service, _, _, _ = deletion
    view = service.create_project(str(uuid.uuid4()), {})
    root = store._dir(view.project.id)

    def forbidden(*args, **kwargs):
        raise AssertionError("no plan inputs must not access their catalog")

    monkeypatch.setattr(plan_input_commands, "resolve_plan_inputs", forbidden)
    monkeypatch.setattr(plan_input_commands, "publish_plan_inputs", forbidden)
    before = files(root)
    preview = service.preview(view.project.id, "delete_chart", {"chart_id": view.charts[0].id})
    assert preview.charts == () and preview.project.plan_inputs is None
    assert files(root) == before
    result = service.execute(
        view.project.id, envelope(view, "delete_chart", {"chart_id": view.charts[0].id})
    )
    assert result.charts == () and result.project.plan_inputs is None
    assert not (root / "plan-inputs").exists()


def test_unaffected_record_is_reused_without_publication(deletion, monkeypatch):
    store, service, initial, root, old = deletion
    unchanged = changed_anchors(old, (old.anchors[2], old.anchors[3]))
    initial = select_record(store, service, initial, unchanged)

    def forbidden(*args, **kwargs):
        raise AssertionError("unchanged inputs must not be published")

    monkeypatch.setattr(plan_input_commands, "publish_plan_inputs", forbidden)
    before = files(root)
    payload = {"chart_id": initial.charts[1].id}
    preview = service.preview(initial.project.id, "delete_chart", payload)
    assert preview.project.plan_inputs == initial.project.plan_inputs
    assert files(root) == before
    result = service.execute(initial.project.id, envelope(initial, "delete_chart", payload))
    assert result.project.plan_inputs == initial.project.plan_inputs
    assert {p.name for p in (root / "plan-inputs").iterdir()} == {
        old.id + ".json",
        unchanged.id + ".json",
    }


def test_undo_plan_input_conflict_leaves_both_charts_and_reference_unchanged(deletion):
    store, service, initial, root, old = deletion
    pid = initial.project.id
    deleted = service.execute(
        pid, envelope(initial, "delete_chart", {"chart_id": initial.charts[1].id})
    )
    replacement = changed_anchors(old, (old.anchors[2],))
    current = select_record(store, service, deleted, replacement)
    before = files(root)
    with pytest.raises(ValueError, match="UNDO_CONFLICT"):
        service.execute(pid, envelope(current, "undo"))
    assert service.get(pid) == current
    assert len(current.charts) == 1
    assert files(root) == before


def test_unknown_remaining_scope_is_rejected_without_publication(deletion):
    store, service, initial, root, old = deletion
    unknown = old.anchors[0].model_copy(
        update={"target_scope": (initial.charts[1].id, str(uuid.uuid4()))}
    )
    invalid = changed_anchors(old, (unknown,))
    initial = select_record(store, service, initial, invalid)
    before = files(root)
    with pytest.raises(ValueError, match="Invalid anchor scope"):
        service.execute(
            initial.project.id,
            envelope(initial, "delete_chart", {"chart_id": initial.charts[1].id}),
        )
    assert service.get(initial.project.id) == initial
    assert files(root) == before
