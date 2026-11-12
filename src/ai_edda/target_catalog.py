import json
import math
import os
import tempfile
from pathlib import Path

from .arrangement_domain import TargetProfile
from .asset_catalog import _safe
from .domain import ObjectRef
from .project_store import _id, _json

_FLOAT_FIELDS = (
    "syncopation_budget",
    "ornament_budget",
    "target_hits_per_second",
    "window_sec",
    "double_event_fraction",
    "min_same_hand_interval_sec",
    "transition_complexity",
    "motif_consistency",
)


def _target(value):
    if isinstance(value, TargetProfile):
        value = value.model_dump(mode="python")
    if not isinstance(value, dict):
        raise ValueError("Invalid target")
    for field in _FLOAT_FIELDS:
        number = value.get(field)
        if number is not None:
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError("Invalid target number")
            try:
                finite = math.isfinite(number)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError("Invalid target number") from exc
            if not finite:
                raise ValueError("Invalid target number")
    record = TargetProfile.model_validate(value)
    if _id(record.id) != record.id:
        raise ValueError("Invalid target ID")
    return record


def _ref(value):
    if isinstance(value, ObjectRef):
        value = value.model_dump(mode="python")
    if (
        not isinstance(value, dict)
        or type(value.get("revision")) is not int
        or value["revision"] < 1
    ):
        raise ValueError("Invalid target reference")
    ref = ObjectRef.model_validate(value)
    if _id(ref.id) != ref.id:
        raise ValueError("Invalid target ID")
    return ref


def resolve_target(project_dir, ref):
    ref = _ref(ref)
    path = _safe(Path(project_dir) / "targets" / f"{ref.id}-{ref.revision}.json")
    record = _target(json.loads(path.read_bytes()))
    if record.id != ref.id or record.version != ref.revision:
        raise ValueError("Target reference mismatch")
    return record


def publish_target(project_dir, record):
    record = _target(record)
    directory = _safe(Path(project_dir) / "targets")
    directory.mkdir(exist_ok=True)
    final = _safe(directory / f"{record.id}-{record.version}.json")
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
                raise ValueError("Target ID conflict")
        return ObjectRef(id=record.id, revision=record.version)
    finally:
        os.unlink(name)
