import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ai_edda import native_history_reader as reader
from ai_edda.native_history_reader import NativeHistoryReadLimits, read_native_history_json


def test_limits():
    for field, value in [
        ("max_bytes", True),
        ("max_nodes", 0),
        ("max_depth", -1),
        ("max_read_bytes", 1048577),
    ]:
        with pytest.raises(ValueError):
            replace(NativeHistoryReadLimits(), **{field: value})


def test_boundaries_and_types(tmp_path):
    path = tmp_path / "record.json"
    path.write_bytes(b"[true,1,1.0]")
    limits = NativeHistoryReadLimits(max_bytes=12, max_nodes=4, max_depth=1, max_read_bytes=2)
    result = read_native_history_json(path, limits=limits)
    assert [type(x) for x in result] == [bool, int, float]
    for change in ({"max_bytes": 11}, {"max_nodes": 3}, {"max_depth": 0}):
        with pytest.raises(ValueError):
            read_native_history_json(path, limits=replace(limits, **change))


@pytest.mark.parametrize("data", [b'{"a":1,"a":2}', b"NaN", b"1e999", b"["])
def test_invalid(tmp_path, data):
    path = tmp_path / "record.json"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        read_native_history_json(path, limits=NativeHistoryReadLimits())


def test_directory_and_symlink(tmp_path):
    with pytest.raises(ValueError):
        read_native_history_json(tmp_path, limits=NativeHistoryReadLimits())
    path = tmp_path / "file"
    path.write_text("1")
    link = tmp_path / "link"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(ValueError):
        read_native_history_json(link, limits=NativeHistoryReadLimits())


def test_cancel_identity(tmp_path):
    path = tmp_path / "file"
    path.write_text("[1,2]")
    error = RuntimeError("cancel")

    def cancel():
        raise error

    with pytest.raises(RuntimeError) as caught:
        read_native_history_json(path, limits=NativeHistoryReadLimits(), cancel=cancel)
    assert caught.value is error


@pytest.mark.parametrize("field", ["max_bytes", "max_nodes", "max_depth", "max_read_bytes"])
@pytest.mark.parametrize("value", [False, True, 1.0, "1", None, -1])
def test_limits_require_integers(field, value):
    with pytest.raises(ValueError):
        replace(NativeHistoryReadLimits(), **{field: value})


def test_scalar_depth_zero_and_object_key_node_boundary(tmp_path):
    path = tmp_path / "record.json"
    path.write_text("true")
    assert (
        read_native_history_json(path, limits=NativeHistoryReadLimits(max_depth=0, max_nodes=1))
        is True
    )
    path.write_text('{"a":null}')
    assert read_native_history_json(path, limits=NativeHistoryReadLimits(max_nodes=3)) == {
        "a": None
    }
    with pytest.raises(ValueError, match="node count"):
        read_native_history_json(path, limits=NativeHistoryReadLimits(max_nodes=2))


class ShortReadFile:
    def __init__(self, stream, requests, *, after_read=None):
        self.stream = stream
        self.requests = requests
        self.after_read = after_read

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()

    def fileno(self):
        return self.stream.fileno()

    def read(self, size=-1):
        assert 0 < size <= 1024 * 1024
        self.requests.append(size)
        data = self.stream.read(min(2, size))
        if self.after_read is not None:
            self.after_read()
        return data


