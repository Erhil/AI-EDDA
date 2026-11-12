"""Validate persisted generation publications against verified sources."""

import hashlib
import re
from pathlib import Path

from .arrangement import validate_context
from .arrangement_domain import ArrangementContext, ArrangementRequest, ChartDraft
from .asset_catalog import _safe
from .domain import ChartNote, HitEvent
from .generation_catalog import _check_generation_publication, _check_generation_reassembly, _hash
from .native_envelope import MAX_DECLARED_BYTES, MAX_FILES
from .native_history import _UUID, _uuid
from .native_history_index import validated_chart, validated_grid
from .native_history_reader import read_native_history_json
from .project_store import _id, _json

MAX_TOTAL_BYTES = MAX_DECLARED_BYTES


def _preflight_collections(envelope, draft_value, limits, cancel):
    provenance_fields = {
        "created_by",
        "operation_id",
        "source_ids",
        "generation_run_id",
        "source_origin",
        "last_modified_by",
        "last_modified_operation_id",
    }

    def fields(value, allowed):
        if not isinstance(value, dict):
            raise ValueError("Invalid generation event or note")
        for key in value:
            if cancel is not None:
                cancel()
            if key not in allowed:
                raise ValueError(f"Unexpected generation field: {key}")

    def provenance(value):
        fields(value, provenance_fields)
        sources = value.get("source_ids", ())
        if not isinstance(sources, (list, tuple)):
            raise ValueError("Invalid provenance source_ids")
        for source in sources:
            if cancel is not None:
                cancel()
            if not isinstance(source, str):
                raise ValueError("Invalid provenance source_id")

    for raw, validator in (
        (envelope["context"].get("chart"), validated_chart),
        (envelope["context"].get("grid"), validated_grid),
        (draft_value.get("chart"), validated_chart),
    ):
        if raw is None:
            continue
        model = validator(raw, limits=limits, cancel=cancel)
        if _json(model.model_dump(mode="json")) != _json(raw):
            raise ValueError("Noncanonical generation collection")
        del model

    output = draft_value.get("generation_run", {}).get("output", {})
    events = output.get("events") if isinstance(output, dict) else None
    if isinstance(events, list):
        for raw_event in events:
            if cancel is not None:
                cancel()
            fields(raw_event, {"id", "time_ref", "notes", "provenance", "locks"})
            if "time_ref" in raw_event:
                fields(raw_event["time_ref"], {"kind", "time_sec", "beat_q", "micro_offset_sec"})
            if "provenance" in raw_event:
                provenance(raw_event["provenance"])
            if "locks" in raw_event:
                fields(raw_event["locks"], {"existence", "time", "membership"})
            notes = raw_event.get("notes", [])
            if isinstance(notes, list):
                for raw_note in notes:
                    if cancel is not None:
                        cancel()
                    fields(raw_note, {"id", "lane", "hand_hint", "provenance", "locks"})
                    if "provenance" in raw_note:
                        provenance(raw_note["provenance"])
                    if "locks" in raw_note:
                        fields(raw_note["locks"], {"existence", "lane"})
                    note = ChartNote.model_validate(raw_note)
                    if _json(note.model_dump(mode="json")) != _json(raw_note):
                        raise ValueError("Noncanonical generation note")
                    del note
            event = HitEvent.model_validate(raw_event)
            if _json(event.model_dump(mode="json")) != _json(raw_event):
                raise ValueError("Noncanonical generation event")
            del event

    for container in (
        envelope["request"],
        draft_value.get("generation_run", {}).get("request", {}),
        output,
    ):
        target = container.get("target")
        divisions = target.get("allowed_subdivisions") if isinstance(target, dict) else None
        if isinstance(divisions, list):
            for item in divisions:
                if cancel is not None:
                    cancel()
                if type(item) is not int or item <= 0:
                    raise ValueError("Invalid allowed subdivision")


def validate_native_generation(root, history, analysis_map, plans_map, targets_map, cancel=None):
    cancelled = []

    def check_cancel():
        if cancel is not None:
            try:
                cancel()
            except BaseException as exc:
                cancelled.append(exc)
                raise

    try:
        return _validate(root, history, analysis_map, plans_map, targets_map, check_cancel)
    except (TypeError, KeyError, AttributeError, RecursionError, OverflowError, OSError) as exc:
        if cancelled and exc is cancelled[-1]:
            raise
        raise ValueError("Invalid native generation") from exc


