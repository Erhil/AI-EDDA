"""Prepare an import in a coordinator-owned workspace."""

import hashlib
import re
import shutil
from pathlib import Path

from .game_archive import prepare_game_archive
from .import_receipts import ImportReceipt, build_import_receipt, publish_import_receipt
from .native_capture import capture_inventory, hash_inventory
from .native_envelope import serialize_native_envelope
from .native_project import validate_native_project
from .native_zip import read_native_zip
from .project_store import _json


def import_worker(payload, progress, cancel):
    def check():
        if cancel.is_set():
            raise RuntimeError("Import cancelled")

    workspace = Path(payload["workspace"])
    job_id = payload["job_id"]
    if (
        not workspace.is_absolute()
        or not isinstance(job_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", job_id)
    ):
        raise ValueError("Invalid workspace or job ID")
    context = payload["request_context"]
    # Reuse the receipt builder's strict request metadata validation.
    build_import_receipt(
        context, job_id, "0" * 64, "00000000-0000-0000-0000-000000000000", 0, "0" * 64, "0" * 64
    )
    check()
    digest = hashlib.sha256()
    size = 0
    declared_size = context["file_size"]
    with (workspace / "source.zip").open("rb") as stream:
        while block := stream.read(1024 * 1024):
            check()
            size += len(block)
            if size > declared_size or size > 1024**3:
                raise ValueError("Archive size mismatch")
            digest.update(block)
    check()
    if size != context["file_size"]:
        raise ValueError("Archive size mismatch")
    archive_hash = digest.hexdigest()
    if "prior_receipt" in payload:
        original = payload["prior_receipt"]
        receipt = ImportReceipt.model_validate(original, strict=True)
        if (
            receipt.request_id != context["request_id"]
            or receipt.request_sha256 != hashlib.sha256(_json(context)).hexdigest()
            or receipt.archive_sha256 != archive_hash
        ):
            raise ValueError("REQUEST_ID_CONFLICT")
        return {"receipt": original}
    prepared = workspace / "prepared"
    owned = False
    try:
        if context["archive_kind"] == "native":
            envelope, root = read_native_zip(workspace / "source.zip", prepared, cancel=check)
            owned = True
            snapshot = validate_native_project(root, envelope.project_id, cancel=check)
        else:
            prepared.mkdir()
            owned = True
            root = prepared / "project"
            snapshot = prepare_game_archive(workspace / "source.zip", root, cancel=check)
            snapshot = validate_native_project(root, snapshot.project.id, cancel=check)
        check()
        if (root / "imports" / (context["request_id"] + ".json")).exists():
            raise ValueError("REQUEST_ID_CONFLICT")
        head = (root / "project.json").read_bytes()
        inventory = hash_inventory(capture_inventory(root, snapshot.project.id, head))
        check()
        receipt = build_import_receipt(
            context,
            job_id,
            archive_hash,
            snapshot.project.id,
            snapshot.project.revision,
            hashlib.sha256(head).hexdigest(),
            hashlib.sha256(serialize_native_envelope(inventory)).hexdigest(),
        )
        publish_import_receipt(root, receipt)
        validate_native_project(root, snapshot.project.id, cancel=check)
        check()
        progress(1.0)
        check()
        return {"receipt": receipt.model_dump(mode="json")}
    except BaseException:
        if owned:
            shutil.rmtree(prepared, ignore_errors=True)
        raise
