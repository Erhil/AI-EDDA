import json
import math
import os
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .arrangement_domain import MusicalAnchor
from .asset_catalog import _safe
from .domain import AudioAsset, Model, ObjectRef
from .project_store import _id, _json


class PlanInputs(Model):
    id: str
    revision: Literal[0] = 0
    audio_asset_id: str = Field(min_length=1, strict=True)
    audio_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$", strict=True)
    analysis: ObjectRef
    anchors: tuple[MusicalAnchor, ...] = ()

    @model_validator(mode="after")
    def validate_ids(self):
        if type(self.revision) is not int or self.revision != 0:
            raise ValueError("Invalid plan inputs revision")
        if _id(self.id) != self.id:
            raise ValueError("Invalid record ID")
        ids = [_id(anchor.id) for anchor in self.anchors]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate anchor ID")
        return self


def _identity(data):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, _json(data).decode("utf-8")))


def _verify(record):
    data = record.model_dump(mode="json", exclude={"id"})
    if record.id != _identity(data):
        raise ValueError("Plan inputs identity mismatch")
    return record


def remove_chart_scope(record, deleted_chart_id, remaining_chart_ids):
    """Remove one chart from scoped anchors, retaining global anchors."""
    record = _verify(PlanInputs.model_validate(record))
    remaining_chart_ids = set(remaining_chart_ids)
    anchors = []
    changed = False
    for anchor in record.anchors:
        if not anchor.target_scope:
            anchors.append(anchor)
            continue
        if not set(anchor.target_scope) <= remaining_chart_ids | {deleted_chart_id}:
            raise ValueError("Invalid anchor scope")
        scope = tuple(chart_id for chart_id in anchor.target_scope if chart_id != deleted_chart_id)
        if not set(scope) <= remaining_chart_ids:
            raise ValueError("Invalid anchor scope")
        if scope != anchor.target_scope:
            changed = True
            if scope:
                anchors.append(anchor.model_copy(update={"target_scope": scope}))
        else:
            anchors.append(anchor)
    if not changed:
        return record
    data = record.model_dump(mode="json", exclude={"id", "anchors"})
    data["anchors"] = [anchor.model_dump(mode="json") for anchor in anchors]
    updated = PlanInputs(id=_identity(data), **data)
    return _verify(updated)


def build_plan_inputs(audio_asset_id, audio, analysis_ref, anchors, chart_ids):
    audio = AudioAsset.model_validate(audio)
    if isinstance(analysis_ref, dict) and "revision" in analysis_ref:
        revision = analysis_ref["revision"]
        if type(revision) is not int or revision < 0:
            raise ValueError("Invalid analysis revision")
    analysis_ref = ObjectRef.model_validate(analysis_ref)
    anchors = tuple(a if isinstance(a, MusicalAnchor) else _validate_anchor(a) for a in anchors)
    chart_ids = set(chart_ids)
    for anchor in anchors:
        if not math.isfinite(anchor.time_sec) or anchor.time_sec >= audio.duration_sec:
            raise ValueError("Anchor outside audio")
        if not math.isfinite(anchor.tolerance_sec) or not set(anchor.target_scope) <= chart_ids:
            raise ValueError("Invalid anchor tolerance or scope")
    data = {
        "revision": 0,
        "audio_asset_id": audio_asset_id,
        "audio_sha256": audio.sha256,
        "analysis": analysis_ref.model_dump(mode="json"),
        "anchors": [a.model_dump(mode="json") for a in anchors],
    }
    return PlanInputs(id=_identity(data), **data)


def _validate_anchor(anchor):
    if not isinstance(anchor, dict) or "id" not in anchor:
        raise ValueError("Anchor ID is required")
    for key in ("time_sec", "tolerance_sec"):
        if key in anchor and (
            isinstance(anchor[key], bool) or not isinstance(anchor[key], (int, float))
        ):
            raise ValueError(f"Invalid anchor {key}")
    try:
        parsed = uuid.UUID(anchor["id"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Invalid anchor ID") from exc
    if str(parsed) != anchor["id"]:
        raise ValueError("Invalid anchor ID")
    return MusicalAnchor.model_validate(anchor)


def resolve_plan_inputs(project_dir, ref):
    if isinstance(ref, dict) and "revision" in ref:
        revision = ref["revision"]
        if type(revision) is not int or revision < 0:
            raise ValueError("Invalid plan inputs reference revision")
    ref = ObjectRef.model_validate(ref)
    record_id = _id(ref.id)
    if ref.id != record_id or ref.revision != 0:
        raise ValueError("Invalid plan inputs reference")
    path = _safe(Path(project_dir) / "plan-inputs" / (record_id + ".json"))
    raw = json.loads(path.read_bytes())
    if (
        isinstance(raw, dict)
        and "revision" in raw
        and (type(raw["revision"]) is not int or raw["revision"] != 0)
    ):
        raise ValueError("Invalid plan inputs revision")
    if (
        isinstance(raw, dict)
        and "analysis" in raw
        and isinstance(raw["analysis"], dict)
        and "revision" in raw["analysis"]
    ):
        revision = raw["analysis"]["revision"]
        if type(revision) is not int or revision < 0:
            raise ValueError("Invalid analysis revision")
    for anchor in raw.get("anchors", []):
        if isinstance(anchor, dict):
            for key in ("time_sec", "tolerance_sec"):
                if key in anchor and (
                    isinstance(anchor[key], bool) or not isinstance(anchor[key], (int, float))
                ):
                    raise ValueError(f"Invalid anchor {key}")
    record = PlanInputs.model_validate(raw)
    if record.id != record_id or record.revision != ref.revision:
        raise ValueError("Plan inputs reference mismatch")
    return _verify(record)


def publish_plan_inputs(project_dir, record):
    if isinstance(record, dict):
        revision = record.get("revision", 0)
        if type(revision) is not int or revision != 0:
            raise ValueError("Invalid plan inputs revision")
        analysis = record.get("analysis")
        if isinstance(analysis, dict) and "revision" in analysis:
            analysis_revision = analysis["revision"]
            if type(analysis_revision) is not int or analysis_revision < 0:
                raise ValueError("Invalid analysis revision")
        for anchor in record.get("anchors", []):
            if isinstance(anchor, dict):
                _validate_anchor(anchor)
    record = _verify(PlanInputs.model_validate(record))
    directory = _safe(Path(project_dir) / "plan-inputs")
    directory.mkdir(exist_ok=True)
    final = _safe(directory / (record.id + ".json"))
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
                raise ValueError("Plan inputs ID conflict")
        return ObjectRef(id=record.id, revision=0)
    finally:
        os.unlink(name)