def _validate(root, history, analysis_map, plans_map, targets_map, cancel):
    if cancel is not None:
        cancel()
    root = _safe(Path(root))
    catalog = _safe(root / "generation")
    if not catalog.exists():
        return None
    if not catalog.is_dir():
        raise ValueError("Invalid generation directory")
    entries = []
    total = count = 0
    for path in catalog.iterdir():
        if cancel is not None:
            cancel()
        path = _safe(path)
        if not re.fullmatch(_UUID, path.name) or not path.is_dir():
            raise ValueError("Invalid generation entry")
        _uuid(path.name)
        files = {p.name: _safe(p) for p in path.iterdir()}
        if set(files) not in (
            {"context.json", "draft.json", "publication.json"},
            {"context.json", "draft.json", "publication.json", "rejection.json"},
        ):
            raise ValueError("Invalid generation files")
        if any(not p.is_file() for p in files.values()):
            raise ValueError("Invalid generation file")
        for file in files.values():
            cancel()
            count += 1
            total += file.stat().st_size
            if count > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise ValueError("Generation catalog exceeds limit")
        entries.append((path, files))
    for path, files in entries:
        cancel()
        envelope = read_native_history_json(files["context.json"], limits=history.limits.read_limits, cancel=cancel)
        draft_value = read_native_history_json(files["draft.json"], limits=history.limits.read_limits, cancel=cancel)
        if not isinstance(envelope, dict) or set(envelope) != {
            "project_id",
            "request_id",
            "draft_id",
            "request",
            "context",
        }:
            raise ValueError("Invalid generation context")
        for key in ("project_id", "request_id", "draft_id"):
            _uuid(envelope[key])
        # Validate raw chart collections before aggregate models can collect
        # one error for every malformed event.
        _preflight_collections(envelope, draft_value, history.limits, cancel)
        request = ArrangementRequest.model_validate(envelope["request"])
        context = ArrangementContext.model_validate(envelope["context"])
        draft = ChartDraft.model_validate(draft_value)
        if any(
            _json(model.model_dump(mode="json")) != _json(value)
            for model, value in (
                (request, envelope["request"]),
                (context, envelope["context"]),
                (draft, draft_value),
            )
        ):
            raise ValueError("Noncanonical generation data")
        if (
            envelope["project_id"] != history.project_id
            or request.project_id != envelope["project_id"]
            or envelope["request_id"] != request.id
            or envelope["draft_id"] != path.name
        ):
            raise ValueError("Generation identity mismatch")
        for value in (
            request.id,
            request.analysis.id,
            request.plan.id,
            request.chart.id,
            draft.id,
            draft.generation_run.id,
            draft.chart.id,
        ):
            _uuid(value)
        event = note = None
        for event in (*draft.chart.events, *draft.generation_run.output.events):
            _uuid(event.id)
            for note in event.notes:
                _uuid(note.id)
        if request.reference_chart is not None or context.reference_chart is not None:
            raise ValueError("Reference chart is unsupported")
        validate_context(request, context)
        def equal(a, b):
            return _json(a.model_dump(mode="json")) == _json(b.model_dump(mode="json"))
        if not history.grid_matches(context.grid) or not history.chart_matches(context.chart):
            raise ValueError("Unknown historical source")
        if (
            context.analysis.id not in analysis_map
            or not equal(context.analysis, analysis_map[context.analysis.id])
            or context.plan.id not in plans_map
            or not equal(context.plan, plans_map[context.plan.id])
        ):
            raise ValueError("Unknown generation source")
        ref = context.chart.target_profile
        if ref is not None and (
            (ref.id, ref.revision) not in targets_map
            or not equal(request.target, targets_map[(ref.id, ref.revision)])
        ):
            raise ValueError("Unknown target")
        _check_generation_reassembly(path.name, request, context, draft, draft_value, cancel=cancel)
        context_hash = _hash(envelope)
        result_hash = _hash({"draft": draft_value})
        public_bytes = _json(draft.model_dump(mode="json"))
        public_hash = hashlib.sha256(public_bytes).hexdigest()
        del request, context, draft, draft_value, envelope, event, note
        receipt = read_native_history_json(files["publication.json"], limits=history.limits.read_limits, cancel=cancel)
        digests = {}
        for name in ("context.json", "draft.json"):
            digest = hashlib.sha256()
            with files[name].open("rb") as stream:
                while True:
                    cancel()
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
            digests[name] = digest.hexdigest()
        _check_generation_publication(receipt, context_sha256=context_hash, result_sha256=result_hash,
                                      public_bytes=public_bytes, artifact_digests=digests, cancel=cancel)
        if "rejection.json" in files:
            record = read_native_history_json(files["rejection.json"], limits=history.limits.read_limits, cancel=cancel)
            if (not isinstance(record, dict)
                or set(record) != {"draft_id", "decision_id", "draft_sha256"}
                or record["draft_id"] != path.name
                or _id(record["decision_id"]) != record["decision_id"]
                or record["draft_sha256"] != public_hash):
                raise ValueError("Invalid draft rejection")
            del record
        del receipt, public_bytes
    return None
