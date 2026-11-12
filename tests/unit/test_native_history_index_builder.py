import json
import weakref
from collections import Counter
from copy import deepcopy

import pytest

from ai_edda.native_history import _build_native_history_index, validate_native_history

PROJECT = "12345678-1234-1234-1234-123456789abc"


def write(path, value):
    path.write_text(json.dumps(value))


def history(root):
    grid = {
        "revision": 0,
        "segments": [
            {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4}
        ],
    }
    old = {
        "project": {
            "id": PROJECT,
            "schema_version": 1,
            "revision": 0,
            "grid_revision": 0,
            "chart_heads": [],
        },
        "receipt": None,
    }
    new = deepcopy(old)
    new["project"]["revision"] = 1
    new["receipt"] = "receipt-a.json"
    receipt = {
        "command_id": "",
        "payload_hash": "a" * 64,
        "previous_head": old,
        "new_head": new,
        "previous_receipt": None,
        "result": {"project": new["project"], "grid": grid, "charts": []},
    }
    write(root / "grid-0.json", grid)
    write(root / "project.json", new)
    write(root / "receipt-a.json", receipt)
    return receipt


def test_valid_matches_fixture(tmp_path):
    receipt = history(tmp_path)
    index = _build_native_history_index(tmp_path, PROJECT)
    assert (
        index.current_head.project.model_dump(mode="json", exclude_unset=True)
        == receipt["new_head"]["project"]
    )
    assert set(index.receipts) == {"receipt-a.json"}


@pytest.mark.parametrize("mutation", ["opaque", "bad_grid", "bad_revision", "bad_pointer", "extra"])
def test_receipt_validation(tmp_path, mutation):
    receipt = history(tmp_path)
    if mutation == "opaque":
        receipt["result"] = {"project": "opaque"}
    elif mutation == "bad_grid":
        receipt["result"]["grid"]["revision"] = 1
    elif mutation == "bad_revision":
        receipt["previous_head"]["project"]["revision"] = True
    elif mutation == "bad_pointer":
        receipt["previous_receipt"] = "receipt-b.json"
    else:
        receipt["result"]["extra"] = 1
    write(tmp_path / "receipt-a.json", receipt)
    if mutation == "opaque":
        _build_native_history_index(tmp_path, PROJECT)
    else:
        with pytest.raises((ValueError, KeyError, TypeError)):
            _build_native_history_index(tmp_path, PROJECT)


@pytest.mark.parametrize("kind", [OSError, TypeError, KeyError])
def test_cancel_identity(tmp_path, kind):
    history(tmp_path)
    error = kind("stop")

    def cancel():
        raise error

    with pytest.raises(kind) as caught:
        _build_native_history_index(tmp_path, PROJECT, cancel=cancel)
    assert caught.value is error


def test_branch_requires_complete_raw_head(tmp_path):
    first = history(tmp_path)
    second = deepcopy(first)
    second["previous_head"] = deepcopy(first["new_head"])
    second["previous_head"]["project"]["metadata"] = {"title": "different"}
    second["previous_receipt"] = "receipt-a.json"
    second["new_head"]["project"]["revision"] = 2
    second["new_head"]["receipt"] = "receipt-b.json"
    second["result"] = None
    write(tmp_path / "receipt-b.json", second)
    with pytest.raises(ValueError, match="chain"):
        _build_native_history_index(tmp_path, PROJECT)


@pytest.mark.parametrize("change", ["note", "provenance", "order", "unknown"])
def test_result_chart_content_and_references(tmp_path, change):
    receipt = history(tmp_path)
    ids = ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]
    charts = []
    for chart_id in ids:
        chart = {
            "id": chart_id,
            "revision": 0,
            "grid_revision": 0,
            "events": [
                {
                    "id": "00000000-0000-0000-0000-000000000003",
                    "time_ref": {"kind": "audio", "time_sec": 1},
                    "notes": [{"id": "00000000-0000-0000-0000-000000000004", "lane": 0}],
                }
            ],
        }
        charts.append(chart)
        write(tmp_path / f"chart-{chart_id}-0.json", chart)
    refs = [{"id": chart_id, "revision": 0} for chart_id in ids]
    receipt["new_head"]["project"]["chart_heads"] = refs
    receipt["result"]["project"]["chart_heads"] = refs
    receipt["result"]["charts"] = deepcopy(charts)
    write(tmp_path / "project.json", receipt["new_head"])
    write(tmp_path / "receipt-a.json", receipt)
    _build_native_history_index(tmp_path, PROJECT)
    if change == "note":
        receipt["result"]["charts"][0]["events"][0]["notes"][0]["lane"] = 1
    elif change == "provenance":
        receipt["result"]["charts"][0]["provenance"] = {"operation_id": "changed"}
    elif change == "order":
        receipt["result"]["charts"].reverse()
    else:
        receipt["new_head"]["project"]["chart_heads"][0]["revision"] = 42
    write(tmp_path / "receipt-a.json", receipt)
    with pytest.raises(ValueError):
        _build_native_history_index(tmp_path, PROJECT)


