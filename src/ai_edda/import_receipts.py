import hashlib
import os
import re
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .asset_catalog import _safe
from .native_history import MAX_NODES, MAX_TOTAL_BYTES, _read, _uuid
from .project_store import _json


class ImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: str = Field(strict=True)
    revision: int = Field(strict=True, ge=0)
    archive_kind: Literal["native", "game"]

    @field_validator("project_id")
    @classmethod
    def valid_id(cls, value):
        _uuid(value)
        return value


class ImportReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1]
    request_id: str = Field(strict=True)
    job_id: str = Field(strict=True, min_length=1)
    request_sha256: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    archive_sha256: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    initial_head_sha256: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    initial_inventory_sha256: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    project_id: str = Field(strict=True)
    public_result: ImportResult

    @field_validator("version", mode="before")
    @classmethod
    def valid_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Invalid version")
        return value

    @field_validator("request_id", "project_id")
    @classmethod
    def valid_id(cls, value):
        _uuid(value)
        return value

    @model_validator(mode="after")
    def bind_project(self):
        if self.project_id != self.public_result.project_id:
            raise ValueError("Project mismatch")
        return self


def build_import_receipt(
    request_context,
    job_id,
    archive_sha256,
    project_id,
    revision,
    initial_head_sha256,
    initial_inventory_sha256,
):
    if not isinstance(request_context, dict) or set(request_context) != {
        "request_id",
        "archive_kind",
        "file_name",
        "file_size",
    }:
        raise ValueError("Invalid request context")
    _uuid(request_context["request_id"])
    name = request_context["file_name"]
    size = request_context["file_size"]
    if (
        not isinstance(name, str)
        or not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or PureWindowsPath(name).drive
        or any(ord(c) < 32 for c in name)
        or name.strip() != name
        or type(size) is not int
        or not 0 < size <= 1024**3
    ):
        raise ValueError("Invalid request metadata")
    result = ImportResult(
        project_id=project_id, revision=revision, archive_kind=request_context["archive_kind"]
    )
    return ImportReceipt(
        version=1,
        request_id=request_context["request_id"],
        job_id=job_id,
        request_sha256=hashlib.sha256(_json(request_context)).hexdigest(),
        archive_sha256=archive_sha256,
        initial_head_sha256=initial_head_sha256,
        initial_inventory_sha256=initial_inventory_sha256,
        project_id=project_id,
        public_result=result,
    )


def publish_import_receipt(project_dir, record):
    if isinstance(record, BaseModel):
        record = record.model_dump(mode="python")
    if isinstance(record, dict) and isinstance(record.get("public_result"), BaseModel):
        record = {**record, "public_result": record["public_result"].model_dump(mode="python")}
    record = ImportReceipt.model_validate(record)
    directory = _safe(Path(project_dir) / "imports")
    directory.mkdir(exist_ok=True)
    final = _safe(directory / (record.request_id + ".json"))
    payload = _json(record.model_dump(mode="json"))
    fd, name = tempfile.mkstemp(prefix=".staging-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, final)
        except FileExistsError:
            if _safe(final).read_bytes() != payload:
                raise ValueError("Import receipt conflict")
        return record
    finally:
        os.unlink(name)


def _resolve(path, request_id, budget):
    _uuid(request_id)
    path = _safe(path)
    if not path.is_file():
        raise ValueError("Import receipt is not a regular file")
    record = ImportReceipt.model_validate(_read(path, budget))
    if record.request_id != request_id:
        raise ValueError("Import receipt filename mismatch")
    return record


def resolve_import_receipt(project_dir, request_id):
    _uuid(request_id)
    return _resolve(
        Path(project_dir) / "imports" / (request_id + ".json"),
        request_id,
        [MAX_TOTAL_BYTES, MAX_NODES],
    )


def validate_native_import_receipts(root, history, cancel=None):
    directory = _safe(Path(root) / "imports")
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise ValueError("Invalid imports directory")
    budget = [MAX_TOTAL_BYTES, MAX_NODES]
    revisions = {snapshot.project.revision for snapshot in history.heads}
    result = {}
    for path in directory.iterdir():
        if cancel is not None:
            cancel()
        match = re.fullmatch(
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json", path.name
        )
        if not match:
            raise ValueError("Invalid import receipt filename")
        record = _resolve(path, match[1], budget)
        if (
            record.project_id != history.project_id
            or record.public_result.revision not in revisions
        ):
            raise ValueError("Import receipt history mismatch")
        result[record.request_id] = record
    return result
