import json
import os
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from ai_edda.domain import SongProject
from ai_edda.native_history_index import NativeHistoryIndex, NativeHistoryLimits, ReceiptRecord


def test_head_dedup_and_owned_copy(tmp_path):
    project_id = str(uuid4())
    index = NativeHistoryIndex(tmp_path, project_id)
    raw = {
        "project": {
            "id": project_id,
            "schema_version": 1,
            "revision": 0,
            "grid_revision": 0,
            "chart_heads": [],
        },
        "receipt": None,
    }
    first = index.record_head(raw)
    assert index.record_head(raw) is first
    raw["project"]["revision"] = 1
    assert first.raw_head["project"]["revision"] == 0


def test_rejects_wrong_project_without_mutation(tmp_path):
    index = NativeHistoryIndex(tmp_path, str(uuid4()))
    raw = {
        "project": {
            "id": str(uuid4()),
            "schema_version": 1,
            "revision": 0,
            "grid_revision": 0,
            "chart_heads": [],
        },
        "receipt": None,
    }
    with pytest.raises(ValueError):
        index.record_head(raw)
    assert index.heads == []


PROJECT_ID = "00000000-0000-0000-0000-000000000001"
CHART_ID = "00000000-0000-0000-0000-000000000002"


def head_input():
    return {
        "project": {
            "id": PROJECT_ID,
            "schema_version": 1,
            "revision": 0,
            "grid_revision": 0,
            "chart_heads": [],
        },
        "receipt": None,
    }


def state(index):
    return (
        deepcopy(index.heads),
        deepcopy(index.receipts),
        deepcopy(index._head_keys),
        index._owned_heads.copy(),
        index._index_bytes,
        index._index_chart_refs,
        index.current_head,
        index.charts.copy(),
        index.grids.copy(),
    )


def encoded_size(value):
    return len(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8", errors="surrogatepass")
    )


def test_head_raw_and_normalized_exact_charge_and_atomic_budget(tmp_path):
    raw = head_input()
    expected = encoded_size(raw) + encoded_size(
        SongProject.model_validate(raw["project"]).model_dump(mode="json")
    )
    index = NativeHistoryIndex(
        tmp_path, PROJECT_ID, limits=NativeHistoryLimits(max_index_bytes=expected)
    )
    first = index.record_head(raw)
    assert index._index_bytes == expected
    assert index.record_head(deepcopy(raw)) is first
    assert index._index_bytes == expected
    small = NativeHistoryIndex(
        tmp_path, PROJECT_ID, limits=NativeHistoryLimits(max_index_bytes=expected - 1)
    )
    before = state(small)
    with pytest.raises(ValueError, match="byte limit"):
        small.record_head(raw)
    assert state(small) == before


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("metadata",),
        ("audio",),
        ("cover",),
        ("provenance",),
        ("analysis",),
        ("plan_inputs",),
        ("chart_heads", 0),
    ],
)
def test_head_wide_records_stop_at_first_error(tmp_path, path):
    from pydantic import ValidationError

    raw = head_input()
    raw["project"]["chart_heads"] = [{"id": CHART_ID, "revision": 0}]
    for name in ("analysis", "plan_inputs"):
        raw["project"][name] = {"id": CHART_ID, "revision": 0}
    target = raw["project"]
    for part in path:
        target = target[part] if isinstance(part, int) else target.setdefault(part, {})
    target.update({f"unknown_{i}": None for i in range(1000)})
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    before = state(index)
    with pytest.raises(ValueError) as caught:
        index.record_head(raw)
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() == 1
    assert state(index) == before


@pytest.mark.parametrize("path", [("export_presets",), ("provenance", "source_ids")])
def test_head_bad_scalar_collections_stop_at_first_error(tmp_path, path):
    from pydantic import ValidationError

    raw = head_input()
    target = raw["project"]
    for part in path[:-1]:
        target = target.setdefault(part, {})
    target[path[-1]] = [None] * 1000
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    before = state(index)
    with pytest.raises(ValueError) as caught:
        index.record_head(raw)
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() == 1
    assert state(index) == before