def test_malformed_late_receipt(tmp_path):
    history(tmp_path)
    write(tmp_path / "receipt-b.json", {"command_id": "incomplete"})
    with pytest.raises(ValueError, match="receipt"):
        _build_native_history_index(tmp_path, PROJECT)


@pytest.mark.parametrize("kind", [OSError, TypeError, KeyError])
def test_cancel_inside_result_validation(tmp_path, monkeypatch, kind):
    from ai_edda import native_history_index

    history(tmp_path)
    error = kind("stop in grid")
    active = False
    original = native_history_index.validated_grid

    def wrapped(*args, **kwargs):
        nonlocal active
        active = True
        try:
            return original(*args, **kwargs)
        finally:
            active = False

    monkeypatch.setattr(native_history_index, "validated_grid", wrapped)

    def cancel():
        if active:
            raise error

    with pytest.raises(kind) as caught:
        _build_native_history_index(tmp_path, PROJECT, cancel=cancel)
    assert caught.value is error


def test_result_normalized_project_is_not_retained_as_raw_head(tmp_path):
    from ai_edda.domain import SongProject

    receipt = history(tmp_path)
    receipt["result"]["project"] = SongProject.model_validate(
        receipt["new_head"]["project"]
    ).model_dump(mode="json")
    write(tmp_path / "receipt-a.json", receipt)
    validate_native_history(tmp_path, PROJECT)
    index = _build_native_history_index(tmp_path, PROJECT)
    assert len(index.heads) == 2
    assert all(
        head.raw_head in [receipt["previous_head"], receipt["new_head"]] for head in index.heads
    )


def test_result_normalization_does_not_consume_retained_head_budget(tmp_path):
    from ai_edda.domain import SongProject
    from ai_edda.native_history_index import NativeHistoryLimits

    receipt = history(tmp_path)
    baseline = _build_native_history_index(tmp_path, PROJECT)
    receipt["result"]["project"] = SongProject.model_validate(
        receipt["new_head"]["project"]
    ).model_dump(mode="json")
    write(tmp_path / "receipt-a.json", receipt)
    # Equivalent result normalization must not add retained historical metadata.
    index = _build_native_history_index(
        tmp_path, PROJECT, limits=NativeHistoryLimits(max_index_bytes=baseline._index_bytes)
    )
    assert index._index_bytes == baseline._index_bytes


@pytest.mark.parametrize(
    "change",
    [
        "schema_bool",
        "revision_bool",
        "grid_revision_float",
        "chart_id",
        "chart_revision_bool",
        "analysis_id",
        "plan_inputs_revision",
    ],
)
def test_transient_result_project_keeps_raw_reference_and_revision_checks(tmp_path, change):
    receipt = history(tmp_path)
    reference_id = "00000000-0000-0000-0000-000000000001"
    if change == "chart_revision_bool":
        chart = {"id": reference_id, "revision": 0, "grid_revision": 0}
        write(tmp_path / f"chart-{reference_id}-0.json", chart)
        receipt["new_head"]["project"]["chart_heads"] = [{"id": reference_id, "revision": 0}]
        receipt["result"]["charts"] = [chart]
    elif change == "plan_inputs_revision":
        receipt["new_head"]["project"]["plan_inputs"] = {"id": reference_id, "revision": 0}
    write(tmp_path / "project.json", receipt["new_head"])
    project = receipt["result"]["project"] = deepcopy(receipt["result"]["project"])
    if change == "schema_bool":
        project["schema_version"] = True
    elif change == "revision_bool":
        project["revision"] = True
    elif change == "grid_revision_float":
        project["grid_revision"] = 0.0
    elif change == "chart_id":
        project["chart_heads"] = [{"id": "bad", "revision": 0}]
    elif change == "chart_revision_bool":
        project["chart_heads"] = [{"id": "00000000-0000-0000-0000-000000000001", "revision": False}]
    elif change == "analysis_id":
        project["analysis"] = {"id": "bad", "revision": 0}
    else:
        project["plan_inputs"] = {"id": "00000000-0000-0000-0000-000000000001", "revision": "0"}
    write(tmp_path / "receipt-a.json", receipt)
    for validate in (validate_native_history, _build_native_history_index):
        with pytest.raises(ValueError):
            validate(tmp_path, PROJECT)


