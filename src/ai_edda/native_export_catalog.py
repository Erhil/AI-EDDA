"""Validate native backup publication records without extracting archives."""

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .asset_catalog import _safe
from .native_envelope import MAX_DECLARED_BYTES, MAX_ENVELOPE_BYTES
from .native_history import MAX_NODES, _integer, _read, _uuid
from .project_store import _json

MAX_NATIVE_BACKUP_BYTES = 1024**3


class NativeExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str
    project_revision: int = Field(ge=0)
    archive_sha256: str
    archive_size: int = Field(gt=0, le=MAX_DECLARED_BYTES + MAX_ENVELOPE_BYTES)
    envelope_sha256: str
    head_sha256: str

    @field_validator("project_id")
    @classmethod
    def valid_id(cls, value):
        _uuid(value)
        return value

    @field_validator("archive_sha256", "envelope_sha256", "head_sha256")
    @classmethod
    def valid_hash(cls, value):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Invalid digest")
        return value


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _result(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid result")
    return NativeExportResult.model_validate(value).model_dump(mode="json")


def _public(export_id, result):
    return {
        "project_id": result["project_id"],
        "export_id": export_id,
        "kind": "native",
        "project_revision": result["project_revision"],
        "file_name": "backup.zip",
    }


def make_native_export_record(export_id, job_id, context, result):
    _uuid(export_id)
    if not isinstance(job_id, str) or not job_id or not isinstance(context, dict):
        raise ValueError("Invalid publication request")
    result = _result(result)
    _uuid(context.get("project_id"))
    _uuid(context.get("export_id"))
    _integer(context.get("project_revision"))
    if (context["project_id"], context["export_id"], context["project_revision"]) != (
        result["project_id"],
        export_id,
        result["project_revision"],
    ):
        raise ValueError("Publication identity mismatch")
    return {
        "version": 1,
        "export_id": export_id,
        "job_id": job_id,
        "context_sha256": _hash(context),
        "result_sha256": _hash(result),
        "result": result,
        "public_result": _public(export_id, result),
    }


def _regular(path):
    path = _safe(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Expected regular file")
    return path


def resolve_native_export(config_root, project_id, export_id):
    _uuid(project_id)
    _uuid(export_id)
    root = _safe(Path(config_root) / "exports" / project_id / export_id)
    if not root.is_dir() or {p.name for p in root.iterdir()} != {"backup.zip", "publication.json"}:
        raise ValueError("Invalid export directory")
    record = _read(_regular(root / "publication.json"), [64 * 1024, MAX_NODES])
    keys = {
        "version",
        "export_id",
        "job_id",
        "context_sha256",
        "result_sha256",
        "result",
        "public_result",
    }
    if (
        not isinstance(record, dict)
        or set(record) != keys
        or type(record["version"]) is not int
        or record["version"] != 1
    ):
        raise ValueError("Invalid publication")
    _uuid(record["export_id"])
    if (
        record["export_id"] != export_id
        or not isinstance(record["job_id"], str)
        or not record["job_id"]
    ):
        raise ValueError("Invalid publication identity")
    for key in ("context_sha256", "result_sha256"):
        if not isinstance(record[key], str) or not re.fullmatch(r"[0-9a-f]{64}", record[key]):
            raise ValueError("Invalid digest")
    result = _result(record["result"])
    if (
        result["project_id"] != project_id
        or _hash(result) != record["result_sha256"]
        or _json(record["public_result"]) != _json(_public(export_id, result))
    ):
        raise ValueError("Publication mismatch")
    archive = _regular(root / "backup.zip")
    if archive.stat().st_size != result["archive_size"]:
        raise ValueError("Archive size mismatch")
    digest = hashlib.sha256()
    size = 0
    with archive.open("rb") as stream:
        while True:
            block = stream.read(min(1024 * 1024, result["archive_size"] - size + 1))
            if not block:
                break
            size += len(block)
            if size > result["archive_size"]:
                raise ValueError("Archive size mismatch")
            digest.update(block)
    if size != result["archive_size"] or digest.hexdigest() != result["archive_sha256"]:
        raise ValueError("Archive mismatch")
    return record


def _present(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _reparse(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _cleanup_native_wrapper(parent, wrapper, marker):
    if wrapper.parent != parent or _reparse(wrapper) or not wrapper.is_dir():
        raise RuntimeError("Export staging ownership is ambiguous")
    marker_path = wrapper / "marker.json"
    if _reparse(marker_path) or _reparse(wrapper / "export"):
        raise RuntimeError("Export staging ownership is ambiguous")
    try:
        with _regular(marker_path).open("rb") as stream:
            actual = stream.read(len(marker) + 1)
    except OSError as exc:
        raise RuntimeError("Export staging ownership is ambiguous") from exc
    if actual != marker:
        raise RuntimeError("Export staging ownership conflict")
    shutil.rmtree(wrapper)


def reconcile_native_export(config_root, export_id, job_id, context, result):
    record = make_native_export_record(export_id, job_id, context, result)
    project_id = record["result"]["project_id"]
    parent = _safe(Path(config_root) / "exports" / project_id)
    destination = parent / export_id
    name = hashlib.sha256(_json([project_id, export_id, job_id])).hexdigest()
    wrapper = parent / (".native-export-" + name)
    marker = _json(
        {
            "version": 1,
            "project_id": project_id,
            "export_id": export_id,
            "job_id": job_id,
            "record_sha256": _hash(record),
        }
    )
    found = _present(destination)
    if found:
        if resolve_native_export(config_root, project_id, export_id) != record:
            raise ValueError("Export publication conflict")
        size = _regular(destination / "backup.zip").stat().st_size
        if size > MAX_NATIVE_BACKUP_BYTES:
            raise ValueError(f"Archive size {size} exceeds allowed {MAX_NATIVE_BACKUP_BYTES} bytes")
    if _present(wrapper):
        _cleanup_native_wrapper(parent, wrapper, marker)
    return (True, record["public_result"]) if found else (False, None)


def publish_native_export(config_root, export_id, job_id, context, result, prepared_dir):
    record = make_native_export_record(export_id, job_id, context, result)
    project_id = record["result"]["project_id"]
    parent = _safe(Path(config_root) / "exports" / project_id)
    destination = parent / export_id
    if _present(destination):
        if resolve_native_export(config_root, project_id, export_id) != record:
            raise ValueError("Export publication conflict")
        size = record["result"]["archive_size"]
        if size > MAX_NATIVE_BACKUP_BYTES:
            raise ValueError(f"Archive size {size} exceeds allowed {MAX_NATIVE_BACKUP_BYTES} bytes")
        return record["public_result"]
    prepared = _safe(Path(prepared_dir))
    if not prepared.is_dir() or {p.name for p in prepared.iterdir()} != {"backup.zip"}:
        raise ValueError("Invalid prepared export directory")
    source = _regular(prepared / "backup.zip")
    size = source.stat().st_size
    if size > MAX_NATIVE_BACKUP_BYTES:
        raise ValueError(f"Archive size {size} exceeds allowed {MAX_NATIVE_BACKUP_BYTES} bytes")
    if size != record["result"]["archive_size"]:
        raise ValueError("Archive size mismatch")
    parent.mkdir(parents=True, exist_ok=True)
    parent = _safe(parent)
    name = hashlib.sha256(_json([project_id, export_id, job_id])).hexdigest()
    wrapper = parent / (".native-export-" + name)
    marker = _json(
        {
            "version": 1,
            "project_id": project_id,
            "export_id": export_id,
            "job_id": job_id,
            "record_sha256": _hash(record),
        }
    )
    wrapper.mkdir()
    marked = False
    try:
        with (wrapper / "marker.json").open("xb") as out:
            out.write(marker)
            out.flush()
            os.fsync(out.fileno())
        marked = True
        payload = wrapper / "export"
        payload.mkdir()
        digest = hashlib.sha256()
        copied = 0
        with source.open("rb") as inp, (payload / "backup.zip").open("xb") as out:
            while block := inp.read(1024 * 1024):
                copied += len(block)
                if copied > size or copied > MAX_NATIVE_BACKUP_BYTES:
                    raise ValueError(
                        f"Archive size {copied} exceeds allowed {MAX_NATIVE_BACKUP_BYTES} bytes"
                    )
                digest.update(block)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if copied != size or digest.hexdigest() != record["result"]["archive_sha256"]:
            raise ValueError("Archive mismatch")
        with (payload / "publication.json").open("xb") as out:
            out.write(_json(record))
            out.flush()
            os.fsync(out.fileno())
        # Verify the staged payload using the same checks as published exports.
        if _read(_regular(payload / "publication.json"), [64 * 1024, MAX_NODES]) != record:
            raise ValueError("Publication mismatch")
        staged_archive = _regular(payload / "backup.zip")
        staged_size = staged_archive.stat().st_size
        if staged_size != size or staged_size > MAX_NATIVE_BACKUP_BYTES:
            raise ValueError(
                f"Archive size {staged_size} exceeds allowed {MAX_NATIVE_BACKUP_BYTES} bytes"
            )
        staged_digest = hashlib.sha256()
        with staged_archive.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                staged_digest.update(block)
        if staged_digest.hexdigest() != record["result"]["archive_sha256"]:
            raise ValueError("Archive mismatch")
        if _present(destination):
            if resolve_native_export(config_root, project_id, export_id) != record:
                raise ValueError("Export publication conflict")
        else:
            os.rename(payload, destination)
        return record["public_result"]
    finally:
        if marked:
            _cleanup_native_wrapper(parent, wrapper, marker)