def test_head_null_chart_reference_rejects_as_invalid_input(tmp_path):
    raw = head_input()
    raw["project"]["chart_heads"] = [None]
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    before = state(index)
    with pytest.raises(ValueError):
        index.record_head(raw)
    assert state(index) == before


def test_head_reference_budget_and_duplicate_failure_are_atomic(tmp_path):
    raw = head_input()
    raw["project"]["chart_heads"] = [{"id": CHART_ID, "revision": 0}]
    index = NativeHistoryIndex(
        tmp_path, PROJECT_ID, limits=NativeHistoryLimits(max_index_chart_refs=1)
    )
    first = index.record_head(raw)
    assert index._index_chart_refs == 1
    assert index.record_head(raw) is first
    before = state(index)
    changed = deepcopy(raw)
    changed["project"]["revision"] = 1
    with pytest.raises(ValueError, match="reference limit"):
        index.record_head(changed)
    assert state(index) == before
    duplicate = deepcopy(raw)
    duplicate["project"]["chart_heads"] *= 2
    other = NativeHistoryIndex(tmp_path, PROJECT_ID)
    before = state(other)
    with pytest.raises(ValueError, match="Duplicate chart"):
        other.record_head(duplicate)
    assert state(other) == before


def test_receipt_dedup_conflict_foreign_ownership_and_budget_are_atomic(tmp_path):
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    head = index.record_head(head_input())
    record = ReceiptRecord("ordinary command", "a" * 64, head, head, None)
    before_charge = index._index_bytes
    index.record_receipt("receipt-a.json", record)
    assert index._index_bytes > before_charge
    before = state(index)
    assert index.record_receipt("receipt-a.json", record) is record
    assert state(index) == before
    with pytest.raises(ValueError, match="Conflicting receipt"):
        index.record_receipt("receipt-a.json", replace(record, command_id="changed"))
    assert state(index) == before
    foreign = NativeHistoryIndex(tmp_path, PROJECT_ID).record_head(head_input())
    with pytest.raises(ValueError, match="Foreign head"):
        index.record_receipt("receipt-b.json", replace(record, new_head=foreign))
    assert state(index) == before
    index.limits = replace(index.limits, max_index_bytes=index._index_bytes)
    with pytest.raises(ValueError, match="byte limit"):
        index.record_receipt("receipt-b.json", record)
    assert state(index) == before


@pytest.mark.parametrize("operation", ["head", "receipt"])
@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, RecursionError])
def test_recording_cancellation_preserves_identity_and_all_state(tmp_path, operation, error_type):
    probe = NativeHistoryIndex(tmp_path, PROJECT_ID)
    checkpoints = []
    if operation == "head":
        probe.cancel = lambda: checkpoints.append(None)
        probe.record_head(head_input())
    else:
        head = probe.record_head(head_input())
        probe.cancel = lambda: checkpoints.append(None)
        probe.record_receipt("receipt-a.json", ReceiptRecord("cmd", "a" * 64, head, head, None))
    for stop in (1, len(checkpoints) // 2, len(checkpoints)):
        index = NativeHistoryIndex(tmp_path, PROJECT_ID)
        if operation == "receipt":
            head = index.record_head(head_input())
            record = ReceiptRecord("cmd", "a" * 64, head, head, None)
        before = state(index)
        calls = 0
        error = error_type("cancel")

        def cancel():
            nonlocal calls
            calls += 1
            if calls == stop:
                raise error

        index.cancel = cancel
        with pytest.raises(error_type) as caught:
            if operation == "head":
                index.record_head(head_input())
            else:
                index.record_receipt("receipt-a.json", record)
        assert caught.value is error
        assert state(index) == before


def test_chart_grid_records_roundtrip_and_source_change(tmp_path):
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    chart = {"id": CHART_ID, "revision": 0, "grid_revision": 0, "events": []}
    grid = {
        "revision": 0,
        "segments": [
            {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4}
        ],
    }
    chart_path = tmp_path / f"chart-{CHART_ID}-0.json"
    grid_path = tmp_path / "grid-0.json"
    chart_path.write_text(json.dumps(chart), encoding="utf-8")
    grid_path.write_text(json.dumps(grid), encoding="utf-8")
    index.record_chart(chart_path, chart)
    index.record_grid(grid_path, grid)
    assert index.chart_matches(index.load_chart(CHART_ID, 0))
    assert index.grid_matches(index.load_grid(0))
    changed = dict(grid, review="changed")
    grid_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError):
        index.load_grid(0)
    with pytest.raises(KeyError):
        index.load_grid(1)


