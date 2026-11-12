"""Read-only grid proposals derived from the selected verified analysis."""

import hashlib
import math

from .analysis_catalog import resolve_analysis
from .domain import Model, TimingGrid
from .grid_hypotheses import generate_grid_hypotheses
from .project_store import ProjectSnapshot, _json
from .timing import retime_chart


class AcceptGridSuggestion(Model):
    hypothesis_id: str


def get_grid_suggestions(project_dir, snapshot):
    ref = snapshot.project.analysis
    if ref is None or snapshot.project.audio is None:
        return ()
    analysis, arrays = resolve_analysis(project_dir, ref.id)
    if analysis.revision != ref.revision or analysis.audio != snapshot.project.audio:
        raise ValueError("ANALYSIS_AUDIO_MISMATCH")
    times = arrays["beat"]["arrays"]["beat_times"]
    times = times[times <= analysis.audio.duration_sec]
    suggestions = []
    for hypothesis in generate_grid_hypotheses(times, analysis.audio.duration_sec):
        first = hypothesis.grid.segments[0]
        segments = []
        for old in snapshot.grid.segments:
            t = first.start_time_sec + old.start_beat_q * 60 / first.bpm_quarter
            if t < 0:
                break
            segments.append(
                old.model_copy(
                    update={
                        "start_time_sec": t,
                        "bpm_quarter": first.bpm_quarter,
                        "origin": "automatic",
                        "review": "approved",
                    }
                )
            )
        boundary_conflict = len(segments) != len(snapshot.grid.segments)
        anchor_conflict = any(
            not math.isclose(
                first.start_time_sec + a.beat_q * 60 / first.bpm_quarter,
                a.time_sec,
                abs_tol=1e-6,
                rel_tol=0,
            )
            for a in snapshot.grid.anchors
        )
        conflict = boundary_conflict or anchor_conflict
        grid = None
        if not conflict:
            grid = TimingGrid(
                revision=snapshot.grid.revision + 1,
                segments=tuple(segments),
                meter=snapshot.grid.meter,
                anchors=snapshot.grid.anchors,
                review="approved",
            )
        identity = {
            "analysis": ref.model_dump(mode="json"),
            "base_grid": snapshot.grid.model_dump(mode="json"),
            "candidate": hypothesis.grid.model_dump(mode="json"),
        }
        reason = (
            "BOUNDARY_CONFLICT"
            if boundary_conflict
            else "ANCHOR_CONFLICT"
            if anchor_conflict
            else None
        )
        suggestions.append(
            {
                "hypothesis_id": hashlib.sha256(_json(identity)).hexdigest(),
                "grid": grid,
                "selectable": not conflict,
                "anchor_conflict": anchor_conflict,
                "boundary_conflict": boundary_conflict,
                "reason": reason,
                "estimated_bpm": hypothesis.estimated_bpm,
                "phase_sec": hypothesis.phase_sec,
                "residual_rms_sec": hypothesis.residual_rms_sec,
                "inlier_count": hypothesis.inlier_count,
                "score": hypothesis.score,
                "score_description": "uncalibrated fit, not probability",
                "interpretation": hypothesis.interpretation,
            }
        )
    return tuple(suggestions)


def apply(project_dir, snapshot, payload):
    request = AcceptGridSuggestion.model_validate(payload)
    suggestion = next(
        (
            s
            for s in get_grid_suggestions(project_dir, snapshot)
            if s["hypothesis_id"] == request.hypothesis_id
        ),
        None,
    )
    if suggestion is None or not suggestion["selectable"]:
        raise ValueError("GRID_SUGGESTION_UNAVAILABLE")
    grid = suggestion["grid"]
    return ProjectSnapshot(
        snapshot.project, grid, tuple(retime_chart(chart, grid) for chart in snapshot.charts)
    )
