"""Bound the expansion of extractor NPZ files before NumPy loads them."""

import ast
import math
import stat
import zipfile
from dataclasses import dataclass

import numpy as np


@dataclass
class AnalysisBudget:
    limit: int
    used: int = 0


def preflight_analysis_npz(path, role, audio, budget, cancel=None):
    def check_cancel():
        if cancel is not None:
            cancel()

    def read(stream, size):
        check_cancel()
        return stream.read(size)

    names = {
        "beat": {"beat_logits", "downbeat_logits", "beat_times", "downbeat_times"},
        "rhythm": {"times", "onset_envelope", "onset_times", "rms", "activity"},
    }
    if role not in names or not math.isfinite(audio.duration_sec) or audio.duration_sec < 0:
        raise ValueError("Invalid analysis role or duration")
    max_count = math.ceil((audio.duration_sec + 0.021) * 50) + 1
    total = 0
    try:
        check_cancel()
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            expected = {name + ".npy" for name in names[role]}
            if (
                len(entries) != len(expected)
                or {e.filename for e in entries} != expected
                or any(e.orig_filename != e.filename for e in entries)
            ):
                raise ValueError("Invalid analysis members")
            for entry in entries:
                check_cancel()
                mode = entry.external_attr >> 16
                if (
                    entry.is_dir()
                    or entry.flag_bits & 1
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                ):
                    raise ValueError("Invalid analysis member")
                total += entry.file_size
                if total + budget.used > budget.limit:
                    raise ValueError("Analysis budget exceeded")
                check_cancel()
                with archive.open(entry) as stream:
                    prefix = read(stream, 8)
                    if (
                        len(prefix) != 8
                        or prefix[:6] != b"\x93NUMPY"
                        or prefix[6:8] not in (b"\x01\x00", b"\x02\x00", b"\x03\x00")
                    ):
                        raise ValueError("Invalid NPY version")
                    size_len = 2 if prefix[6] == 1 else 4
                    raw = read(stream, size_len)
                    if len(raw) != size_len:
                        raise ValueError("Truncated NPY header")
                    header_len = int.from_bytes(raw, "little")
                    if header_len > 65536:
                        raise ValueError("NPY header too large")
                    header = read(stream, header_len)
                    if len(header) != header_len:
                        raise ValueError("Truncated NPY header")
                    meta = ast.literal_eval(
                        header.decode("utf-8" if prefix[6] == 3 else "latin1").strip()
                    )
                    if (
                        not isinstance(meta, dict)
                        or set(meta) != {"descr", "fortran_order", "shape"}
                        or type(meta["fortran_order"]) is not bool
                    ):
                        raise ValueError("Invalid NPY header")
                    shape = meta["shape"]
                    if not isinstance(meta["descr"], str):
                        raise ValueError("Invalid dtype descriptor")
                    dtype = np.dtype(meta["descr"])
                    if (
                        not isinstance(shape, tuple)
                        or len(shape) != 1
                        or type(shape[0]) is not int
                        or shape[0] < 0
                        or shape[0] > max_count
                        or dtype.kind not in "iuf"
                        or dtype.fields is not None
                    ):
                        raise ValueError("Invalid analysis array")
                    remaining = shape[0] * dtype.itemsize
                    if entry.file_size != 8 + size_len + header_len + remaining:
                        raise ValueError("Invalid NPY payload size")
                    while remaining:
                        chunk = read(stream, min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("Truncated NPY payload")
                        remaining -= len(chunk)
                    if read(stream, 1):
                        raise ValueError("Trailing NPY payload")
    except (
        zipfile.BadZipFile,
        EOFError,
        SyntaxError,
        TypeError,
        UnicodeError,
        OverflowError,
    ) as exc:
        raise ValueError("Invalid analysis archive") from exc
    budget.used += total
    return total
