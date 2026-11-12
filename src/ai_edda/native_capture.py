import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .asset_catalog import _safe
from .native_envelope import _PATTERNS, MAX_DECLARED_BYTES, MAX_FILES, NativeEnvelope, NativeFile
from .safe_zip import validate_member_paths

_BLOCK = 1024 * 1024
_CACHE = re.compile(r"waveforms/[0-9a-f]{64}/(?:manifest\.json|peaks\.npz)")


@dataclass(frozen=True)
class CaptureInventory:
    root: Path
    project_id: str
    head: bytes
    files: tuple


def capture_inventory(root, project_id, head):
    """Capture file metadata while the caller holds the project lock."""
    if not isinstance(head, bytes):
        raise ValueError("Head must be bytes")
    root = _safe(root)
    if not root.is_dir():
        raise ValueError("Invalid project directory")
    files = []
    total = len(head)
    try:

        def raise_walk_error(error):
            raise error

        for directory, dirs, names in os.walk(root, followlinks=False, onerror=raise_walk_error):
            for name in dirs + names:
                path = Path(directory) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Unsafe path")
                if stat.S_ISDIR(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Unsupported file")
                rel = path.relative_to(root).as_posix()
                if _CACHE.fullmatch(rel):
                    continue
                if not any(re.fullmatch(pattern, rel) for pattern in _PATTERNS):
                    raise ValueError("Unknown native path")
                if rel == "project.json":
                    continue
                files.append((rel, info.st_size, info.st_mtime_ns, info.st_ino))
                total += info.st_size
                if len(files) + 1 > MAX_FILES or total > MAX_DECLARED_BYTES:
                    raise ValueError("Capture exceeds limit")
        if not (root / "project.json").is_file():
            raise ValueError("Missing project head")
        validate_member_paths([(item[0], False) for item in files] + [("project.json", False)])
        if total > MAX_DECLARED_BYTES:
            raise ValueError("Capture exceeds limit")
        return CaptureInventory(root, project_id, head, tuple(sorted(files)))
    except OSError as exc:
        raise ValueError("Cannot inventory project") from exc


def hash_inventory(inventory):
    """Hash captured immutable files outside the project lock."""
    entries = [
        NativeFile(
            path="project.json",
            size=len(inventory.head),
            sha256=hashlib.sha256(inventory.head).hexdigest(),
        )
    ]
    try:
        for rel, size, mtime, inode in inventory.files:
            path = _safe(inventory.root / rel)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or (info.st_size, info.st_mtime_ns, info.st_ino) != (
                size,
                mtime,
                inode,
            ):
                raise ValueError("Captured file changed")
            digest = hashlib.sha256()
            count = 0
            with path.open("rb") as stream:
                while block := stream.read(_BLOCK):
                    count += len(block)
                    if count > size:
                        raise ValueError("Captured file changed")
                    digest.update(block)
                after = os.fstat(stream.fileno())
            _safe(path)
            if count != size or (after.st_size, after.st_mtime_ns, after.st_ino) != (
                size,
                mtime,
                inode,
            ):
                raise ValueError("Captured file changed")
            entries.append(NativeFile(path=rel, size=size, sha256=digest.hexdigest()))
        return NativeEnvelope(
            archive_version=1,
            schema_version=1,
            project_id=inventory.project_id,
            files=tuple(sorted(entries, key=lambda e: e.path)),
        )
    except OSError as exc:
        raise ValueError("Cannot hash captured file") from exc
