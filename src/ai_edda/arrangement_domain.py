from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .domain import AudioAsset, Chart, HitEvent, Model, ObjectRef, Provenance, TimingGrid, new_id
from .validation import Diagnostic

Id = str


class FeatureManifestRef(Model):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    version: str = Field(min_length=1)

    @model_validator(mode="after")
    def relative(self):
        p = PurePosixPath(self.path.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts or ":" in self.path:
            raise ValueError("manifest path must be relative")
        return self


class RhythmAnalysis(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    revision: int = Field(default=0, strict=True, ge=0)
    audio_asset_id: str = Field(min_length=1)
    audio: AudioAsset
    manifests: tuple[FeatureManifestRef, ...] = ()


class Section(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    kind: str = "whole_song"
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def bounds(self):
        if self.end_sec <= self.start_sec:
            raise ValueError("empty section")
        return self


class RhythmSite(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    time_sec: float = Field(ge=0)
    beat_q: float
    attack_strength: float | None = Field(default=None, ge=0)
    metric_role: str | None = None
    source_role: str | None = None
    acoustic_evidence: tuple[str, ...] = ()
    provenance: Provenance = Field(default_factory=Provenance)


class MusicalAnchor(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    time_sec: float = Field(ge=0)
    strength: Literal["soft", "required"] = "soft"
    tolerance_sec: float = Field(default=0, ge=0)
    target_scope: tuple[str, ...] = ()
    provenance: Provenance = Field(default_factory=Provenance)


class MotifLink(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    site_ids: tuple[str, ...] = Field(min_length=2)
    provenance: Provenance = Field(default_factory=Provenance)


class UserAnnotation(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    text: str
    provenance: Provenance = Field(default_factory=Provenance)


class MusicalPlan(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    revision: int = Field(default=0, strict=True, ge=0)
    audio_asset_id: str = Field(min_length=1)
    audio_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    analysis: ObjectRef
    grid_revision: int = Field(strict=True, ge=0)
    sections: tuple[Section, ...] = Field(min_length=1)
    rhythm_sites: tuple[RhythmSite, ...] = ()
    musical_anchors: tuple[MusicalAnchor, ...] = ()
    motif_links: tuple[MotifLink, ...] = ()
    user_annotations: tuple[UserAnnotation, ...] = ()


class TargetProfile(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    version: int = Field(strict=True, ge=1)
    name: str = Field(min_length=1)
    intended_rank: int | None = Field(default=None, strict=True, ge=0)
    allowed_subdivisions: tuple[Annotated[int, Field(strict=True, gt=0)], ...] | None = None
    syncopation_budget: float | None = Field(default=None, ge=0, le=1)
    ornament_budget: float | None = Field(default=None, ge=0, le=1)
    target_hits_per_second: float | None = Field(default=None, ge=0)
    max_hits_in_window: int | None = Field(default=None, strict=True, gt=0)
    window_sec: float | None = Field(default=None, gt=0)
    double_event_fraction: float | None = Field(default=None, ge=0, le=1)
    max_simultaneous_notes: int | None = Field(default=None, strict=True, ge=1, le=2)
    min_same_hand_interval_sec: float | None = Field(default=None, ge=0)
    max_same_hand_burst: int | None = Field(default=None, strict=True, gt=0)
    movement_profile: str | None = None
    transition_complexity: float | None = Field(default=None, ge=0, le=1)
    recovery_policy: str | None = None
    accent_preference: str | None = None
    motif_consistency: float | None = Field(default=None, ge=0, le=1)
    style_id: str | None = None

    @model_validator(mode="after")
    def controls(self):
        if self.allowed_subdivisions is not None and any(
            type(x) is not int or x <= 0 for x in self.allowed_subdivisions
        ):
            raise ValueError("invalid subdivisions")
        if (self.max_hits_in_window is None) != (self.window_sec is None):
            raise ValueError("density window requires both fields")
        return self


class ArrangementRequest(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    project_id: str = Field(min_length=1)
    generator_id: str = Field(min_length=1)
    generator_version: str = Field(min_length=1)
    audio_asset_id: str = Field(min_length=1)
    audio_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    analysis: "StrictObjectRef"
    plan: "StrictObjectRef"
    grid_revision: int = Field(strict=True, ge=0)
    chart: "StrictObjectRef"
    target: TargetProfile
    range_q: "ArrangementRangeQ"
    mode: Literal["single_lane", "heuristic_arranger"]
    lane: int | None = Field(default=None, strict=True, ge=0, le=3)
    reference_chart: "StrictObjectRef | None" = None
    reference_influence: Literal["rhythm_theme", "pattern_theme", "adaptation"] | None = None
    seed: int = Field(strict=True)

    @model_validator(mode="after")
    def bounds(self):
        if (self.mode == "single_lane") != (self.lane is not None):
            raise ValueError("lane required only for single_lane")
        if (self.reference_chart is None) != (self.reference_influence is None):
            raise ValueError("reference requires influence")
        return self


class StrictObjectRef(Model):
    id: str = Field(min_length=1, strict=True)
    revision: int = Field(strict=True, ge=0)


class ArrangementRangeQ(Model):
    start_q: float = Field(allow_inf_nan=False)
    end_q: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def bounds(self):
        if self.end_q <= self.start_q:
            raise ValueError("empty span")
        return self


class GeneratorCapabilities(Model):
    backend_id: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)
    schema_versions: tuple[str, ...] = ()
    feature_versions: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()
    target_fields: tuple[str, ...] = ()
    lock_support: str = ""
    context_limit_sec: float | None = Field(default=None, ge=0)
    checkpoint: str | None = None


class ArrangementContext(Model):
    grid: TimingGrid
    plan: MusicalPlan
    analysis: RhythmAnalysis
    chart: Chart
    reference_chart: Chart | None = None


class GeneratedSpan(Model):
    events: tuple[HitEvent, ...] = ()
    source_evidence: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    target: TargetProfile
    seed: int = Field(strict=True)
    backend_id: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)


class GenerationRun(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    request: ArrangementRequest
    capabilities: GeneratorCapabilities
    output: GeneratedSpan


class ChartDiff(Model):
    added_event_ids: tuple[str, ...] = ()
    removed_event_ids: tuple[str, ...] = ()
    changed_event_ids: tuple[str, ...] = ()


class ChartDraft(Model):
    id: str = Field(default_factory=new_id, min_length=1)
    base_chart: ObjectRef
    chart: Chart
    diff: ChartDiff
    validation: tuple[Diagnostic, ...] = ()
    generation_run: GenerationRun
