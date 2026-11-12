"""Validate native project snapshots and all receipt chains."""

import json
import math
import re
from pathlib import Path
from uuid import UUID

from .asset_catalog import _safe
from .domain import SongProject
from .native_envelope import _unique_pairs

MAX_HISTORY_JSON_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_NODES = 1_000_000
MAX_READ_BYTES = 1024 * 1024
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_RECEIPT = r"receipt-[0-9a-f]+\.json"


def _integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid revision")
    return value


def _uuid(value):
    if not isinstance(value, str) or not re.fullmatch(_UUID, value):
        raise ValueError("Invalid UUID")
    try:
        if str(UUID(value)) != value:
            raise ValueError("Invalid UUID")
    except (ValueError, AttributeError) as exc:
        raise ValueError("Invalid UUID") from exc


def _walk(value, depth=0):
    if depth > 64:
        raise ValueError("JSON nesting exceeds limit")
    if isinstance(value, dict):
        yield 1
        for key, child in value.items():
            yield 1
            yield from _walk(child, depth + 1)
    elif isinstance(value, list):
        yield 1
        for child in value:
            yield from _walk(child, depth + 1)
    else:
        yield 1
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite JSON")


def _read(path, budget):
    path = _safe(path)
    with path.open("rb") as stream:
        chunks = []
        size = 0
        while True:
            chunk = stream.read(
                min(MAX_READ_BYTES, max(1, min(MAX_HISTORY_JSON_BYTES, budget[0]) - size + 1))
            )
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_HISTORY_JSON_BYTES or size > budget[0]:
                raise ValueError("History JSON exceeds limit")
            chunks.append(chunk)
        data = b"".join(chunks)
    budget[0] -= len(data)
    try:
        value = json.loads(
            data,
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")),
        )
        for _ in _walk(value):
            budget[1] -= 1
            if budget[1] < 0:
                raise ValueError("JSON node count exceeds limit")
        return value
    except (TypeError, KeyError, RecursionError) as exc:
        raise ValueError("Invalid history JSON") from exc


