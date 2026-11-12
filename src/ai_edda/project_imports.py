"""Publish a prepared import on the project store volume."""

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path

from pydantic import BaseModel

from .asset_catalog import _safe
from .import_receipts import ImportReceipt, build_import_receipt, resolve_import_receipt
from .native_capture import capture_inventory, hash_inventory
from .native_envelope import NativeEnvelope, serialize_native_envelope
from .native_history import MAX_HISTORY_JSON_BYTES
from .native_project import validate_native_project
from .project_store import _json


def _data(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _data(v) for k, v in value.items()}
    return value


def _receipt(context, result, job_id):
    context, result = _data(context), _data(result)
    if not isinstance(context, dict) or set(context) not in (
        {"request"},
        {"request", "prior_receipt"},
    ):
        raise ValueError("Invalid import context")
    if not isinstance(result, dict) or set(result) != {"receipt"}:
        raise ValueError("Invalid import result")
    receipt = ImportReceipt.model_validate(result["receipt"], strict=True)
    expected = build_import_receipt(
        context["request"],
        receipt.job_id,
        receipt.archive_sha256,
        receipt.project_id,
        receipt.public_result.revision,
        receipt.initial_head_sha256,
        receipt.initial_inventory_sha256,
    )
    if receipt != expected:
        raise ValueError("Import receipt mismatch")
    if "prior_receipt" in context:
        if receipt != ImportReceipt.model_validate(context["prior_receipt"], strict=True):
            raise ValueError("Prior receipt mismatch")
    elif receipt.job_id != job_id:
        raise ValueError("Import job mismatch")
    return receipt, "prior_receipt" in context


def _destination(store, receipt):
    destination = store._dir(receipt.project_id)
    if not destination.exists():
        return destination, False
    path = destination / "imports" / (receipt.request_id + ".json")
    if not path.exists():
        raise ValueError("PROJECT_EXISTS: " + receipt.project_id)
    if resolve_import_receipt(destination, receipt.request_id) != receipt:
        raise ValueError("Import receipt conflict")
    return destination, True


def _cleanup_owned_wrapper(store, wrapper, marker):
    root = store.root.resolve()
    wrapper = Path(wrapper)

    def is_reparse(path):
        try:
            info = path.lstat()
        except OSError:
            return False
        attrs = getattr(info, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return stat.S_ISLNK(info.st_mode) or bool(attrs & reparse)

    # Validate each component without resolving it, so a junction cannot redirect
    # marker reads or recursive deletion outside the store root.
    if wrapper.parent != root or is_reparse(wrapper) or not wrapper.is_dir():
        raise RuntimeError("Import staging ownership is ambiguous; recovery required")
    marker_path = wrapper / "marker.json"
    project = wrapper / "project"
    if is_reparse(marker_path) or is_reparse(project):
        raise RuntimeError("Import staging ownership is ambiguous; recovery required")
    try:
        actual = marker_path.read_bytes()
    except OSError as exc:
        raise RuntimeError("Import staging ownership is ambiguous; recovery required") from exc
    if actual != marker:
        raise RuntimeError("Import staging ownership conflict; recovery required")
    shutil.rmtree(wrapper)


def _marker(job_id, receipt):
    return _json(
        {
            "version": 1,
            "job_id": job_id,
            "project_id": receipt.project_id,
            "receipt_sha256": hashlib.sha256(_json(receipt.model_dump(mode="json"))).hexdigest(),
        }
    )


def _present(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def reconcile_project_import(store, job_id, context, result):
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Invalid job ID")
    receipt, _ = _receipt(context, result, job_id)
    wrapper = store.root.resolve() / (".import-" + job_id)
    destination = store._dir(receipt.project_id)
    found = False
    try:
        if _present(destination):
            path = destination / "imports" / (receipt.request_id + ".json")
            if _present(path):
                _, found = _destination(store, receipt)
            elif not destination.is_dir() or destination.is_symlink():
                raise RuntimeError("Import destination is ambiguous; recovery required")
    except Exception as exc:
        raise RuntimeError("Import destination is ambiguous; recovery required") from exc
    if _present(wrapper):
        _cleanup_owned_wrapper(store, wrapper, _marker(job_id, receipt))
    return (True, receipt.public_result.model_dump(mode="json")) if found else (False, None)


def publish_project_import(store, context, result, workspace):
    workspace = Path(workspace)
    if not workspace.is_absolute() or not re.fullmatch(r"[0-9a-f]{32}", workspace.name):
        raise ValueError("Invalid workspace")
    job_id = workspace.name
    receipt, prior = _receipt(context, result, job_id)
    destination, found = _destination(store, receipt)
    public = receipt.public_result.model_dump(mode="json")
    if found:
        return public
    if prior:
        raise ValueError("Prior import destination missing")
    source = _safe(workspace / "prepared" / "project")
    if resolve_import_receipt(source, receipt.request_id) != receipt:
        raise ValueError("Prepared receipt mismatch")
    with _safe(source / "project.json").open("rb") as stream:
        head = stream.read(MAX_HISTORY_JSON_BYTES + 1)
    if (
        len(head) > MAX_HISTORY_JSON_BYTES
        or hashlib.sha256(head).hexdigest() != receipt.initial_head_sha256
    ):
        raise ValueError("Prepared head mismatch")
    envelope = hash_inventory(capture_inventory(source, receipt.project_id, head))
    initial = NativeEnvelope(
        archive_version=1,
        schema_version=1,
        project_id=receipt.project_id,
        files=tuple(
            f for f in envelope.files if f.path != "imports/" + receipt.request_id + ".json"
        ),
    )
    if (
        hashlib.sha256(serialize_native_envelope(initial)).hexdigest()
        != receipt.initial_inventory_sha256
    ):
        raise ValueError("Prepared inventory mismatch")
    wrapper = store.root.resolve() / (".import-" + job_id)
    marker = _marker(job_id, receipt)
    wrapper.mkdir()
    owned = True
    try:
        with (wrapper / "marker.json").open("xb") as out:
            out.write(marker)
            out.flush()
            os.fsync(out.fileno())
        target = wrapper / "project"
        target.mkdir()
        for entry in envelope.files:
            dest = target / entry.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with _safe(source / entry.path).open("rb") as inp, dest.open("xb") as out:
                while block := inp.read(1024 * 1024):
                    size += len(block)
                    if size > entry.size:
                        raise ValueError("Source changed")
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if size != entry.size or digest.hexdigest() != entry.sha256:
                raise ValueError("Source changed")
        copied = hash_inventory(capture_inventory(target, receipt.project_id, head))
        if copied != envelope:
            raise ValueError("Copied inventory mismatch")
        snapshot = validate_native_project(target, receipt.project_id)
        if snapshot.project.revision != receipt.public_result.revision:
            raise ValueError("Project revision mismatch")
        with store._mutex:
            destination, found = _destination(store, receipt)
            if not found:
                os.rename(target, destination)
        return public
    finally:
        if owned:
            _cleanup_owned_wrapper(store, wrapper, marker)
