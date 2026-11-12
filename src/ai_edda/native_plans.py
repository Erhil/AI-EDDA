"""Validate persisted plans against their verified historical sources."""

import math
import re
from pathlib import Path

from .analysis_catalog import resolve_analysis
from .asset_catalog import _safe
from .musical_plan import ProjectionConfig, project_musical_plan
from .native_history import _UUID, MAX_NODES, MAX_TOTAL_BYTES, _read, _uuid
from .native_history_index import validated_grid
from .plan_catalog import load_plan
from .project_store import _json


def validate_native_plans(root, history, analysis_map, inputs_map, cancel=None):
    try:
        return _validate(root, history, analysis_map, inputs_map, cancel)
    except (TypeError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
        raise ValueError("Invalid native plan") from exc


def _validate(root, history, analysis_map, inputs_map, cancel):
    if cancel is not None:
        cancel()
    root = _safe(Path(root))
    directory = _safe(root / "plans")
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise ValueError("Invalid plans directory")
    budget = [MAX_TOTAL_BYTES, MAX_NODES]
    result = {}
    for path in directory.iterdir():
        if cancel is not None:
            cancel()
        path = _safe(path)
        match = re.fullmatch(rf"({_UUID})\.json", path.name)
        if match is None or not path.is_file():
            raise ValueError("Invalid plan file")
        record_id = match[1]
        _uuid(record_id)
        raw = _read(path, budget)
        if (
            not isinstance(raw, dict)
            or set(raw) != {"identity", "plan"}
            or not isinstance(raw["identity"], dict)
            or not isinstance(raw["plan"], dict)
        ):
            raise ValueError("Invalid plan envelope")
        identity = raw["identity"]
        if (
            set(identity)
            != {"algorithm", "analysis", "grid", "config", "plan_inputs_ref", "plan_inputs"}
            or identity["algorithm"] != "musical-plan-projection-v1"
        ):
            raise ValueError("Invalid plan identity")
        analysis_data = identity["analysis"]
        if not isinstance(analysis_data, dict):
            raise ValueError("Invalid analysis")
        analysis = analysis_map.get(analysis_data.get("id"))
        if analysis is None or _json(analysis.model_dump(mode="json")) != _json(analysis_data):
            raise ValueError("Unknown analysis")
        grid = validated_grid(identity["grid"], limits=history.limits, cancel=cancel)
        if _json(grid.model_dump(mode="json")) != _json(
            identity["grid"]
        ) or not history.grid_matches(grid):
            raise ValueError("Unknown grid")
        config = identity["config"]
        if (
            not isinstance(config, dict)
            or set(config) != {"min_rms", "support_window_sec"}
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or v < 0
                for v in config.values()
            )
        ):
            raise ValueError("Invalid projection config")
        ref, embedded = identity["plan_inputs_ref"], identity["plan_inputs"]
        inputs = None
        if ref is not None or embedded is not None:
            if (
                not isinstance(ref, dict)
                or set(ref) != {"id", "revision"}
                or type(ref["revision"]) is not int
                or not isinstance(embedded, dict)
            ):
                raise ValueError("Invalid plan inputs reference")
            inputs = inputs_map.get(ref["id"])
            if (
                inputs is None
                or ref != {"id": inputs.id, "revision": inputs.revision}
                or _json(embedded) != _json(inputs.model_dump(mode="json"))
                or inputs.analysis.id != analysis.id
                or inputs.analysis.revision != analysis.revision
                or inputs.audio_asset_id != analysis.audio_asset_id
                or inputs.audio_sha256 != analysis.audio.sha256
            ):
                raise ValueError("Unknown plan inputs")
        resolved, bundle = resolve_analysis(root, analysis.id)
        if resolved != analysis:
            raise ValueError("Analysis mismatch")
        beat, rhythm = bundle["beat"]["arrays"], bundle["rhythm"]["arrays"]
        projected = project_musical_plan(
            analysis,
            grid,
            beat["beat_times"],
            beat["downbeat_times"],
            rhythm["times"],
            rhythm["onset_envelope"],
            rhythm["rms"],
            rhythm["onset_times"],
            ProjectionConfig(**config),
        )
        projected = projected.model_copy(
            update={"id": record_id, "musical_anchors": inputs.anchors if inputs else ()}
        )
        persisted = load_plan(root, {"id": record_id, "revision": 0})
        if _json(projected.model_dump(mode="json")) != _json(
            persisted.model_dump(mode="json")
        ) or _json(raw["plan"]) != _json(persisted.model_dump(mode="json")):
            raise ValueError("Plan projection mismatch")
        result[record_id] = persisted
    return result
