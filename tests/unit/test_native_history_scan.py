import json
import os
import subprocess
import weakref
from uuid import UUID

import pytest

from ai_edda.native_history import _scan_history_files

PROJECT = "12345678-1234-1234-1234-123456789abc"


def test_scan_receipts_and_limits(tmp_path):
    (tmp_path / "receipt-ab.json").write_text("not parsed")
    index, receipts = _scan_history_files(tmp_path, PROJECT)
    assert not index.charts and [p.name for p in receipts] == ["receipt-ab.json"]
    with pytest.raises(ValueError, match="limit"):
        _scan_history_files(tmp_path, PROJECT, max_files=0)
    with pytest.raises(ValueError, match="limit"):
        _scan_history_files(tmp_path, PROJECT, max_bytes=1)


def test_scan_rejects_bad_filename_and_cancels(tmp_path):
    (tmp_path / "grid-invalid.json").write_text("{}")
    with pytest.raises(ValueError, match="filename"):
        _scan_history_files(tmp_path, PROJECT)

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        _scan_history_files(tmp_path, PROJECT, cancel=cancel)


CHART = "00000000-0000-0000-0000-000000000001"


def write_grid(root, revision=0):
    raw = {
        "revision": revision,
        "segments": [
            {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4},
        ],
    }
    path = root / f"grid-{revision}.json"
    path.write_text(json.dumps(raw))
    return path


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, RecursionError, OSError])
def test_scan_cancellation_preserves_exact_exception(tmp_path, error_type):
    (tmp_path / "receipt-a.json").write_text("{}")
    error = error_type("cancel")

    def cancel():
        raise error

    with pytest.raises(error_type) as caught:
        _scan_history_files(tmp_path, PROJECT, cancel=cancel)
    assert caught.value is error


@pytest.mark.parametrize("stage", ["reader", "index_record", "index_accounting"])
@pytest.mark.parametrize("error_type", [OSError, FileNotFoundError, RuntimeError])
def test_scan_delayed_inner_cancellation_preserves_exact_exception(
    tmp_path, monkeypatch, stage, error_type
):
    from ai_edda import native_history_reader as reader
    from ai_edda.native_history_index import NativeHistoryIndex

    write_grid(tmp_path)
    active = None
    error = error_type("delayed cancel")
    if stage == "reader":
        target, name = reader, "read_native_history_json"
    elif stage == "index_record":
        target, name = NativeHistoryIndex, "record_grid"
    else:
        target, name = NativeHistoryIndex, "_size"
    original = getattr(target, name)

    def entered(*args, **kwargs):
        nonlocal active
        active = stage
        try:
            return original(*args, **kwargs)
        finally:
            active = None

    monkeypatch.setattr(target, name, entered)

    def cancel():
        if active == stage:
            raise error

    with pytest.raises(error_type) as caught:
        _scan_history_files(tmp_path, PROJECT, cancel=cancel)
    assert caught.value is error


def test_scan_counts_nested_regular_files_at_exact_limits(tmp_path):
    nested = tmp_path / "media"
    nested.mkdir()
    (nested / "payload").write_bytes(b"123")
    (tmp_path / "receipt-a.json").write_bytes(b"{}")
    index, receipts = _scan_history_files(tmp_path, PROJECT, max_files=2, max_bytes=5)
    assert not index.charts and len(receipts) == 1
    for change in ({"max_files": 1, "max_bytes": 5}, {"max_files": 2, "max_bytes": 4}):
        with pytest.raises(ValueError, match="limit"):
            _scan_history_files(tmp_path, PROJECT, **change)


@pytest.mark.parametrize(
    "name",
    ["grid-00.json", "grid--1.json", "grid-0.JSON", f"chart-{CHART}-00.json", "receipt-G.json"],
)
def test_scan_rejects_noncanonical_identity_aliases(tmp_path, name):
    if name.startswith("chart-"):
        raw = {"id": CHART, "revision": 0, "grid_revision": 0}
    else:
        raw = {
            "revision": 0,
            "segments": [
                {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4},
            ],
        }
    (tmp_path / name).write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        _scan_history_files(tmp_path, PROJECT)


def test_scan_missing_grid_rejected(tmp_path):
    (tmp_path / f"chart-{CHART}-0.json").write_text(
        json.dumps({"id": CHART, "revision": 0, "grid_revision": 0})
    )
    with pytest.raises(ValueError, match="Missing chart grid"):
        _scan_history_files(tmp_path, PROJECT)


def test_scan_ten_thousand_events_retains_metadata_only(tmp_path):
    write_grid(tmp_path)
    raw = {
        "id": CHART,
        "revision": 0,
        "grid_revision": 0,
        "events": [
            {
                "id": str(UUID(int=100 + i)),
                "time_ref": {"kind": "audio", "time_sec": i * 0.1},
                "notes": [{"id": str(UUID(int=20100 + i)), "lane": i % 4}],
            }
            for i in range(10000)
        ],
    }
    path = tmp_path / f"chart-{CHART}-0.json"
    path.write_text(json.dumps(raw))
    index, receipts = _scan_history_files(tmp_path, PROJECT)
    assert receipts == ()
    assert len(index.charts) == len(index.grids) == 1
    record = index.charts[(CHART, 0)]
    assert record.node_count == 640037
    assert record.source_bytes == path.stat().st_size
    assert not hasattr(record, "events")
    assert not hasattr(index.grids[0], "segments")
    assert index.heads == [] and index.current_head is None and index.receipts == {}


def test_scan_releases_previous_raw_record_before_next_read(tmp_path, monkeypatch):
    from ai_edda import native_history_reader as reader

    write_grid(tmp_path, 0)
    write_grid(tmp_path, 1)
    original = reader.read_native_history_json
    previous = []
    previous_path = None

    class TrackedDict(dict):
        pass

    def observed(path, **kwargs):
        nonlocal previous_path, previous
        if previous_path is not None and path != previous_path:
            assert all(ref() is None for ref in previous), (
                "Previous parsed record remains live during next read"
            )
            previous = []
        result = TrackedDict(original(path, **kwargs))
        previous_path = path
        previous.append(weakref.ref(result))
        return result

    monkeypatch.setattr(reader, "read_native_history_json", observed)
    index, _ = _scan_history_files(tmp_path, PROJECT)
    assert len(index.grids) == 2
    assert all(ref() is None for ref in previous)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_scan_rejects_nested_windows_junction(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    (target / "payload").write_text("outside")
    junction = root / "media"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "New-Item -ItemType Junction -Path $env:NATIVE_SCAN_LINK "
            "-Target $env:NATIVE_SCAN_TARGET | Out-Null",
        ],
        env={**os.environ, "NATIVE_SCAN_LINK": str(junction), "NATIVE_SCAN_TARGET": str(target)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ValueError, match="Unsafe path"):
            _scan_history_files(root, PROJECT)
    finally:
        os.rmdir(junction)
