import json
import os
import tempfile
from pathlib import Path

from .arrangement import SingleLaneProvider
from .arrangement_domain import ChartDraft
from .asset_catalog import _safe
from .generation_catalog import _hash, resolve_generation, resolve_generation_with_context
from .generation_context import prepare_generation
from .generation_drafts import assemble_draft
from .project_store import ProjectSnapshot, _id, _json


def _rejection(project_dir, draft_id, draft):
    path = _safe(Path(project_dir) / "generation" / draft_id / "rejection.json")
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(record, dict)
        or set(record) != {"draft_id", "decision_id", "draft_sha256"}
        or record["draft_id"] != draft_id
        or _id(record["decision_id"]) != record["decision_id"]
        or record["draft_sha256"] != _hash(draft)
    ):
        raise ValueError("Invalid draft rejection")
    return record


def _evaluate(project_dir, snapshot, draft):
    model = ChartDraft.model_validate(draft)
    request = model.generation_run.request
    context = prepare_generation(project_dir, snapshot, request)
    rebuilt = assemble_draft(
        request, context, model.generation_run.output, SingleLaneProvider().describe()
    )
    rebuilt = rebuilt.model_copy(update={"id": model.id})
    if _json(rebuilt.model_dump(mode="json")) != _json(draft):
        raise ValueError("Current dependencies or rebuilt draft differ")
    errors = [item for item in rebuilt.validation if item.severity == "error"]
    if errors:
        raise ValueError("Chart validation errors: " + ", ".join(item.code for item in errors))
    return rebuilt


def inspect_draft(project_dir, snapshot, draft_id):
    draft_id = _id(draft_id)
    draft, context = resolve_generation_with_context(project_dir, draft_id)
    rejected = _rejection(project_dir, draft_id, draft) is not None
    reasons = []
    if rejected:
        reasons.append("Draft is rejected")
    try:
        _evaluate(project_dir, snapshot, draft)
    except (ValueError, OSError) as exc:
        reasons.append(str(exc))
    return {
        "draft": draft,
        "base_chart": context["chart"],
        "grid": context["grid"],
        "applicable": not reasons,
        "rejected": rejected,
        "reasons": reasons,
    }


def apply_draft(project_dir, snapshot, draft_id):
    draft_id = _id(draft_id)
    draft = resolve_generation(project_dir, draft_id)
    if _rejection(project_dir, draft_id, draft) is not None:
        raise ValueError("Draft is rejected")
    rebuilt = _evaluate(project_dir, snapshot, draft)
    return ProjectSnapshot(
        snapshot.project,
        snapshot.grid,
        tuple(
            rebuilt.chart if chart.id == rebuilt.chart.id else chart for chart in snapshot.charts
        ),
    )


def reject_draft(project_dir, draft_id, decision_id):
    draft_id, decision_id = _id(draft_id), _id(decision_id)
    draft = resolve_generation(project_dir, draft_id)
    record = {"draft_id": draft_id, "decision_id": decision_id, "draft_sha256": _hash(draft)}
    existing = _rejection(project_dir, draft_id, draft)
    if existing is not None:
        if existing != record:
            raise ValueError("Draft rejection conflict")
        return existing
    root = _safe(Path(project_dir) / "generation" / draft_id)
    path = _safe(root / "rejection.json")
    fd, name = tempfile.mkstemp(prefix=".rejection-", dir=root)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json(record))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            existing = _rejection(project_dir, draft_id, draft)
            if existing != record:
                raise ValueError("Draft rejection conflict")
            return existing
        return record
    finally:
        os.unlink(name)