def test_repeated_short_reads_and_maximum_request(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_bytes(b'{"short":[1,2,3]}')
    original_open = Path.open
    requests = []
    monkeypatch.setattr(
        Path, "open", lambda self, *a, **kw: ShortReadFile(original_open(self, *a, **kw), requests)
    )
    limits = NativeHistoryReadLimits(max_bytes=path.stat().st_size, max_read_bytes=3)
    assert read_native_history_json(path, limits=limits) == {"short": [1, 2, 3]}
    assert len(requests) > 5
    assert max(requests) == 3


def test_actual_bytes_reject_file_growing_after_fstat(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_bytes(b"[]")
    original_open = Path.open
    requests = []
    streams = []

    def grow():
        if len(requests) == 1:
            with original_open(path, "ab") as output:
                output.write(b" " * 20)

    def open_short(self, *args, **kwargs):
        stream = ShortReadFile(original_open(self, *args, **kwargs), requests, after_read=grow)
        streams.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_short)
    with pytest.raises(ValueError, match="exceeds limit"):
        read_native_history_json(
            path, limits=NativeHistoryReadLimits(max_bytes=8, max_read_bytes=3)
        )
    assert all(stream.stream.closed for stream in streams)
    assert sum(min(2, size) for size in requests) == 9


@pytest.mark.parametrize("stage", ["read", "before_decode", "after_decode", "traversal"])
@pytest.mark.parametrize("error_type", [RuntimeError, RecursionError])
def test_cancel_stage_preserves_exact_exception(tmp_path, monkeypatch, stage, error_type):
    path = tmp_path / "record.json"
    path.write_text("[1,2,3]")
    original_open, original_loads = Path.open, json.loads
    state = {"eof": False, "decoded": False, "after_decode_checks": 0}
    streams = []

    class TrackedFile(ShortReadFile):
        def read(self, size=-1):
            result = super().read(size)
            state["eof"] = not result
            return result

    def open_tracked(self, *args, **kwargs):
        stream = TrackedFile(original_open(self, *args, **kwargs), [])
        streams.append(stream)
        return stream

    def loads(*args, **kwargs):
        result = original_loads(*args, **kwargs)
        state["decoded"] = True
        return result

    error = error_type("cancel at " + stage)

    def cancel():
        if state["decoded"]:
            state["after_decode_checks"] += 1
        reached = {
            "read": bool(streams) and not state["eof"],
            "before_decode": state["eof"] and not state["decoded"],
            "after_decode": state["after_decode_checks"] == 1,
            "traversal": state["after_decode_checks"] == 4,
        }
        if reached[stage]:
            raise error

    monkeypatch.setattr(Path, "open", open_tracked)
    monkeypatch.setattr(reader.json, "loads", loads)
    with pytest.raises(error_type) as caught:
        read_native_history_json(path, limits=NativeHistoryReadLimits(), cancel=cancel)
    assert caught.value is error
    assert all(stream.stream.closed for stream in streams)


def test_deep_decode_recursion_becomes_value_error(tmp_path):
    path = tmp_path / "record.json"
    path.write_text("[" * 2000 + "0" + "]" * 2000)
    with pytest.raises(ValueError, match="nesting exceeds limit"):
        read_native_history_json(path, limits=NativeHistoryReadLimits())


def test_duplicate_long_key_has_bounded_error(tmp_path):
    path = tmp_path / "record.json"
    key = "key" * 100_000
    path.write_text(json.dumps({key: 1})[:-1] + "," + json.dumps(key) + ":2}")
    with pytest.raises(ValueError) as caught:
        read_native_history_json(path, limits=NativeHistoryReadLimits())
    assert str(caught.value) == "Duplicate JSON key"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_reject_real_windows_junction_parent(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "record.json").write_text("1")
    junction = tmp_path / "junction"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "New-Item -ItemType Junction -Path $env:NATIVE_READER_TEST_LINK "
            "-Target $env:NATIVE_READER_TEST_TARGET | Out-Null",
        ],
        env={
            **os.environ,
            "NATIVE_READER_TEST_LINK": str(junction),
            "NATIVE_READER_TEST_TARGET": str(target),
        },
        capture_output=True,
        text=True,
    )
    if result.returncode:
        pytest.fail("Could not create Windows junction: " + result.stderr)
    try:
        assert junction.lstat().st_file_attributes & 0x400
        with pytest.raises(ValueError, match="Unsafe path"):
            read_native_history_json(junction / "record.json", limits=NativeHistoryReadLimits())
    finally:
        # Remove the link itself. Do not recurse into the target.
        os.rmdir(junction)
