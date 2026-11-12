from bisect import bisect_right
from math import isclose
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class TempoSegment(Model):
    start_time_sec: float = Field(ge=0)
    start_beat_q: int
    bpm_quarter: float = Field(gt=0)
    grid_division: int = Field(gt=0)
    origin: str = "manual"
    review: str = "needs_review"


class Meter(Model):
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)
    bar_origin_beat_q: float = 0


class GridAnchor(Model):
    beat_q: float
    time_sec: float
    origin: str = "manual"


class TimingGrid(Model):
    revision: int = Field(default=0, ge=0)
    segments: tuple[TempoSegment, ...] = Field(min_length=1)
    meter: Meter = Field(default_factory=lambda: Meter(numerator=4, denominator=4))
    review: str = "needs_review"
    anchors: tuple[GridAnchor, ...] = ()

    @model_validator(mode="after")
    def continuous(self):
        for a, b in zip(self.segments, self.segments[1:]):
            if b.start_beat_q <= a.start_beat_q or not isclose(
                b.start_time_sec,
                a.start_time_sec + (b.start_beat_q - a.start_beat_q) * 60 / a.bpm_quarter,
                abs_tol=1e-6,
                rel_tol=0,
            ):
                raise ValueError("GRID_DISCONTINUITY")
        beat_starts = [s.start_beat_q for s in self.segments]
        for anchor in self.anchors:
            i = max(0, bisect_right(beat_starts, anchor.beat_q) - 1)
            segment = self.segments[i]
            expected_time = (
                segment.start_time_sec
                + (anchor.beat_q - segment.start_beat_q) * 60 / segment.bpm_quarter
            )
            if not isclose(expected_time, anchor.time_sec, abs_tol=1e-6, rel_tol=0):
                raise ValueError("GRID_ANCHOR_INCONSISTENT")
        return self

    def time_at(self, q: float) -> float:
        i = max(0, bisect_right([s.start_beat_q for s in self.segments], q) - 1)
        s = self.segments[i]
        return s.start_time_sec + (q - s.start_beat_q) * 60 / s.bpm_quarter

    def inverse_time(self, t: float) -> float:
        i = max(0, bisect_right([s.start_time_sec for s in self.segments], t) - 1)
        s = self.segments[i]
        return s.start_beat_q + (t - s.start_time_sec) * s.bpm_quarter / 60


class BeatTimeRef(Model):
    kind: Literal["beat"]
    beat_q: float
    micro_offset_sec: float = 0


class AudioTimeRef(Model):
    kind: Literal["audio"]
    time_sec: float


TimeRef = BeatTimeRef | AudioTimeRef


def new_id():
    return str(uuid4())


class EventLocks(Model):
    existence: bool = False
    time: bool = False
    membership: bool = False


class NoteLocks(Model):
    existence: bool = False
    lane: bool = False


class Provenance(Model):
    created_by: Literal["manual", "generated", "imported"] = "manual"
    operation_id: str | None = None
    source_ids: tuple[str, ...] = ()
    generation_run_id: str | None = None
    source_origin: Literal["manual", "generated", "unknown"] | None = None
    last_modified_by: str | None = None
    last_modified_operation_id: str | None = None

    @model_validator(mode="after")
    def import_origin(self):
        if self.created_by == "imported" and self.source_origin is None:
            object.__setattr__(self, "source_origin", "unknown")
        return self


class ChartNote(Model):
    id: str = Field(default_factory=new_id)
    lane: int = Field(strict=True, ge=0, le=3)
    hand_hint: Literal["left", "right"] | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    locks: NoteLocks = Field(default_factory=NoteLocks)


class HitEvent(Model):
    id: str = Field(default_factory=new_id)
    time_ref: TimeRef
    notes: tuple[ChartNote, ...] = ()
    provenance: Provenance = Field(default_factory=Provenance)
    locks: EventLocks = Field(default_factory=EventLocks)


class ChartMetadata(Model):
    name: str = ""
    rank: int | None = None
    speed: float | None = None


class ObjectRef(Model):
    id: str
    revision: int = Field(ge=0)


class Chart(Model):
    id: str = Field(default_factory=new_id)
    revision: int = Field(default=0, ge=0)
    grid_revision: int = Field(default=0, ge=0)
    metadata: ChartMetadata = Field(default_factory=ChartMetadata)
    target_profile: ObjectRef | None = None
    review: str = "needs_review"
    provenance: Provenance = Field(default_factory=Provenance)
    events: tuple[HitEvent, ...] = ()

    @model_validator(mode="after")
    def unique_ids(self):
        event_ids = [e.id for e in self.events]
        note_ids = [n.id for e in self.events for n in e.notes]
        if len(event_ids) != len(set(event_ids)) or len(note_ids) != len(set(note_ids)):
            raise ValueError("duplicate event or note ID")
        return self


class SongMetadata(Model):
    title: str = ""
    artist: str = ""
    mapper: str = ""
    subtitle: str = ""
    explicit: bool = False


class AudioAsset(Model):
    path: str
    sha256: str
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    frames: int = Field(ge=0)
    duration_sec: float = Field(ge=0)
    subtype: str


class CoverAsset(Model):
    path: str
    sha256: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SongProject(Model):
    schema_version: int = 1
    creation_request_hash: str | None = None
    id: str = Field(default_factory=new_id)
    revision: int = Field(default=0, ge=0)
    metadata: SongMetadata = Field(default_factory=SongMetadata)
    audio: AudioAsset | None = None
    cover: CoverAsset | None = None
    analysis: ObjectRef | None = None
    plan_inputs: ObjectRef | None = None
    grid_revision: int = Field(default=0, ge=0)
    chart_heads: tuple[ObjectRef, ...] = ()
    export_presets: tuple[str, ...] = ()
    provenance: Provenance = Field(default_factory=Provenance)
