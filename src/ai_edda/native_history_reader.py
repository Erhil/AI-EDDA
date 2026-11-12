"""Read one native history JSON record with bounded resources."""

import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .asset_catalog import _safe
from .native_envelope import _unique_pairs


@dataclass(frozen=True)
class NativeHistoryReadLimits:
    max_bytes: int = 64 * 1024 * 1024
    max_nodes: int = 6_000_000
    max_depth: int = 64
    max_read_bytes: int = 1024 * 1024

    def __post_init__(self):
        for name in ("max_bytes", "max_nodes", "max_depth", "max_read_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "max_depth" else 1):
                raise ValueError(f"Invalid {name}")
        if self.max_read_bytes > 1024 * 1024:
            raise ValueError("Invalid max_read_bytes")


def read_native_history_json(
    path: Path, *, limits: NativeHistoryReadLimits, cancel: Callable[[], None] | None = None
) -> object:
    """Read and validate one record; standard JSON decoding cannot be interrupted."""

    def check():
        if cancel is not None:
            cancel()

    path = _safe(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("History path is not a regular file")
    data = bytearray()
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("History path is not a regular file")
        while True:
            check()
            chunk = stream.read(min(limits.max_read_bytes, limits.max_bytes - len(data) + 1))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limits.max_bytes:
                raise ValueError("History JSON exceeds limit")
    check()

    def reject_constant(_):
        raise ValueError("Non-finite JSON")

    try:
        value = json.loads(data, object_pairs_hook=_unique_pairs, parse_constant=reject_constant)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds limit") from exc
    del data
    check()

    def children(items, child_depth):
        return ((child, child_depth) for child in items)

    stack = [iter(((value, 0),))]
    count = 0
    while stack:
        check()
        try:
            item, depth = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if depth > limits.max_depth:
            raise ValueError("JSON nesting exceeds limit")
        count += 1
        if isinstance(item, dict):
            count += len(item)
            stack.append(children(item.values(), depth + 1))
        elif isinstance(item, list):
            stack.append(children(item, depth + 1))
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Non-finite JSON")
        if count > limits.max_nodes:
            raise ValueError("JSON node count exceeds limit")
    return value
