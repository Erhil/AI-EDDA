"""Prepare and verify an archive from a pinned project capture."""

import hashlib
import shutil
from pathlib import Path

from .native_capture import CaptureInventory, hash_inventory
from .native_envelope import serialize_native_envelope
from .native_project import validate_native_project
from .native_zip import read_native_zip, write_native_zip


def native_export_worker(payload, progress, cancel):
    def check():
        if cancel.is_set():
            raise RuntimeError("Native export cancelled")

    def report(value):
        check()
        progress(value)
        check()

    check()
    inventory = payload["inventory"]
    revision = payload["project_revision"]
    workspace = Path(payload["workspace"])
    if (
        not isinstance(inventory, CaptureInventory)
        or type(revision) is not int
        or revision < 0
        or not workspace.is_absolute()
    ):
        raise ValueError("Invalid native export payload")
    source = Path(inventory.root).resolve()
    target = workspace.resolve()
    if target == source or source in target.parents:
        raise ValueError("Native export workspace must be outside the source")
    prepared = workspace / "prepared"
    verification = workspace / "verification"
    owned_prepared = owned_verification = False
    try:
        check()
        prepared.mkdir()
        owned_prepared = True
        check()
        envelope = hash_inventory(inventory)
        check()
        report(0.2)
        archive = prepared / "backup.zip"
        write_native_zip(archive, inventory, envelope, cancel=check)
        check()
        report(0.6)
        # Extraction creates its destination exclusively.
        restored_envelope, root = read_native_zip(archive, verification, cancel=check)
        owned_verification = True
        check()
        snapshot = validate_native_project(root, inventory.project_id, cancel=check)
        check()
        if snapshot.project.revision != revision or restored_envelope != envelope:
            raise ValueError("Restored project does not match capture")
        report(0.9)
        digest = hashlib.sha256()
        size = 0
        with archive.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                check()
                size += len(block)
                digest.update(block)
        check()
        result = {
            "project_id": inventory.project_id,
            "project_revision": revision,
            "archive_sha256": digest.hexdigest(),
            "archive_size": size,
            "envelope_sha256": hashlib.sha256(serialize_native_envelope(envelope)).hexdigest(),
            "head_sha256": hashlib.sha256(inventory.head).hexdigest(),
        }
        report(1.0)
        return result
    except BaseException:
        if owned_verification:
            shutil.rmtree(verification, ignore_errors=True)
        if owned_prepared:
            shutil.rmtree(prepared, ignore_errors=True)
        raise