def test_grid_record_budget_and_path_failure_are_atomic(tmp_path):
    index = NativeHistoryIndex(tmp_path, PROJECT_ID, limits=NativeHistoryLimits(max_index_bytes=1))
    grid = {
        "revision": 0,
        "segments": [
            {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4}
        ],
    }
    path = tmp_path / "grid-0.json"
    path.write_text(json.dumps(grid), encoding="utf-8")
    before = state(index)
    with pytest.raises(ValueError):
        index.record_grid(path, grid)
    assert state(index) == before
    with pytest.raises((ValueError, OSError)):
        index.record_grid(tmp_path, grid)
    assert state(index) == before


def source_input(kind):
    if kind == "chart":
        return {"id": CHART_ID, "revision": 0, "grid_revision": 0, "events": []}
    return {
        "revision": 0,
        "segments": [
            {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4}
        ],
    }


def write_source(root, kind, raw=None):
    raw = source_input(kind) if raw is None else raw
    path = root / (f"chart-{CHART_ID}-0.json" if kind == "chart" else "grid-0.json")
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path, raw


def source_nodes(value):
    if isinstance(value, dict):
        return 1 + sum(1 + source_nodes(child) for child in value.values())
    if isinstance(value, list):
        return 1 + sum(source_nodes(child) for child in value)
    return 1


@pytest.mark.parametrize("kind", ["chart", "grid"])
def test_source_record_charges_normalized_nodes_and_deduplicates(tmp_path, kind):
    from ai_edda.domain import Chart, TimingGrid

    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    path, raw = write_source(tmp_path, kind)
    record_source = getattr(index, "record_" + kind)
    record = record_source(path, raw)
    before = state(index)
    assert record_source(path, deepcopy(raw)) is record
    assert state(index) == before
    model = (Chart if kind == "chart" else TimingGrid).model_validate(raw)
    assert record.node_count == source_nodes(model.model_dump(mode="json"))
    assert record.source_bytes == len(path.read_bytes())
    assert not hasattr(record, "model")


@pytest.mark.parametrize("kind", ["chart", "grid"])
def test_source_conflict_and_same_identity_content_change_are_atomic(tmp_path, kind):
    from ai_edda.domain import Chart, TimingGrid

    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    path, raw = write_source(tmp_path, kind)
    getattr(index, "record_" + kind)(path, raw)
    before = state(index)
    model_type = Chart if kind == "chart" else TimingGrid
    model = model_type.model_validate(raw)
    match = getattr(index, kind + "_matches")
    assert match(model)
    assert not match(model.model_copy(update={"review": "changed"}))
    changed = {**raw, "review": "changed"}
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Conflicting"):
        getattr(index, "record_" + kind)(path, changed)
    assert state(index) == before
    args = (CHART_ID, 0) if kind == "chart" else (0,)
    with pytest.raises(ValueError, match="changed"):
        getattr(index, "load_" + kind)(*args)
    assert state(index) == before


@pytest.mark.parametrize("kind", ["chart", "grid"])
def test_matches_use_indexed_fingerprint_without_source_reads(tmp_path, monkeypatch, kind):
    from ai_edda import native_history_reader as reader
    from ai_edda.domain import Chart, TimingGrid

    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    path, raw = write_source(tmp_path, kind)
    getattr(index, "record_" + kind)(path, raw)
    model = (Chart if kind == "chart" else TimingGrid).model_validate(raw)
    original = reader.read_native_history_json
    reads = []

    def observed(path, **kwargs):
        reads.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(reader, "read_native_history_json", observed)
    for _ in range(3):
        assert getattr(index, kind + "_matches")(model)
    assert reads == []