def _scan_history_files(
    project_dir, expected_project_id, *, limits=None, max_files=None, max_bytes=None, cancel=None
):
    """Index immutable chart and grid files and collect receipt paths."""
    import os
    import stat

    from .native_envelope import MAX_DECLARED_BYTES, MAX_FILES
    from .native_history_index import DEFAULT_PROFILE, NativeHistoryIndex
    from .native_history_reader import read_native_history_json

    cancellation_error = []

    def check_cancel():
        if cancel is not None:
            try:
                cancel()
            except OSError as exc:
                cancellation_error.append(exc)
                raise

    root = _safe(Path(project_dir))
    index = NativeHistoryIndex(
        root, expected_project_id, limits=limits or DEFAULT_PROFILE, cancel=check_cancel
    )
    max_files = MAX_FILES if max_files is None else max_files
    max_bytes = MAX_DECLARED_BYTES if max_bytes is None else max_bytes
    if type(max_files) is not int or max_files < 0 or type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("Invalid scan limits")
    count = size = 0
    receipts = []

    def onerror(error):
        raise error

    try:
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
            for name in dirs + names:
                check_cancel()
                path = Path(directory) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Unsafe path")
                if stat.S_ISDIR(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Unsupported file")
                count += 1
                size += info.st_size
                if count > max_files or size > max_bytes:
                    raise ValueError("Project exceeds limit")
                if path.parent != root:
                    continue
                g = re.fullmatch(r"grid-([0-9]+)\.json", name)
                c = re.fullmatch(rf"chart-({_UUID})-([0-9]+)\.json", name)
                if g or c:
                    raw = read_native_history_json(
                        path, limits=index.limits.read_limits, cancel=check_cancel
                    )
                    try:
                        record = (
                            index.record_grid(path, raw) if g else index.record_chart(path, raw)
                        )
                    finally:
                        del raw
                    if g and str(record.revision) != g[1]:
                        raise ValueError("Grid filename mismatch")
                    if c and (record.chart_id != c[1] or str(record.revision) != c[2]):
                        raise ValueError("Chart filename mismatch")
                elif re.fullmatch(_RECEIPT, name):
                    receipts.append(path)
                elif name.startswith(("grid-", "chart-", "receipt-")):
                    raise ValueError("Invalid history filename")
        for chart in index.charts.values():
            check_cancel()
            if chart.grid_revision not in index.grids:
                raise ValueError("Missing chart grid")
        return index, tuple(receipts)
    except OSError as exc:
        if cancellation_error and exc is cancellation_error[-1]:
            raise
        raise ValueError("Cannot scan project") from exc


def validate_native_history(project_dir, expected_project_id, *, cancel=None):
    cancellation = []

    def check():
        if cancel is not None:
            try:
                cancel()
            except BaseException as exc:
                cancellation.append(exc)
                raise

    try:
        return _build_native_history_index(project_dir, expected_project_id, cancel=check)
    except (TypeError, KeyError, RecursionError) as exc:
        if cancellation:
            raise cancellation[0]
        raise ValueError("Invalid native history") from exc


def _build_native_history_index(root, expected_project_id, *, limits=None, cancel=None):
    """Build a bounded metadata index and validate every receipt chain."""
    from .native_history_index import ReceiptRecord, validated_chart, validated_grid
    from .native_history_reader import read_native_history_json

    index, paths = _scan_history_files(root, expected_project_id, limits=limits, cancel=cancel)

    def check():
        if cancel is not None:
            cancel()

    def head(raw):
        record = index.record_head(raw)
        project = record.project
        if project.grid_revision not in index.grids:
            raise ValueError("Missing head grid")
        for ref in project.chart_heads:
            check()
            chart = index.charts.get((ref.id, ref.revision))
            if chart is None or chart.grid_revision != project.grid_revision:
                raise ValueError("Missing or mismatched head chart")
        return record

    current = head(
        read_native_history_json(
            index.root / "project.json", limits=index.limits.read_limits, cancel=cancel
        )
    )
    index.current_head = current
    if current.raw_head["receipt"] is None and current.project.revision != 0:
        raise ValueError("Initial head revision mismatch")
    for path in paths:
        check()
        receipt = read_native_history_json(path, limits=index.limits.read_limits, cancel=cancel)
        if not isinstance(receipt, dict) or set(receipt) != {
            "command_id",
            "payload_hash",
            "previous_head",
            "new_head",
            "previous_receipt",
            "result",
        }:
            raise ValueError("Invalid receipt")
        previous = head(receipt["previous_head"])
        new = head(receipt["new_head"])
        pointer = receipt["previous_receipt"]
        if (
            new.raw_head["receipt"] != path.name
            or pointer != previous.raw_head["receipt"]
            or new.project.revision != previous.project.revision + 1
        ):
            raise ValueError("Invalid receipt transition")
        if pointer is None and previous.project.revision != 0:
            raise ValueError("Invalid initial receipt")
        result = receipt["result"]
        if isinstance(result, dict) and {"project", "grid", "charts"} <= set(result):
            if set(result) != {"project", "grid", "charts"}:
                raise ValueError("Invalid project view result")
            index._check_project_raw(result["project"])
            result_project = SongProject.model_validate(result["project"])
            if result_project != new.project:
                raise ValueError("Receipt project mismatch")
            del result_project
            grid = validated_grid(result["grid"], limits=index.limits, cancel=cancel)
            if grid.revision != new.project.grid_revision or not index.grid_matches(grid):
                raise ValueError("Receipt grid mismatch")
            del grid
            charts = result["charts"]
            if not isinstance(charts, list) or len(charts) != len(new.project.chart_heads):
                raise ValueError("Receipt charts mismatch")
            for i, ref in enumerate(new.project.chart_heads):
                check()
                chart = validated_chart(charts[i], limits=index.limits, cancel=cancel)
                if (chart.id, chart.revision) != (ref.id, ref.revision) or not index.chart_matches(
                    chart
                ):
                    raise ValueError("Receipt chart mismatch")
                charts[i] = None
                del chart
        index.record_receipt(
            path.name,
            ReceiptRecord(receipt["command_id"], receipt["payload_hash"], previous, new, pointer),
        )
        del result, receipt
    for record in index.receipts.values():
        check()
        if record.previous_receipt is not None:
            prior = index.receipts.get(record.previous_receipt)
            if prior is None or prior.new_head.raw_head != record.previous_head.raw_head:
                raise ValueError("Broken receipt chain")
    pointer = current.raw_head["receipt"]
    if pointer is not None:
        prior = index.receipts.get(pointer)
        if prior is None or prior.new_head.raw_head != current.raw_head:
            raise ValueError("Broken current head")
    return index
