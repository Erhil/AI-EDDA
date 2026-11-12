import re
from pathlib import Path

from .asset_catalog import _safe
from .native_history import _UUID, MAX_NODES, MAX_TOTAL_BYTES, _read, _uuid
from .plan_inputs import resolve_plan_inputs
from .target_catalog import resolve_target


def validate_native_inputs(project_dir, history, audio_map, analysis_map, cancel=None):
    root = _safe(Path(project_dir))
    budget = [MAX_TOTAL_BYTES, MAX_NODES]
    inputs, targets = {}, {}
    chart_ids = {chart.chart_id for chart in history.charts.values()}

    def entries(name, pattern):
        directory = _safe(root / name)
        if not directory.exists():
            return
        if not directory.is_dir():
            raise ValueError("Invalid catalog directory")
        for path in directory.iterdir():
            if cancel is not None:
                cancel()
            path = _safe(path)
            match = re.fullmatch(pattern, path.name)
            if match is None or not path.is_file():
                raise ValueError("Invalid catalog file")
            _uuid(match[1])
            raw = _read(path, budget)
            yield match, raw

    for match, raw in entries("plan-inputs", rf"({_UUID})\.json"):
        if not isinstance(raw, dict) or not isinstance(raw.get("anchors"), list):
            raise ValueError("Invalid plan inputs structure")
        for anchor in raw["anchors"]:
            if not isinstance(anchor, dict) or not isinstance(anchor.get("id"), str):
                raise ValueError("Invalid anchor structure")
            try:
                _uuid(anchor["id"])
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Invalid anchor id") from None
        record = resolve_plan_inputs(root, {"id": match[1], "revision": 0})
        audio = audio_map.get(record.audio_asset_id)
        analysis = analysis_map.get(record.analysis.id)
        if (
            audio is None
            or analysis is None
            or analysis.revision != record.analysis.revision
            or analysis.audio_asset_id != record.audio_asset_id
            or analysis.audio != audio
            or record.audio_sha256 != audio.sha256
        ):
            raise ValueError("Invalid plan inputs media")
        for anchor in record.anchors:
            if anchor.time_sec >= audio.duration_sec or not set(anchor.target_scope) <= chart_ids:
                raise ValueError("Invalid anchor time or scope")
        inputs[record.id] = record
    for match, _raw in entries("targets", rf"({_UUID})-([1-9][0-9]*)\.json"):
        revision = int(match[2])
        record = resolve_target(root, {"id": match[1], "revision": revision})
        targets[(record.id, revision)] = record
    for snapshot in history.heads:
        ref = snapshot.project.plan_inputs
        if ref is not None and (ref.id not in inputs or inputs[ref.id].revision != ref.revision):
            raise ValueError("Missing historical plan inputs")
        if ref is not None:
            chart_ids = {c.id for c in snapshot.project.chart_heads}
            if any(not set(a.target_scope) <= chart_ids for a in inputs[ref.id].anchors):
                raise ValueError("Invalid selected anchor scope")
    for chart in history.charts.values():
        ref = chart.target_profile
        if ref is not None and ref not in targets:
            raise ValueError("Missing historical target")
    return inputs, targets