@pytest.mark.parametrize("kind", ["chart", "grid"])
def test_load_returns_fresh_models_without_retaining_cache(tmp_path, monkeypatch, kind):
    from ai_edda import native_history_reader as reader

    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    path, raw = write_source(tmp_path, kind)
    getattr(index, "record_" + kind)(path, raw)
    before = state(index)
    original = reader.read_native_history_json
    reads = []

    def observed(path, **kwargs):
        reads.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(reader, "read_native_history_json", observed)
    args = (CHART_ID, 0) if kind == "chart" else (0,)
    first = getattr(index, "load_" + kind)(*args)
    second = getattr(index, "load_" + kind)(*args)
    assert first == second and first is not second
    assert state(index) == before
    # Record the bounded pass count without imposing a new single-read contract.
    assert len(reads) <= 4
    assert set(reads) == {path}


@pytest.mark.parametrize("kind", ["chart", "grid"])
def test_source_relative_paths_and_wrong_parent_rejection(tmp_path, monkeypatch, kind):
    monkeypatch.chdir(tmp_path)
    root = Path("history")
    root.mkdir()
    path, raw = write_source(root, kind)
    index = NativeHistoryIndex(root, PROJECT_ID)
    record = getattr(index, "record_" + kind)(path, raw)
    assert record.path.is_absolute()
    outside, _ = write_source(tmp_path, kind)
    before = state(index)
    with pytest.raises(ValueError, match="Unsafe history path"):
        getattr(index, "record_" + kind)(outside, raw)
    assert state(index) == before