def test_builder_preserves_valid_unselected_branches_and_orphan_sources(tmp_path):
    first = history(tmp_path)
    branch = deepcopy(first)
    branch["new_head"]["receipt"] = "receipt-b.json"
    branch["new_head"]["project"]["metadata"] = {"title": "branch"}
    branch["result"] = None
    write(tmp_path / "receipt-b.json", branch)
    orphan_id = "00000000-0000-0000-0000-000000000001"
    write(
        tmp_path / f"chart-{orphan_id}-7.json", {"id": orphan_id, "revision": 7, "grid_revision": 0}
    )
    legacy = validate_native_history(tmp_path, PROJECT)
    index = _build_native_history_index(tmp_path, PROJECT)
    assert set(index.receipts) == set(legacy.receipts) == {"receipt-a.json", "receipt-b.json"}
    assert (orphan_id, 7) in index.charts
    assert index.current_head.raw_head == first["new_head"]


def test_builder_rejects_unselected_receipt_chain_gap(tmp_path):
    first = history(tmp_path)
    branch = deepcopy(first)
    branch["previous_head"] = deepcopy(first["new_head"])
    branch["previous_head"]["receipt"] = "receipt-c.json"
    branch["previous_receipt"] = "receipt-c.json"
    branch["new_head"]["project"]["revision"] = 2
    branch["new_head"]["receipt"] = "receipt-b.json"
    branch["result"] = None
    write(tmp_path / "receipt-b.json", branch)
    for validate in (validate_native_history, _build_native_history_index):
        with pytest.raises(ValueError, match="chain"):
            validate(tmp_path, PROJECT)


def test_builder_reads_each_source_fixed_times_without_loading_models_again(tmp_path, monkeypatch):
    from ai_edda import native_history_reader as reader
    from ai_edda.native_history_index import NativeHistoryIndex

    first = history(tmp_path)
    second = deepcopy(first)
    second["new_head"]["receipt"] = "receipt-b.json"
    write(tmp_path / "receipt-b.json", second)
    original = reader.read_native_history_json
    reads = Counter()

    def observed(path, **kwargs):
        reads[path.name] += 1
        return original(path, **kwargs)

    def unexpected_load(*args, **kwargs):
        pytest.fail("Receipt builder materialized an indexed source model")

    monkeypatch.setattr(reader, "read_native_history_json", observed)
    monkeypatch.setattr(NativeHistoryIndex, "load_grid", unexpected_load)
    monkeypatch.setattr(NativeHistoryIndex, "load_chart", unexpected_load)
    _build_native_history_index(tmp_path, PROJECT)
    assert set(reads) == {"grid-0.json", "project.json", "receipt-a.json", "receipt-b.json"}
    assert reads["grid-0.json"] <= 2
    assert reads["project.json"] == reads["receipt-a.json"] == reads["receipt-b.json"] == 1


def test_builder_releases_raw_receipt_before_reading_next(tmp_path, monkeypatch):
    from ai_edda import native_history_reader as reader

    first = history(tmp_path)
    second = deepcopy(first)
    second["new_head"]["receipt"] = "receipt-b.json"
    second["result"] = None
    write(tmp_path / "receipt-b.json", second)
    original = reader.read_native_history_json
    receipts = []

    class TrackedDict(dict):
        pass

    def observed(path, **kwargs):
        if path.name.startswith("receipt-"):
            assert all(reference() is None for reference in receipts)
            result = TrackedDict(original(path, **kwargs))
            receipts.append(weakref.ref(result))
            return result
        return original(path, **kwargs)

    monkeypatch.setattr(reader, "read_native_history_json", observed)
    _build_native_history_index(tmp_path, PROJECT)
    assert all(reference() is None for reference in receipts)


def test_builder_releases_each_result_chart_before_validating_next(tmp_path, monkeypatch):
    from ai_edda import native_history_index as module

    receipt = history(tmp_path)
    ids = [f"00000000-0000-0000-0000-{n:012d}" for n in (1, 2)]
    charts = [{"id": chart_id, "revision": 0, "grid_revision": 0} for chart_id in ids]
    for chart in charts:
        write(tmp_path / f"chart-{chart['id']}-0.json", chart)
    receipt["new_head"]["project"]["chart_heads"] = [{"id": i, "revision": 0} for i in ids]
    receipt["result"]["charts"] = charts
    write(tmp_path / "project.json", receipt["new_head"])
    write(tmp_path / "receipt-a.json", receipt)
    original = module.validated_chart
    references = []

    def observed(*args, **kwargs):
        assert all(reference() is None for reference in references)
        result = original(*args, **kwargs)
        references.append(weakref.ref(result))
        return result

    monkeypatch.setattr(module, "validated_chart", observed)
    _build_native_history_index(tmp_path, PROJECT)
    assert all(reference() is None for reference in references)
