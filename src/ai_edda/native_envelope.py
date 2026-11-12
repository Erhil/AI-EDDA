import json
import re
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from .domain import Model
from .safe_zip import validate_member_paths

MAX_FILES = 10000
MAX_DECLARED_BYTES = 2 * 1024**3
MAX_ENVELOPE_BYTES = 8 * 1024**2
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PATTERNS = [
    r"project\.json",
    r"grid-[0-9]+\.json",
    rf"chart-{_UUID}-[0-9]+\.json",
    r"receipt-[0-9a-f]+\.json",
    rf"audio/{_UUID}/(?:song\.ogg|decoded\.wav|manifest\.json)",
    rf"assets/{_UUID}/(?:cover\.jpg|manifest\.json)",
    rf"analysis/{_UUID}/(?:analysis\.json|descriptor\.json|publication\.json|beat/(?:manifest\.json|beats\.npz)|rhythm/(?:manifest\.json|features\.npz))",
    rf"generation/{_UUID}/(?:context|draft|publication|rejection)\.json",
    rf"(?:plan-inputs|plans|imports)/{_UUID}\.json",
    rf"targets/{_UUID}-[1-9][0-9]*\.json",
    rf"exports/{_UUID}/(?:publication\.json|[^/]+\.zip|[^/]+\.receipt\.json|[^/]+/(?:info\.dat|song\.ogg|cover\.jpg|(?:Easy|Normal|Hard|Expert|ExpertPlus)\.dat))",
]


class NativeFile(Model):
    path: StrictStr
    size: StrictInt = Field(ge=0)
    sha256: StrictStr

    @field_validator("path")
    @classmethod
    def known_path(cls, value):
        if value.endswith("/") or not any(re.fullmatch(p, value) for p in _PATTERNS):
            raise ValueError("Unknown native path")
        return value

    @field_validator("sha256")
    @classmethod
    def valid_hash(cls, value):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Invalid SHA-256")
        return value


class NativeEnvelope(Model):
    archive_version: StrictInt
    schema_version: StrictInt
    project_id: StrictStr
    files: tuple[NativeFile, ...] = Field(max_length=MAX_FILES)

    @model_validator(mode="after")
    def validate_envelope(self):
        if self.archive_version != 1 or self.schema_version != 1:
            raise ValueError("Unsupported version")
        try:
            if str(UUID(self.project_id)) != self.project_id:
                raise ValueError("Invalid project ID")
        except (ValueError, AttributeError):
            raise ValueError("Invalid project ID") from None
        validate_member_paths((entry.path, False) for entry in self.files)
        if sum(entry.size for entry in self.files) > MAX_DECLARED_BYTES:
            raise ValueError("Declared size exceeds limit")
        return self


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_native_envelope(data: bytes) -> NativeEnvelope:
    if len(data) > MAX_ENVELOPE_BYTES:
        raise ValueError("Envelope exceeds limit")
    return NativeEnvelope.model_validate(json.loads(data, object_pairs_hook=_unique_pairs))


def serialize_native_envelope(envelope: NativeEnvelope) -> bytes:
    data = json.dumps(
        envelope.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(data) > MAX_ENVELOPE_BYTES:
        raise ValueError("Envelope exceeds limit")
    return data
