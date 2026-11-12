"""Streaming transport for native project archives; semantic validation is separate."""

import hashlib
import os
import shutil
import stat
import zipfile
from pathlib import Path

from .asset_catalog import _safe
from .native_envelope import (
    MAX_DECLARED_BYTES,
    MAX_ENVELOPE_BYTES,
    MAX_FILES,
    parse_native_envelope,
    serialize_native_envelope,
)
from .safe_zip import extract_zip

_BLOCK = 1024 * 1024


def _check_cancel(cancel):
    if cancel:
        cancel()


def write_native_zip(destination, inventory, envelope, cancel=None):
    """Write a captured inventory to a new archive and verify the written bytes."""
    _check_cancel(cancel)
    data = serialize_native_envelope(envelope)
    envelope = parse_native_envelope(data)
    expected = {entry.path: entry for entry in envelope.files}
    captured = {rel: (size, mtime, inode) for rel, size, mtime, inode in inventory.files}
    if envelope.project_id != inventory.project_id or set(expected) != set(captured) | {
        "project.json"
    }:
        raise ValueError("Envelope does not match capture")
    head = expected["project.json"]
    if (
        head.size != len(inventory.head)
        or head.sha256 != hashlib.sha256(inventory.head).hexdigest()
    ):
        raise ValueError("Envelope does not match captured head")
    destination = Path(destination).absolute()
    for parent in (destination.parent, *destination.parent.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Reparse destination")
    owned = False
    try:
        with destination.open("xb") as output:
            owned = True
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as archive:
                for name, value in (
                    ("archive.json", data),
                    ("project/project.json", inventory.head),
                ):
                    with archive.open(name, "w", force_zip64=True) as target:
                        for offset in range(0, len(value), _BLOCK):
                            _check_cancel(cancel)
                            target.write(value[offset : offset + _BLOCK])
                for rel, metadata in captured.items():
                    _check_cancel(cancel)
                    path = _safe(inventory.root / rel)
                    before = path.lstat()
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or (before.st_size, before.st_mtime_ns, before.st_ino) != metadata
                    ):
                        raise ValueError("Captured file changed")
                    digest = hashlib.sha256()
                    count = 0
                    with (
                        path.open("rb") as source,
                        archive.open("project/" + rel, "w", force_zip64=True) as target,
                    ):
                        while block := source.read(_BLOCK):
                            _check_cancel(cancel)
                            count += len(block)
                            if count > metadata[0]:
                                raise ValueError("Captured file changed")
                            digest.update(block)
                            target.write(block)
                        after = os.fstat(source.fileno())
                    _safe(path)
                    if (
                        (after.st_size, after.st_mtime_ns, after.st_ino) != metadata
                        or count != expected[rel].size
                        or digest.hexdigest() != expected[rel].sha256
                    ):
                        raise ValueError("Captured file changed")
        with zipfile.ZipFile(destination) as archive:
            if set(archive.namelist()) != {"archive.json"} | {"project/" + p for p in expected}:
                raise ValueError("Written inventory mismatch")
            for entry in envelope.files:
                digest = hashlib.sha256()
                count = 0
                with archive.open("project/" + entry.path) as source:
                    while block := source.read(_BLOCK):
                        _check_cancel(cancel)
                        count += len(block)
                        digest.update(block)
                if count != entry.size or digest.hexdigest() != entry.sha256:
                    raise ValueError("Written file mismatch")
    except BaseException:
        if owned:
            destination.unlink(missing_ok=True)
        raise


def read_native_zip(source, destination, cancel=None):
    """Extract and verify a native archive into a newly owned directory."""
    destination = Path(destination)
    _check_cancel(cancel)
    files = extract_zip(
        source,
        destination,
        max_entries=MAX_FILES + 1,
        max_bytes=MAX_DECLARED_BYTES + MAX_ENVELOPE_BYTES,
        cancel=cancel,
    )
    try:
        if "archive.json" not in files:
            raise ValueError("Missing archive envelope")
        with (destination / "archive.json").open("rb") as stream:
            data = bytearray()
            while len(data) <= MAX_ENVELOPE_BYTES:
                _check_cancel(cancel)
                block = stream.read(min(_BLOCK, MAX_ENVELOPE_BYTES + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            envelope = parse_native_envelope(bytes(data))
        if set(files) != {"archive.json"} | {
            "project/" + e.path for e in envelope.files
        } or "project.json" not in {e.path for e in envelope.files}:
            raise ValueError("Archive inventory mismatch")
        allowed_dirs = {"project"}
        for file in files:
            parts = file.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                allowed_dirs.add("/".join(parts[:i]))
        actual_dirs = {
            p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_dir()
        }
        if actual_dirs - allowed_dirs:
            raise ValueError("Archive inventory mismatch")
        for entry in envelope.files:
            digest = hashlib.sha256()
            count = 0
            with (destination / "project" / entry.path).open("rb") as stream:
                while block := stream.read(_BLOCK):
                    _check_cancel(cancel)
                    count += len(block)
                    if count > entry.size:
                        raise ValueError("Archive file mismatch")
                    digest.update(block)
            if count != entry.size or digest.hexdigest() != entry.sha256:
                raise ValueError("Archive file mismatch")
        return envelope, destination / "project"
    except BaseException:
        shutil.rmtree(destination)
        raise
