import json
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from .arrangement_domain import GeneratedSpan, GeneratorCapabilities
from .domain import BeatTimeRef, ChartNote, HitEvent, Provenance
from .timing import final_time_sec
from .validation import Diagnostic


def validate_context(request, context):
    """Reject a context that does not match the revisions captured by a request."""
    matches = (
        request.audio_asset_id == context.analysis.audio_asset_id,
        request.audio_sha256 == context.analysis.audio.sha256,
        request.analysis.id == context.analysis.id,
        request.analysis.revision == context.analysis.revision,
        request.plan.id == context.plan.id,
        request.plan.revision == context.plan.revision,
        request.grid_revision == context.grid.revision,
        request.chart.id == context.chart.id,
        request.chart.revision == context.chart.revision,
        context.plan.analysis.id == context.analysis.id,
        context.plan.analysis.revision == context.analysis.revision,
        context.plan.audio_asset_id == context.analysis.audio_asset_id,
        context.plan.audio_sha256 == context.analysis.audio.sha256,
        context.plan.grid_revision == context.grid.revision,
        context.chart.grid_revision == context.grid.revision,
    )
    if not all(matches):
        raise ValueError("STALE_REVISION")
    if request.reference_chart is not None:
        reference = context.reference_chart
        if (
            reference is None
            or request.reference_chart.id != reference.id
            or request.reference_chart.revision != reference.revision
        ):
            raise ValueError("STALE_REVISION")


class ArrangementProvider(Protocol):
    def describe(self) -> GeneratorCapabilities: ...
    def generate(self, request, context) -> GeneratedSpan: ...


class SingleLaneProvider:
    """Consume MusicalPlan sites; raw feature arrays and manifests are unsupported."""

    def describe(self):
        return GeneratorCapabilities(
            backend_id="single_lane",
            backend_version="1",
            modes=("single_lane",),
            schema_versions=("1",),
            feature_versions=(),
            target_fields=("allowed_subdivisions",),
            lock_support="event and note locks",
        )

    def generate(self, request, context):
        validate_context(request, context)
        if (request.generator_id, request.generator_version, request.mode) != (
            "single_lane",
            "1",
            "single_lane",
        ):
            raise ValueError("unsupported generator or mode")
        if request.reference_chart is not None:
            raise ValueError("reference charts are unsupported")
        allowed = {"id", "version", "name", "intended_rank", "allowed_subdivisions"}
        unsupported = [
            k for k, v in request.target.model_dump().items() if k not in allowed and v is not None
        ]
        if unsupported:
            raise ValueError("unsupported target controls: " + ", ".join(unsupported))
        divisions = request.target.allowed_subdivisions
        if divisions is None:
            divisions = (1,)
        elif not divisions:
            raise ValueError("allowed_subdivisions must not be empty")
        if any(d not in (1, 2, 4) for d in divisions):
            raise ValueError("unsupported subdivisions")
        grid = context.grid
        start, end = grid.time_at(request.range_q.start_q), grid.time_at(request.range_q.end_q)
        duration = context.analysis.audio.duration_sec

        def locked(e):
            return any(e.locks.model_dump().values()) or any(
                any(n.locks.model_dump().values()) for n in e.notes
            )

        protected = [
            e
            for e in context.chart.events
            if locked(e) and start <= final_time_sec(grid, e.time_ref) < end
        ]
        for event in protected:
            lanes = [note.lane for note in event.notes]
            if not 1 <= len(event.notes) <= 2 or len(set(lanes)) != len(lanes):
                raise ValueError(f"protected event cannot be preserved as valid output: {event.id}")
        events = list(protected)
        evidence = []
        occupied = [final_time_sec(grid, e.time_ref) for e in context.chart.events if locked(e)]
        grouped = {}
        for site in context.plan.rhythm_sites:
            if (
                site.source_role not in ("beat", "downbeat")
                or not site.acoustic_evidence
                or not start <= site.time_sec < end
            ):
                continue
            candidates = [round(site.beat_q * d) / d for d in divisions]
            q = min(candidates, key=lambda x: (abs(x - site.beat_q), x))
            t = grid.time_at(q)
            if not (
                start <= t < end and 0 <= t < duration and t >= grid.segments[0].start_time_sec
            ):
                continue
            grouped.setdefault(q, []).append(site)
        for q, sites in sorted(grouped.items()):
            t = grid.time_at(q)
            if any(abs(t - x) <= 1e-6 for x in occupied):
                continue
            ids = tuple(sorted(s.id for s in sites))
            key = json.dumps([request.id, ids], ensure_ascii=False, separators=(",", ":"))
            p = Provenance(created_by="generated", generation_run_id=request.id, source_ids=ids)
            events.append(
                HitEvent(
                    id=str(uuid5(NAMESPACE_URL, key + ":event")),
                    time_ref=BeatTimeRef(kind="beat", beat_q=q),
                    notes=(
                        ChartNote(
                            id=str(uuid5(NAMESPACE_URL, key + ":note")),
                            lane=request.lane,
                            provenance=p,
                        ),
                    ),
                    provenance=p,
                )
            )
            evidence.extend(x for s in sites for x in s.acoustic_evidence)
        for anchor in context.plan.musical_anchors:
            if (
                anchor.strength == "required"
                and (not anchor.target_scope or context.chart.id in anchor.target_scope)
                and start <= anchor.time_sec < end
                and not any(
                    abs(final_time_sec(grid, e.time_ref) - anchor.time_sec) <= anchor.tolerance_sec
                    for e in events
                )
            ):
                raise ValueError(f"unsupported required musical anchor: {anchor.id}")
        events.sort(key=lambda e: (final_time_sec(grid, e.time_ref), e.id))
        return GeneratedSpan(
            events=tuple(events),
            source_evidence=tuple(dict.fromkeys(evidence)),
            diagnostics=(
                Diagnostic(
                    code="TIMING_DIAGNOSTIC",
                    severity="warning",
                    message="This is a timing diagnostic, not an ergonomic four-lane arrangement.",
                ),
            ),
            target=request.target,
            seed=request.seed,
            backend_id="single_lane",
            backend_version="1",
        )