@pytest.mark.parametrize("operation", ["record_chart", "record_grid", "load_chart", "load_grid"])
@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, RecursionError])
def test_source_cancellation_preserves_identity_and_all_state(tmp_path, operation, error_type):
    kind = operation.rsplit("_", 1)[1]
    path, raw = write_source(tmp_path, kind)
    probe = NativeHistoryIndex(tmp_path, PROJECT_ID)
    if operation.startswith("load"):
        getattr(probe, "record_" + kind)(path, raw)
    args = (
        (path, raw)
        if operation.startswith("record")
        else ((CHART_ID, 0) if kind == "chart" else (0,))
    )
    checkpoints = []
    probe.cancel = lambda: checkpoints.append(None)
    getattr(probe, operation)(*args)
    for stop in (1, len(checkpoints) // 2, len(checkpoints)):
        index = NativeHistoryIndex(tmp_path, PROJECT_ID)
        if operation.startswith("load"):
            getattr(index, "record_" + kind)(path, raw)
        before = state(index)
        calls = 0
        error = error_type("cancel")

        def cancel():
            nonlocal calls
            calls += 1
            if calls == stop:
                raise error

        index.cancel = cancel
        with pytest.raises(error_type) as caught:
            getattr(index, operation)(*args)
        assert caught.value is error
        assert state(index) == before


@pytest.mark.parametrize("kind", ["chart", "grid"])
def test_source_wrong_filename_nonregular_and_budget_failure_are_atomic(tmp_path, kind):
    path, raw = write_source(tmp_path, kind)
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    before = state(index)
    wrong = path.with_name("wrong.json")
    wrong.write_text(json.dumps(raw))
    for candidate in (wrong, tmp_path):
        with pytest.raises(ValueError):
            getattr(index, "record_" + kind)(candidate, raw)
        assert state(index) == before
    small = NativeHistoryIndex(tmp_path, PROJECT_ID, limits=NativeHistoryLimits(max_index_bytes=1))
    before = state(small)
    with pytest.raises(ValueError, match="byte limit"):
        getattr(small, "record_" + kind)(path, raw)
    assert state(small) == before


@pytest.mark.parametrize("kind", ["chart", "grid"])
def test_source_rejects_same_parent_directory_named_like_record(tmp_path, kind):
    filename = f"chart-{CHART_ID}-0.json" if kind == "chart" else "grid-0.json"
    directory = tmp_path / filename
    directory.mkdir()
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    before = state(index)
    with pytest.raises(ValueError, match="regular file"):
        getattr(index, "record_" + kind)(directory, source_input(kind))
    assert state(index) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
@pytest.mark.parametrize("kind", ["chart", "grid"])
def test_source_rejects_windows_junction_parent(tmp_path, kind):
    target = tmp_path / "target"
    target.mkdir()
    path, raw = write_source(target, kind)
    junction = tmp_path / "junction"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "New-Item -ItemType Junction -Path $env:NATIVE_INDEX_TEST_LINK "
            "-Target $env:NATIVE_INDEX_TEST_TARGET | Out-Null",
        ],
        env={
            **os.environ,
            "NATIVE_INDEX_TEST_LINK": str(junction),
            "NATIVE_INDEX_TEST_TARGET": str(target),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    try:
        index = NativeHistoryIndex(junction, PROJECT_ID)
        before = state(index)
        with pytest.raises(ValueError, match="Unsafe path"):
            getattr(index, "record_" + kind)(junction / path.name, raw)
        assert state(index) == before
    finally:
        os.rmdir(junction)


def test_materialize_preflight_and_source_changes(tmp_path, monkeypatch):
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    ids = [CHART_ID, str(uuid4())]
    grid = {
        "revision": 0,
        "segments": [
            {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4}
        ],
    }
    path = tmp_path / "grid-0.json"
    path.write_text(json.dumps(grid), encoding="utf-8")
    index.record_grid(path, grid)
    for chart_id in ids:
        chart = {"id": chart_id, "revision": 0, "grid_revision": 0, "events": []}
        path = tmp_path / f"chart-{chart_id}-0.json"
        path.write_text(json.dumps(chart), encoding="utf-8")
        index.record_chart(path, chart)
    raw = head_input()
    raw["project"]["chart_heads"] = [{"id": i, "revision": 0} for i in reversed(ids)]
    index.current_head = index.record_head(raw)
    records = list(index.charts.values())
    index.limits = replace(
        index.limits,
        max_current_bytes=sum(r.source_bytes for r in records),
        max_current_nodes=sum(r.node_count for r in records),
    )
    before = state(index)
    assert [c.id for c in index.materialize_current().charts] == list(reversed(ids))
    assert state(index) == before
    for field in ("max_current_bytes", "max_current_nodes"):
        limits = index.limits
        index.limits = replace(limits, **{field: getattr(limits, field) - 1})
        with monkeypatch.context() as m:
            m.setattr(index, "load_grid", lambda *args: pytest.fail("loaded before preflight"))
            with pytest.raises(ValueError, match="limit"):
                index.materialize_current()
        index.limits = limits
    record = index.charts[(ids[0], 0)]
    index.charts[(ids[0], 0)] = replace(record, grid_revision=1)
    with pytest.raises(ValueError, match="grid"):
        index.materialize_current()
    del index.charts[(ids[0], 0)]
    with pytest.raises(ValueError, match="Missing chart"):
        index.materialize_current()
    index.charts[(ids[0], 0)] = record
    record.path.write_text(
        json.dumps(
            {"id": ids[0], "revision": 0, "grid_revision": 0, "events": [], "review": "changed"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        index.materialize_current()
    index.cancel = lambda: (_ for _ in ()).throw(RuntimeError("cancel"))
    with pytest.raises(RuntimeError, match="cancel"):
        index.materialize_current()


def test_materialize_rejects_unset_and_foreign_heads(tmp_path):
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    with pytest.raises(ValueError):
        index.materialize_current()
    foreign = NativeHistoryIndex(tmp_path, PROJECT_ID)
    index.current_head = foreign.record_head(head_input())
    with pytest.raises(ValueError):
        index.materialize_current()


def materialization_index(root, *, chart_count=2):
    index = NativeHistoryIndex(root, PROJECT_ID)
    path, raw = write_source(root, "grid")
    index.record_grid(path, raw)
    ids = []
    for n in range(chart_count):
        chart_id = f"00000000-0000-0000-0000-{n + 2:012d}"
        chart = {**source_input("chart"), "id": chart_id}
        path = root / f"chart-{chart_id}-0.json"
        path.write_text(json.dumps(chart))
        index.record_chart(path, chart)
        ids.append(chart_id)
    head = head_input()
    head["project"]["chart_heads"] = [{"id": chart_id, "revision": 0} for chart_id in reversed(ids)]
    index.current_head = index.record_head(head)
    return index


@pytest.mark.parametrize(
    "failure", ["bytes", "nodes", "missing_chart", "missing_grid", "mixed_grid"]
)
def test_materialize_preflight_failures_perform_zero_source_reads(tmp_path, monkeypatch, failure):
    from ai_edda import native_history_reader as reader

    index = materialization_index(tmp_path)
    total_bytes = sum(r.source_bytes for r in index.charts.values())
    total_nodes = sum(r.node_count for r in index.charts.values())
    index.limits = replace(
        index.limits, max_current_bytes=total_bytes, max_current_nodes=total_nodes
    )
    if failure == "bytes":
        index.limits = replace(index.limits, max_current_bytes=total_bytes - 1)
    elif failure == "nodes":
        index.limits = replace(index.limits, max_current_nodes=total_nodes - 1)
    elif failure == "missing_chart":
        del index.charts[next(iter(index.charts))]
    elif failure == "missing_grid":
        index.grids.clear()
    else:
        key = next(iter(index.charts))
        index.charts[key] = replace(index.charts[key], grid_revision=1)
    before = state(index)

    def unexpected_read(*args, **kwargs):
        pytest.fail("Source read occurred before selected-state preflight finished")

    monkeypatch.setattr(reader, "read_native_history_json", unexpected_read)
    with pytest.raises(ValueError):
        index.materialize_current()
    assert state(index) == before


@pytest.mark.parametrize("chart_count", [0, 2])
def test_materialize_returns_fresh_snapshot_and_models_without_cache(tmp_path, chart_count):
    from ai_edda.project_store import ProjectSnapshot

    index = materialization_index(tmp_path, chart_count=chart_count)
    before = state(index)
    keys_before = set(vars(index))
    first = index.materialize_current()
    second = index.materialize_current()
    assert isinstance(first, ProjectSnapshot)
    assert first == second and first is not second
    assert first.grid is not second.grid
    assert [chart.id for chart in first.charts] == [
        ref.id for ref in index.current_head.project.chart_heads
    ]
    assert all(a is not b for a, b in zip(first.charts, second.charts))
    assert state(index) == before
    assert set(vars(index)) == keys_before


@pytest.mark.parametrize("source", ["chart", "grid"])
def test_materialize_source_changes_fail_without_index_mutation(tmp_path, source):
    index = materialization_index(tmp_path)
    record = next(iter(index.charts.values())) if source == "chart" else index.grids[0]
    raw = json.loads(record.path.read_text())
    raw["review"] = "changed"
    record.path.write_text(json.dumps(raw))
    before = state(index)
    with pytest.raises(ValueError, match="changed"):
        index.materialize_current()
    assert state(index) == before


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, RecursionError])
def test_materialize_cancellation_at_preflight_reads_and_return_preserves_state(
    tmp_path, error_type
):
    index = materialization_index(tmp_path)
    checkpoints = []
    index.cancel = lambda: checkpoints.append(None)
    index.materialize_current()
    for stop in (1, 3, len(checkpoints) // 2, len(checkpoints)):
        calls = 0
        error = error_type("cancel")

        def cancel():
            nonlocal calls
            calls += 1
            if calls == stop:
                raise error

        index.cancel = cancel
        before = state(index)
        with pytest.raises(error_type) as caught:
            index.materialize_current()
        assert caught.value is error
        assert state(index) == before


def test_raw_project_helper_has_no_retention_or_capacity_side_effects(tmp_path, monkeypatch):
    index = NativeHistoryIndex(
        tmp_path, PROJECT_ID, limits=NativeHistoryLimits(max_index_chart_refs=1)
    )
    raw = head_input()
    raw["project"]["chart_heads"] = [{"id": CHART_ID, "revision": 0}]
    index.current_head = index.record_head(raw)
    index.limits = replace(index.limits, max_index_bytes=index._index_bytes)
    assert index._index_chart_refs == index.limits.max_index_chart_refs
    before = state(index)
    before_raw = deepcopy(raw)
    import copy

    def unexpected(*args, **kwargs):
        pytest.fail("Raw-project checks performed retention or aggregate model work")

    monkeypatch.setattr(index, "_size", unexpected)
    monkeypatch.setattr(index, "_reserve", unexpected)
    monkeypatch.setattr(SongProject, "model_validate", unexpected)
    monkeypatch.setattr(copy, "deepcopy", unexpected)
    index.cancel = unexpected
    assert index._check_project_raw(raw["project"]) == 1
    assert raw == before_raw
    # Remove the copy spy before taking the independent state snapshot.
    monkeypatch.undo()
    assert state(index) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "project_id",
        "schema_bool",
        "schema_other",
        "revision_bool",
        "grid_revision_float",
        "chart_id",
        "chart_revision_bool",
        "duplicate_chart",
        "null_chart",
        "analysis_id",
        "analysis_revision",
        "plan_inputs_id",
    ],
)
def test_raw_project_helper_keeps_strict_checks_and_failure_state(tmp_path, mutation):
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    raw = head_input()["project"]
    raw["chart_heads"] = [{"id": CHART_ID, "revision": 0}]
    if mutation == "project_id":
        raw["id"] = CHART_ID
    elif mutation == "schema_bool":
        raw["schema_version"] = True
    elif mutation == "schema_other":
        raw["schema_version"] = 2
    elif mutation == "revision_bool":
        raw["revision"] = False
    elif mutation == "grid_revision_float":
        raw["grid_revision"] = 0.0
    elif mutation == "chart_id":
        raw["chart_heads"][0]["id"] = "bad"
    elif mutation == "chart_revision_bool":
        raw["chart_heads"][0]["revision"] = False
    elif mutation == "duplicate_chart":
        raw["chart_heads"] *= 2
    elif mutation == "null_chart":
        raw["chart_heads"] = [None]
    elif mutation == "analysis_id":
        raw["analysis"] = {"id": "bad", "revision": 0}
    elif mutation == "analysis_revision":
        raw["analysis"] = {"id": CHART_ID, "revision": "0"}
    else:
        raw["plan_inputs"] = {"id": "bad", "revision": 0}
    before, before_raw = state(index), deepcopy(raw)
    with pytest.raises(ValueError):
        index._check_project_raw(raw)
    assert state(index) == before
    assert raw == before_raw


def test_raw_project_helper_enforces_absolute_reference_bound(tmp_path):
    raw = head_input()["project"]
    raw["chart_heads"] = [
        {"id": CHART_ID, "revision": 0},
        {"id": "00000000-0000-0000-0000-000000000003", "revision": 0},
    ]
    index = NativeHistoryIndex(
        tmp_path, PROJECT_ID, limits=NativeHistoryLimits(max_index_chart_refs=1)
    )
    before = state(index)
    with pytest.raises(ValueError, match="reference limit"):
        index._check_project_raw(raw)
    assert state(index) == before


@pytest.mark.parametrize(
    "mutation", ["wide_project", "wide_metadata", "wide_reference", "bad_presets", "bad_sources"]
)
def test_raw_project_helper_stops_at_first_collection_error(tmp_path, mutation):
    from pydantic import ValidationError

    raw = head_input()["project"]
    if mutation == "wide_project":
        target = raw
    elif mutation == "wide_metadata":
        target = raw.setdefault("metadata", {})
    elif mutation == "wide_reference":
        raw["chart_heads"] = [{"id": CHART_ID, "revision": 0}]
        target = raw["chart_heads"][0]
    elif mutation == "bad_presets":
        raw["export_presets"] = [None] * 1000
        target = None
    else:
        raw["provenance"] = {"source_ids": [None] * 1000}
        target = None
    if target is not None:
        target.update({f"unknown_{n}": None for n in range(1000)})
    index = NativeHistoryIndex(tmp_path, PROJECT_ID)
    before = state(index)
    with pytest.raises(ValueError) as caught:
        index._check_project_raw(raw)
    if isinstance(caught.value, ValidationError):
        assert caught.value.error_count() == 1
    assert state(index) == before
