"""Project acoustic observations into a grid without creating chart events."""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

import numpy as np

from .arrangement_domain import MusicalPlan, RhythmSite, Section
from .domain import ObjectRef, Provenance


@dataclass(frozen=True)
class ProjectionConfig:
    """Uncalibrated heuristics: absolute RMS floor and support distance in seconds."""

    min_rms: float = 1e-5
    support_window_sec: float = 0.05


def project_musical_plan(
    analysis,
    grid,
    beat_times,
    downbeat_times,
    feature_times,
    onset_envelope,
    rms,
    onset_times,
    config=ProjectionConfig(),
):
    duration = analysis.audio.duration_sec
    if not np.isfinite(duration) or duration <= 0:
        raise ValueError("audio duration must be positive")
    if (
        not np.isfinite(config.min_rms)
        or config.min_rms < 0
        or not np.isfinite(config.support_window_sec)
        or config.support_window_sec < 0
    ):
        raise ValueError("invalid projection configuration")

    def array(values, name, bounded=True):
        a = np.asarray(values, dtype=float)
        if a.ndim != 1 or not np.isfinite(a).all():
            raise ValueError(f"invalid {name}")
        if bounded and (np.any(a < 0) or np.any(a > duration) or np.any(np.diff(a) <= 0)):
            raise ValueError(f"invalid {name} times")
        return a

    beats = array(beat_times, "beat")
    downbeats = array(downbeat_times, "downbeat")
    times = array(feature_times, "feature")
    onsets = array(onset_times, "onset")
    envelope = array(onset_envelope, "onset envelope", False)
    energy = array(rms, "rms", False)
    if (
        not len(times)
        or len(times) != len(envelope)
        or len(times) != len(energy)
        or np.any(envelope < 0)
        or np.any(energy < 0)
    ):
        raise ValueError("misaligned or negative features")
    provenance = Provenance(created_by="generated", source_ids=(analysis.id,))
    sites = []
    for role, observations in (("beat", beats), ("downbeat", downbeats), ("onset", onsets)):
        for index, t in enumerate(observations):
            nearest = int(np.argmin(np.abs(times - t)))
            if (
                abs(times[nearest] - t) > config.support_window_sec
                or energy[nearest] <= config.min_rms
            ):
                continue
            key = f"{analysis.id}:{analysis.revision}:{role}:{index}"
            sites.append(
                RhythmSite(
                    id=str(uuid5(NAMESPACE_URL, key)),
                    time_sec=float(t),
                    beat_q=grid.inverse_time(float(t)),
                    attack_strength=float(envelope[nearest]),
                    metric_role=role if role != "onset" else None,
                    source_role=role,
                    acoustic_evidence=(
                        f"analysis:{analysis.id}:{analysis.revision}:{role}_times:{index}",
                        f"analysis:{analysis.id}:{analysis.revision}:features:{nearest}",
                    ),
                    provenance=provenance,
                )
            )
    sites.sort(key=lambda site: (site.time_sec, site.source_role))
    # Include all inputs that define the immutable projection. Grid revision alone
    # is insufficient because distinct grids can share the same revision.
    grid_identity = grid.model_dump(mode="json")
    import json

    identity = json.dumps(
        {
            "algorithm": "musical-plan-projection-v1",
            "analysis": {"id": analysis.id, "revision": analysis.revision},
            "config": {"min_rms": config.min_rms, "support_window_sec": config.support_window_sec},
            "grid": grid_identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return MusicalPlan(
        id=str(uuid5(NAMESPACE_URL, identity)),
        audio_asset_id=analysis.audio_asset_id,
        audio_sha256=analysis.audio.sha256,
        analysis=ObjectRef(id=analysis.id, revision=analysis.revision),
        grid_revision=grid.revision,
        sections=(
            Section(
                id=str(uuid5(NAMESPACE_URL, f"{analysis.id}:{analysis.revision}:whole_song")),
                start_sec=0,
                end_sec=duration,
                provenance=provenance,
            ),
        ),
        rhythm_sites=tuple(sites),
    )


def reproject_musical_plan(plan, grid):
    """Keep source observations and IDs while updating the grid coordinates."""
    sites = tuple(
        site.model_copy(update={"beat_q": grid.inverse_time(site.time_sec)})
        for site in plan.rhythm_sites
    )
    return plan.model_copy(
        update={
            "revision": plan.revision + 1,
            "grid_revision": grid.revision,
            "rhythm_sites": sites,
        }
    )
