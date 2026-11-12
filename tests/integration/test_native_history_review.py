"""Independent native-history checks built from real persisted transactions."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from ai_edda.application import ApplicationService, ProjectView
from ai_edda.domain import Chart, ChartNote, HitEvent, ObjectRef
from ai_edda.native_history import validate_native_history
from ai_edda.project_store import ProjectStore
from ai_edda.validation import validate_chart


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def history(tmp_path):
    with ProjectStore(tmp_path) as store:
        app = ApplicationService(store)
        view = app.create_project(str(uuid4()), {"title": "Initial"})
        for title in ("First", "Second"):
            view = app.execute(
                view.project.id,
                {
                    "command_id": title,
                    "base_project_revision": view.project.revision,
                    "expected_grid_revision": view.grid.revision,
                    "expected_chart_revisions": {c.id: c.revision for c in view.charts},
                    "kind": "update_song_metadata",
                    "payload": {"title": title},
                },
            )
        directory = store._dir(view.project.id)
    return directory, view.project.id


def receipt(history):
    directory, _ = history
    return directory / read(directory / "project.json")["receipt"]


def test_real_application_history_is_preserved(history):
    directory, pid = history
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    validated = validate_native_history(directory, pid)
    assert validated.materialize_current().project.metadata.title == "Second"
    assert len(validated.receipts) == 2
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.parametrize("error_type", [TypeError, KeyError, RecursionError, OSError])
def test_public_history_preserves_delayed_reader_cancellation_identity(
    history, monkeypatch, error_type
):
    from ai_edda import native_history_reader as reader

    directory, pid = history
    original = reader.read_native_history_json
    active = False
    error = error_type("cancel while reading current head")

    def entered(path, **kwargs):
        nonlocal active
        active = path.name == "project.json"
        try:
            return original(path, **kwargs)
        finally:
            active = False

    monkeypatch.setattr(reader, "read_native_history_json", entered)

    def cancel():
        if active:
            raise error

    with pytest.raises(error_type) as caught:
        validate_native_history(directory, pid, cancel=cancel)
    assert caught.value is error


def test_public_history_normalizes_actual_missing_project_field(history):
    directory, pid = history
    path = directory / "project.json"
    raw = read(path)
    del raw["project"]["id"]
    write(path, raw)
    with pytest.raises(ValueError, match="Invalid native history") as caught:
        validate_native_history(directory, pid)
    assert isinstance(caught.value.__cause__, KeyError)


@pytest.mark.parametrize("result", [None, 7, "complete", [True, {"ok": 1}], {"ok": True}])
def test_store_generic_result_is_a_supported_contract(tmp_path, result):
    with ProjectStore(tmp_path) as store:
        app = ApplicationService(store)
        initial = app.create_project(str(uuid4()), {})
        store.commit(
            initial.project.id,
            "generic",
            {},
            0,
            0,
            {c.id: c.revision for c in initial.charts},
            initial.project,
            initial.grid,
            initial.charts,
            result,
        )
        value = validate_native_history(store._dir(initial.project.id), initial.project.id)
        assert read(store._dir(initial.project.id) / next(iter(value.receipts)))["result"] == result


def test_failed_factory_leaves_valid_orphans_and_revision_gaps(tmp_path):
    with ProjectStore(tmp_path) as store:
        view = ApplicationService(store).create_project(str(uuid4()), {})
        arguments = (
            view.project.id,
            "command",
            {},
            0,
            0,
            {c.id: c.revision for c in view.charts},
            view.project,
            view.grid,
            view.charts,
        )
        with pytest.raises(ValueError):
            store.commit(*arguments, lambda _: float("nan"))
        store.commit(*arguments, {"ok": True})
        value = validate_native_history(store._dir(view.project.id), view.project.id)
        assert value.materialize_current().project.revision == 1
        assert value.materialize_current().grid.revision == 2
        assert set(value.grids) == {0, 1, 2}


def test_failed_head_publication_leaves_valid_orphan_receipt(tmp_path, monkeypatch):
    with ProjectStore(tmp_path) as store:
        view = ApplicationService(store).create_project(str(uuid4()), {})
        arguments = (
            view.project.id,
            "command",
            {},
            0,
            0,
            {c.id: c.revision for c in view.charts},
            view.project,
            view.grid,
            view.charts,
        )
        original = store._write

        def fail_head(path, value, replace=False):
            if path.name == "project.json" and replace:
                raise OSError("injected head publication failure")
            return original(path, value, replace)

        with monkeypatch.context() as patch:
            patch.setattr(store, "_write", fail_head)
            with pytest.raises(OSError):
                store.commit(*arguments, {"ok": True})
        store.commit(*arguments, {"ok": True})
        directory = store._dir(view.project.id)
        validated = validate_native_history(directory, view.project.id)
        assert len(validated.receipts) == 2
        current = read(directory / "project.json")["receipt"]
        orphan = next(p for p in directory.glob("receipt-*.json") if p.name != current)
        bad = read(orphan)
        bad["result"] = {"project": {}, "grid": {}, "charts": []}
        write(orphan, bad)
        with pytest.raises(ValueError):
            validate_native_history(directory, view.project.id)


@pytest.mark.parametrize("field", ["analysis", "plan_inputs"])
@pytest.mark.parametrize("bad_revision", [True, "0", 0.0])
def test_nested_reference_revision_is_exact_integer(history, field, bad_revision):
    directory, pid = history
    path = directory / "project.json"
    value = read(path)
    value["project"][field] = {"id": str(uuid4()), "revision": bad_revision}
    write(path, value)
    latest = directory / value["receipt"]
    transaction = read(latest)
    transaction["new_head"] = value
    transaction["result"]["project"] = value["project"]
    write(latest, transaction)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


@pytest.mark.parametrize("field", ["analysis", "plan_inputs"])
def test_nested_reference_id_is_canonical(history, field):
    directory, pid = history
    path = directory / "project.json"
    value = read(path)
    value["project"][field] = {"id": "not-a-uuid", "revision": 0}
    write(path, value)
    # Keep the receipt's new head in sync, so this tests the ID independently.
    latest = directory / value["receipt"]
    transaction = read(latest)
    transaction["new_head"] = value
    transaction["result"]["project"] = value["project"]
    write(latest, transaction)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


@pytest.mark.parametrize("object_kind", ["event", "note", "target"])
def test_orphan_chart_ids_are_canonical(history, object_kind):
    directory, pid = history
    chart = Chart(
        events=(HitEvent(time_ref={"kind": "audio", "time_sec": 0}, notes=(ChartNote(lane=0),)),)
    )
    value = chart.model_dump(mode="json")
    if object_kind == "event":
        value["events"][0]["id"] = "not-a-uuid"
    elif object_kind == "note":
        value["events"][0]["notes"][0]["id"] = "not-a-uuid"
    else:
        value["target_profile"] = {"id": "not-a-uuid", "revision": 1}
    write(directory / f"chart-{chart.id}-0.json", value)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


@pytest.mark.parametrize("bad_revision", [True, "1", 1.0])
def test_orphan_target_reference_revision_is_exact_integer(history, bad_revision):
    directory, pid = history
    chart = Chart()
    value = chart.model_dump(mode="json")
    value["target_profile"] = {"id": str(uuid4()), "revision": bad_revision}
    write(directory / f"chart-{chart.id}-0.json", value)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


def test_invalid_local_chart_can_be_stored_and_validated(tmp_path):
    """An empty event fails local game validation but is a valid native draft."""
    with ProjectStore(tmp_path) as store:
        view = ApplicationService(store).create_project(str(uuid4()), {})
        chart = Chart(events=(HitEvent(time_ref={"kind": "audio", "time_sec": -1}),))
        assert "INVALID_EVENT_SIZE" in {d.code for d in validate_chart(chart, view.grid, 60)}
        store.commit(
            view.project.id,
            "draft",
            {},
            0,
            0,
            {c.id: c.revision for c in view.charts},
            view.project,
            view.grid,
            (chart,),
            {"ok": True},
        )
        value = validate_native_history(store._dir(view.project.id), view.project.id)
        assert value.materialize_current().charts[0].events[0].notes == ()


@pytest.mark.parametrize("change", ["extra", "wrong", "nested_revision", "nested_id"])
def test_project_view_result_cannot_bypass_snapshot_checks(history, change):
    directory, pid = history
    path = receipt(history)
    value = read(path)
    if change == "extra":
        value["result"]["extra"] = True
    elif change == "wrong":
        value["result"]["project"]["metadata"]["title"] = "forged"
    elif change == "nested_revision":
        value["result"]["grid"]["revision"] = str(value["result"]["grid"]["revision"])
    else:
        value["result"]["charts"][0]["id"] = value["result"]["charts"][0]["id"].upper()
    write(path, value)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


def test_generic_result_float_overflow_is_rejected(history):
    directory, pid = history
    path = receipt(history)
    value = read(path)
    value["result"] = "OVERFLOW"
    path.write_text(json.dumps(value).replace('"OVERFLOW"', "1e999"), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


@pytest.mark.parametrize("change", ["missing", "self_cycle", "wrong_previous_head"])
def test_receipt_chain_corruption_is_rejected(history, change):
    directory, pid = history
    path = receipt(history)
    value = read(path)
    if change == "missing":
        (directory / value["previous_receipt"]).unlink()
    elif change == "self_cycle":
        value["previous_receipt"] = path.name
        value["previous_head"]["receipt"] = path.name
        write(path, value)
    else:
        value["previous_head"]["project"]["metadata"]["title"] = "forged"
        write(path, value)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


def test_orphan_grid_duplicate_json_key_is_rejected(history):
    directory, pid = history
    value = read(directory / "grid-0.json")
    value["revision"] = 99
    serialized = json.dumps(value)
    (directory / "grid-99.json").write_text(serialized[:-1] + ', "revision": 99}', encoding="utf-8")
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


def test_noninitial_head_requires_receipt(history):
    directory, pid = history
    path = directory / "project.json"
    value = read(path)
    assert value["project"]["revision"] == 2
    value["receipt"] = None
    write(path, value)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


def test_historical_receipt_cannot_truncate_its_chain(history):
    directory, pid = history
    path = receipt(history)
    value = read(path)
    assert value["previous_head"]["project"]["revision"] == 1
    value["previous_head"]["receipt"] = None
    value["previous_receipt"] = None
    write(path, value)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


def test_broken_reference_used_only_by_historical_head_is_rejected(history):
    directory, pid = history
    (directory / "grid-0.json").unlink()
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


def test_orphan_chart_filename_binding_is_enforced(history):
    directory, pid = history
    chart = Chart()
    write(directory / f"chart-{uuid4()}-0.json", chart.model_dump(mode="json"))
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_project_schema_version_is_explicit_exact_and_supported(history, version):
    directory, pid = history
    path = directory / "project.json"
    value = read(path)
    value["project"]["schema_version"] = version
    write(path, value)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


def test_oversized_json_is_rejected_before_parsing(history, monkeypatch):

    directory, pid = history
    from ai_edda.native_history import _build_native_history_index
    from ai_edda.native_history_index import NativeHistoryLimits
    from ai_edda.native_history_reader import NativeHistoryReadLimits

    def validate_native_history(root, pid):
        return _build_native_history_index(
            root, pid, limits=NativeHistoryLimits(read_limits=NativeHistoryReadLimits(max_bytes=64))
        )

    with pytest.raises(ValueError, match="limit"):
        validate_native_history(directory, pid)


def test_deep_receipt_json_is_rejected_as_invalid_input(history):
    directory, pid = history
    path = receipt(history)
    value = read(path)
    value["result"] = "DEEP"
    path.write_text(
        json.dumps(value).replace('"DEEP"', "[" * 1500 + "0" + "]" * 1500), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


@pytest.mark.parametrize("document", [None, [], 42, {"segments": []}])
def test_malformed_orphan_grid_is_a_validation_error(history, document):
    directory, pid = history
    write(directory / "grid-99.json", document)
    with pytest.raises(ValueError):
        validate_native_history(directory, pid)


def test_aggregate_json_byte_budget_has_exact_boundary(history, monkeypatch):

    directory, pid = history
    total = sum(p.stat().st_size for p in directory.iterdir())
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    from ai_edda.native_history import _scan_history_files

    _scan_history_files(directory, pid, max_bytes=total)
    assert validate_native_history(directory, pid).materialize_current().project.id == pid
    with pytest.raises(ValueError, match="limit"):
        _scan_history_files(directory, pid, max_bytes=total - 1)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_json_node_budget_is_per_record(history, monkeypatch):

    directory, pid = history
    # The cap applies per record. This fixture has 777 nodes across nine documents.
    from ai_edda.native_history import _build_native_history_index
    from ai_edda.native_history_index import NativeHistoryLimits
    from ai_edda.native_history_reader import NativeHistoryReadLimits

    def validate_native_history(root, pid):
        return _build_native_history_index(
            root,
            pid,
            limits=NativeHistoryLimits(read_limits=NativeHistoryReadLimits(max_nodes=100)),
        )

    with pytest.raises(ValueError, match="node"):
        validate_native_history(directory, pid)


def test_json_between_read_block_and_document_limit_remains_valid(tmp_path):
    with ProjectStore(tmp_path) as store:
        view = ApplicationService(store).create_project(str(uuid4()), {})
        result = "x" * (2 * 1024 * 1024)
        store.commit(
            view.project.id,
            "large",
            {},
            0,
            0,
            {c.id: c.revision for c in view.charts},
            view.project,
            view.grid,
            view.charts,
            result,
        )
        value = validate_native_history(store._dir(view.project.id), view.project.id)
        assert read(store._dir(view.project.id) / next(iter(value.receipts)))["result"] == result


def test_history_reader_uses_bounded_blocks(history, monkeypatch):

    directory, pid = history
    original = Path.open
    requests = []

    class TrackedStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size=-1):
            requests.append(size)
            assert 0 < size <= 128
            return self.stream.read(size)

    def tracked_open(path, *args, **kwargs):
        return TrackedStream(original(path, *args, **kwargs))

    from ai_edda.native_history import _build_native_history_index
    from ai_edda.native_history_index import NativeHistoryLimits
    from ai_edda.native_history_reader import NativeHistoryReadLimits

    def validate_native_history(root, pid):
        return _build_native_history_index(
            root,
            pid,
            limits=NativeHistoryLimits(read_limits=NativeHistoryReadLimits(max_read_bytes=128)),
        )

    monkeypatch.setattr(Path, "open", tracked_open)
    assert validate_native_history(directory, pid).materialize_current().project.id == pid
    assert len(requests) > 9


@pytest.mark.parametrize("depth, accepted", [(60, True), (65, False)])
def test_json_depth_boundary(history, depth, accepted):
    directory, pid = history
    path = receipt(history)
    value = read(path)
    result = 0
    for _ in range(depth):
        result = [result]
    value["result"] = result
    write(path, value)
    if accepted:
        assert validate_native_history(directory, pid).receipts
    else:
        with pytest.raises(ValueError, match="nesting"):
            validate_native_history(directory, pid)


@pytest.mark.parametrize("reference", ["analysis", "plan_inputs", "target_profile"])
def test_project_view_nested_reference_revision_cannot_coerce(tmp_path, reference):
    with ProjectStore(tmp_path) as store:
        view = ApplicationService(store).create_project(str(uuid4()), {})
        ref = ObjectRef(id=str(uuid4()), revision=1)
        project, charts = view.project, view.charts
        if reference == "target_profile":
            charts = (charts[0].model_copy(update={"target_profile": ref}),)
        else:
            project = project.model_copy(update={reference: ref})
        store.commit(
            view.project.id,
            "reference",
            {},
            0,
            0,
            {c.id: c.revision for c in view.charts},
            project,
            view.grid,
            charts,
            lambda s: ProjectView(project=s.project, grid=s.grid, charts=s.charts).model_dump(
                mode="json"
            ),
        )
        directory = store._dir(view.project.id)
        assert validate_native_history(directory, view.project.id).receipts
        path = directory / read(directory / "project.json")["receipt"]
        value = read(path)
        if reference == "target_profile":
            value["result"]["charts"][0]["target_profile"]["revision"] = True
        else:
            value["result"]["project"][reference]["revision"] = True
        write(path, value)
        with pytest.raises(ValueError):
            validate_native_history(directory, view.project.id)
