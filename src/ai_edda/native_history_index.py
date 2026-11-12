"""Metadata records and fingerprints for native history indexing."""

import re
from dataclasses import dataclass, field
from hashlib import sha256
from json import JSONEncoder
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from .domain import Chart, ChartNote, GridAnchor, HitEvent, SongProject, TempoSegment, TimingGrid
from .native_history_reader import NativeHistoryReadLimits


@dataclass(frozen=True)
class ChartRecord:
    path: Path
    chart_id: str
    revision: int
    grid_revision: int
    target_profile: tuple[str, int] | None
    fingerprint: str
    source_bytes: int
    node_count: int


@dataclass(frozen=True)
class GridRecord:
    path: Path
    revision: int
    fingerprint: str
    source_bytes: int
    node_count: int


@dataclass(frozen=True)
class HeadRecord:
    raw_head: Any
    project: SongProject


@dataclass(frozen=True)
class ReceiptRecord:
    command_id: str
    payload_hash: str
    previous_head: HeadRecord
    new_head: HeadRecord
    previous_receipt: str | None


@dataclass(frozen=True)
class NativeHistoryLimits:
    read_limits: NativeHistoryReadLimits = field(default_factory=NativeHistoryReadLimits)
    max_chart_nodes: int = 2_000_000
    max_current_bytes: int = 64 * 1024 * 1024
    max_current_nodes: int = 5_000_000
    max_index_bytes: int = 16 * 1024 * 1024
    max_index_chart_refs: int = 100_000

    def __post_init__(self) -> None:
        if not isinstance(self.read_limits, NativeHistoryReadLimits):
            raise TypeError("read_limits must be NativeHistoryReadLimits")
        for name in (
            "max_chart_nodes",
            "max_current_bytes",
            "max_current_nodes",
            "max_index_bytes",
            "max_index_chart_refs",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be a positive integer")
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_PROFILE = NativeHistoryLimits()


def model_fingerprint(model, *, cancel: Callable[[], None] | None = None) -> str:
    """Hash the canonical JSON form of an already validated, bounded model."""
    if cancel is not None:
        cancel()
    value = model.model_dump(mode="json")
    if cancel is not None:
        cancel()
    digest = sha256()
    encoder = JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    for chunk in encoder.iterencode(value):
        if cancel is not None:
            cancel()
        # Surrogate code points are accepted by the model layer through escaped JSON.
        # surrogatepass gives each one a stable, distinct byte sequence while leaving
        # every ordinary UTF-8 encoding unchanged.
        digest.update(chunk.encode("utf-8", errors="surrogatepass"))
    return digest.hexdigest()


def validated_chart(raw, *, limits=DEFAULT_PROFILE, cancel=None) -> Chart:
    """Validate a chart incrementally within its raw and expanded node budget."""

    def check():
        if cancel is not None:
            cancel()

    def count(value):
        total = 0
        stack = [value]
        while stack:
            check()
            item = stack.pop()
            total += 1
            if isinstance(item, dict):
                total += len(item)
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)
            if total > limits.max_chart_nodes:
                raise ValueError("Chart node count exceeds limit")
        return total

    def ref_id(value):
        if not isinstance(value, str) or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value
        ):
            raise ValueError("Invalid UUID")
        if str(UUID(value)) != value:
            raise ValueError("Invalid UUID")

    def revision(value):
        if type(value) is not int or value < 0:
            raise ValueError("Invalid revision")

    def fields(value, allowed):
        if not isinstance(value, dict):
            raise ValueError("Invalid chart record")
        for key in value:
            check()
            if key not in allowed:
                raise ValueError(f"Unexpected chart field: {key}")

    provenance_fields = {
        "created_by",
        "operation_id",
        "source_ids",
        "generation_run_id",
        "source_origin",
        "last_modified_by",
        "last_modified_operation_id",
    }

    def provenance(value):
        fields(value, provenance_fields)
        sources = value.get("source_ids", ())
        if not isinstance(sources, (list, tuple)):
            raise ValueError("Invalid provenance source_ids")
        for source in sources:
            check()
            if not isinstance(source, str):
                raise ValueError("Invalid provenance source_id")

    # Reserve the raw representation first. Expanded nodes below replace raw
    # event/note payloads, while the aggregate header is charged separately.
    count(raw)
    if not isinstance(raw, dict):
        raise ValueError("Invalid chart")
    fields(
        raw,
        {
            "id",
            "revision",
            "grid_revision",
            "metadata",
            "target_profile",
            "review",
            "provenance",
            "events",
        },
    )
    if "provenance" in raw:
        provenance(raw["provenance"])
    if "metadata" in raw:
        fields(raw["metadata"], {"name", "rank", "speed"})
    ref_id(raw["id"])
    revision(raw["revision"])
    revision(raw["grid_revision"])
    target = raw.get("target_profile")
    if target is not None:
        fields(target, {"id", "revision"})
        ref_id(target["id"])
        revision(target["revision"])
    events_raw = raw.get("events", [])
    if not isinstance(events_raw, (list, tuple)):
        raise ValueError("Invalid events")
    # Charge the normalized header before constructing any event or note.
    header = Chart.model_validate({**raw, "events": ()})
    expanded = count(header.model_dump(mode="json", exclude={"events"})) + 2
    if expanded > limits.max_chart_nodes:
        raise ValueError("Chart node count exceeds limit")
    events = []
    for event_raw in events_raw:
        check()
        fields(event_raw, {"id", "time_ref", "notes", "provenance", "locks"})
        if "time_ref" in event_raw:
            fields(event_raw["time_ref"], {"kind", "time_sec", "beat_q", "micro_offset_sec"})
        if "provenance" in event_raw:
            provenance(event_raw["provenance"])
        if "locks" in event_raw:
            fields(event_raw["locks"], {"existence", "time", "membership"})
        ref_id(event_raw["id"])
        notes_raw = event_raw.get("notes", [])
        if not isinstance(notes_raw, (list, tuple)):
            raise ValueError("Invalid notes")
        notes = []
        for note_raw in notes_raw:
            check()
            fields(note_raw, {"id", "lane", "hand_hint", "provenance", "locks"})
            if "provenance" in note_raw:
                provenance(note_raw["provenance"])
            if "locks" in note_raw:
                fields(note_raw["locks"], {"existence", "lane"})
            ref_id(note_raw["id"])
            note = ChartNote.model_validate(note_raw)
            expanded += count(note.model_dump(mode="json"))
            if expanded > limits.max_chart_nodes:
                raise ValueError("Chart node count exceeds limit")
            notes.append(note)
        event = HitEvent.model_validate({**event_raw, "notes": tuple(notes)})
        event_fields = event.model_dump(mode="json", exclude={"notes"})
        expanded += count(event_fields) + 2  # notes key and collection
        if expanded > limits.max_chart_nodes:
            raise ValueError("Chart node count exceeds limit")
        events.append(event)
    return Chart.model_validate({**raw, "events": tuple(events)})


def validated_grid(raw, *, limits=DEFAULT_PROFILE, cancel=None) -> TimingGrid:
    """Validate a grid within raw and normalized record node limits."""

    def check():
        if cancel is not None:
            cancel()

    def count(value):
        total = 0
        stack = [value]
        while stack:
            check()
            item = stack.pop()
            total += 1
            if isinstance(item, dict):
                total += len(item)
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)
            if total > limits.read_limits.max_nodes:
                raise ValueError("Grid node count exceeds limit")
        return total

    def fields(value, allowed):
        if not isinstance(value, dict):
            raise ValueError("Invalid grid record")
        for key in value:
            check()
            if key not in allowed:
                raise ValueError(f"Unexpected grid field: {key}")

    count(raw)
    fields(raw, {"revision", "segments", "meter", "review", "anchors"})
    if type(raw.get("revision")) is not int or raw["revision"] < 0:
        raise ValueError("Invalid revision")
    if "meter" in raw:
        fields(raw["meter"], {"numerator", "denominator", "bar_origin_beat_q"})
    segments_raw = raw.get("segments")
    anchors_raw = raw.get("anchors", ())
    if not isinstance(segments_raw, (list, tuple)) or not segments_raw:
        raise ValueError("Invalid segments")
    if not isinstance(anchors_raw, (list, tuple)):
        raise ValueError("Invalid anchors")
    dummy = {"start_time_sec": 0, "start_beat_q": 0, "bpm_quarter": 120, "grid_division": 4}
    header = TimingGrid.model_validate({**raw, "segments": (dummy,), "anchors": ()})
    expanded = count(header.model_dump(mode="json", exclude={"segments", "anchors"})) + 4
    if expanded > limits.read_limits.max_nodes:
        raise ValueError("Grid node count exceeds limit")
    segments = []
    for item in segments_raw:
        check()
        fields(
            item,
            {"start_time_sec", "start_beat_q", "bpm_quarter", "grid_division", "origin", "review"},
        )
        segment = TempoSegment.model_validate(item)
        expanded += count(segment.model_dump(mode="json"))
        if expanded > limits.read_limits.max_nodes:
            raise ValueError("Grid node count exceeds limit")
        segments.append(segment)
    anchors = []
    for item in anchors_raw:
        check()
        fields(item, {"beat_q", "time_sec", "origin"})
        anchor = GridAnchor.model_validate(item)
        expanded += count(anchor.model_dump(mode="json"))
        if expanded > limits.read_limits.max_nodes:
            raise ValueError("Grid node count exceeds limit")
        anchors.append(anchor)
    return TimingGrid.model_validate(
        {**raw, "segments": tuple(segments), "anchors": tuple(anchors)}
    )


class NativeHistoryIndex:
    """Retain bounded metadata for a native history."""

    def __init__(self, root, project_id, *, limits=DEFAULT_PROFILE, cancel=None):
        from .native_history import _uuid

        _uuid(project_id)
        self.root = Path(root)
        self.project_id = project_id
        self.limits = limits
        self.cancel = cancel
        self.current_head = None
        self.heads = []
        self.charts = {}
        self.grids = {}
        self.receipts = {}
        self._head_keys = {}
        self._owned_heads = set()
        self._index_bytes = 0
        self._index_chart_refs = 0

    def _check(self):
        if self.cancel is not None:
            self.cancel()

    def _size(self, value):
        encoder = JSONEncoder(
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        size = 0
        for chunk in encoder.iterencode(value):
            self._check()
            size += len(chunk.encode("utf-8", errors="surrogatepass"))
            if size > self.limits.max_index_bytes:
                raise ValueError("Index byte limit exceeded")
        return size

    def _reserve(self, size, refs=0):
        if self._index_bytes + size > self.limits.max_index_bytes:
            raise ValueError("Index byte limit exceeded")
        if self._index_chart_refs + refs > self.limits.max_index_chart_refs:
            raise ValueError("Index chart reference limit exceeded")

    def _check_project_raw(self, project) -> int:
        from .domain import ObjectRef
        from .native_history import _integer, _uuid

        if not isinstance(project, dict):
            raise ValueError("Invalid project")
        allowed_project = {
            "schema_version",
            "creation_request_hash",
            "id",
            "revision",
            "metadata",
            "audio",
            "cover",
            "analysis",
            "plan_inputs",
            "grid_revision",
            "chart_heads",
            "export_presets",
            "provenance",
        }
        for key in project:
            if key not in allowed_project:
                raise ValueError(f"Unexpected project field: {key}")

        def object_fields(value, allowed, label):
            if not isinstance(value, dict):
                raise ValueError(f"Invalid {label}")
            for key in value:
                if key not in allowed:
                    raise ValueError(f"Unexpected {label} field: {key}")

        object_fields(
            project.get("metadata", {}),
            {"title", "artist", "mapper", "subtitle", "explicit"},
            "metadata",
        )
        if project.get("audio") is not None:
            object_fields(
                project["audio"],
                {"path", "sha256", "sample_rate", "channels", "frames", "duration_sec", "subtype"},
                "audio",
            )
        if project.get("cover") is not None:
            object_fields(project["cover"], {"path", "sha256", "width", "height"}, "cover")
        object_fields(
            project.get("provenance", {}),
            {
                "created_by",
                "operation_id",
                "source_ids",
                "generation_run_id",
                "source_origin",
                "last_modified_by",
                "last_modified_operation_id",
            },
            "provenance",
        )
        presets = project.get("export_presets", ())
        if not isinstance(presets, (list, tuple)):
            raise ValueError("Invalid export presets")
        for preset in presets:
            if not isinstance(preset, str):
                raise ValueError("Invalid export preset")
        sources = project.get("provenance", {}).get("source_ids", ())
        if not isinstance(sources, (list, tuple)):
            raise ValueError("Invalid provenance source_ids")
        for source in sources:
            if not isinstance(source, str):
                raise ValueError("Invalid provenance source_id")
        _uuid(project["id"])
        if project["id"] != self.project_id or _integer(project["schema_version"]) != 1:
            raise ValueError("Invalid project")
        _integer(project["revision"])
        _integer(project["grid_revision"])
        refs = project["chart_heads"]
        if not isinstance(refs, (list, tuple)):
            raise ValueError("Invalid chart references")
        if len(refs) > self.limits.max_index_chart_refs:
            raise ValueError("Index chart reference limit exceeded")
        seen = set()
        for ref in refs:
            if ref is None:
                raise ValueError("Invalid reference")
            if not isinstance(ref, dict):
                raise ValueError("Invalid reference")
            object_fields(ref, {"id", "revision"}, "reference")
            _uuid(ref["id"])
            _integer(ref["revision"])
            ObjectRef.model_validate(ref)
        for ref in (project.get("analysis"), project.get("plan_inputs")):
            if ref is None:
                continue
            if not isinstance(ref, dict):
                raise ValueError("Invalid reference")
            object_fields(ref, {"id", "revision"}, "reference")
            _uuid(ref["id"])
            _integer(ref["revision"])
            ObjectRef.model_validate(ref)
        for ref in refs:
            if ref["id"] in seen:
                raise ValueError("Duplicate chart")
            seen.add(ref["id"])
        return len(refs)

    def record_head(self, raw):
        from copy import deepcopy

        from .native_history import _RECEIPT

        if not isinstance(raw, dict) or set(raw) != {"project", "receipt"}:
            raise ValueError("Invalid head")
        project = raw["project"]
        refs_count = self._check_project_raw(project)
        pointer = raw["receipt"]
        if pointer is not None and (
            not isinstance(pointer, str) or not re.fullmatch(_RECEIPT, pointer)
        ):
            raise ValueError("Invalid receipt pointer")
        raw_size = self._size(raw)
        key = sha256(
            "".join(
                JSONEncoder(
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).iterencode(raw)
            ).encode("utf-8", errors="surrogatepass")
        ).digest()
        for existing in self._head_keys.get(key, ()):
            if existing.raw_head == raw:
                return existing
        self._reserve(raw_size, refs_count)
        owned = deepcopy(raw)
        model = SongProject.model_validate(owned["project"])
        size = raw_size + self._size(model.model_dump(mode="json"))
        self._reserve(size, refs_count)
        record = HeadRecord(owned, model)
        self._check()
        self.heads.append(record)
        self._head_keys.setdefault(key, []).append(record)
        self._owned_heads.add(id(record))
        self._index_bytes += size
        self._index_chart_refs += refs_count
        return record

    def record_receipt(self, name, record):
        from .native_history import _RECEIPT

        if (
            not isinstance(name, str)
            or not re.fullmatch(_RECEIPT, name)
            or not isinstance(record, ReceiptRecord)
        ):
            raise ValueError("Invalid receipt")
        if (
            not isinstance(record.command_id, str)
            or not isinstance(record.payload_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", record.payload_hash)
        ):
            raise ValueError("Invalid receipt fields")
        if record.previous_receipt is not None and (
            not isinstance(record.previous_receipt, str)
            or not re.fullmatch(_RECEIPT, record.previous_receipt)
        ):
            raise ValueError("Invalid receipt pointer")
        if (
            id(record.previous_head) not in self._owned_heads
            or id(record.new_head) not in self._owned_heads
        ):
            raise ValueError("Foreign head")
        existing = self.receipts.get(name)
        if existing is not None:
            if existing == record:
                return existing
            raise ValueError("Conflicting receipt")
        size = self._size(
            [
                name,
                record.command_id,
                record.payload_hash,
                record.previous_receipt,
                id(record.previous_head),
                id(record.new_head),
            ]
        )
        self._reserve(size)
        self._check()
        self.receipts[name] = record
        self._index_bytes += size
        return record

    def _source_record(self, path, raw, chart):
        from .asset_catalog import _safe
        from .native_history_reader import read_native_history_json

        path = _safe(path)
        root = _safe(self.root)
        if path.parent != root:
            raise ValueError("Unsafe history path")
        actual = read_native_history_json(path, limits=self.limits.read_limits, cancel=self.cancel)
        if actual != raw:
            raise ValueError("Source content changed")
        model = (validated_chart if chart else validated_grid)(
            actual, limits=self.limits, cancel=self.cancel
        )
        expected = (
            f"chart-{model.id}-{model.revision}.json" if chart else f"grid-{model.revision}.json"
        )
        if path.name != expected:
            raise ValueError("History identity does not match path")
        count = 0
        stack = [model.model_dump(mode="json")]
        while stack:
            self._check()
            item = stack.pop()
            count += 1
            if isinstance(item, dict):
                count += len(item)
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)
        size = path.stat().st_size
        fingerprint = model_fingerprint(model, cancel=self.cancel)
        if chart:
            target = model.target_profile
            record = ChartRecord(
                path,
                model.id,
                model.revision,
                model.grid_revision,
                None if target is None else (target.id, target.revision),
                fingerprint,
                size,
                count,
            )
        else:
            record = GridRecord(path, model.revision, fingerprint, size, count)
        return record, model

    def _record_source(self, path, raw, chart):
        record, _ = self._source_record(path, raw, chart)
        records = self.charts if chart else self.grids
        key = (record.chart_id, record.revision) if chart else record.revision
        existing = records.get(key)
        if existing is not None:
            if existing == record:
                return existing
            raise ValueError("Conflicting history record")
        size = self._size(
            [str(value) if isinstance(value, Path) else value for value in vars(record).values()]
        )
        self._reserve(size)
        self._check()
        records[key] = record
        self._index_bytes += size
        return record

    def record_chart(self, path, raw):
        return self._record_source(path, raw, True)

    def record_grid(self, path, raw):
        return self._record_source(path, raw, False)

    def _load_source(self, record, chart):
        from .native_history_reader import read_native_history_json

        raw = read_native_history_json(
            record.path, limits=self.limits.read_limits, cancel=self.cancel
        )
        current, model = self._source_record(record.path, raw, chart)
        if current != record:
            raise ValueError("History source changed")
        return model

    def load_chart(self, chart_id, revision):
        return self._load_source(self.charts[(chart_id, revision)], True)

    def load_grid(self, revision):
        return self._load_source(self.grids[revision], False)

    def materialize_current(self):
        from .project_store import ProjectSnapshot

        self._check()
        head = self.current_head
        if head is None or id(head) not in self._owned_heads:
            raise ValueError("Unset or foreign current head")
        project = head.project
        if project.id != self.project_id:
            raise ValueError("Invalid project")
        if project.grid_revision not in self.grids:
            raise ValueError("Missing grid")
        records = []
        seen = set()
        total_bytes = total_nodes = 0
        for ref in project.chart_heads:
            self._check()
            if ref.id in seen:
                raise ValueError("Duplicate chart")
            seen.add(ref.id)
            record = self.charts.get((ref.id, ref.revision))
            if record is None:
                raise ValueError("Missing chart")
            if record.grid_revision != project.grid_revision:
                raise ValueError("Chart grid mismatch")
            records.append(record)
            total_bytes += record.source_bytes
            total_nodes += record.node_count
            if (
                total_bytes > self.limits.max_current_bytes
                or total_nodes > self.limits.max_current_nodes
            ):
                raise ValueError("Current chart limit exceeded")
        self._check()
        grid = self.load_grid(project.grid_revision)
        if grid.revision != project.grid_revision:
            raise ValueError("Grid revision mismatch")
        charts = []
        for record in records:
            self._check()
            chart = self.load_chart(record.chart_id, record.revision)
            if chart.grid_revision != project.grid_revision:
                raise ValueError("Chart grid mismatch")
            charts.append(chart)
        self._check()
        return ProjectSnapshot(project, grid, tuple(charts))

    def chart_matches(self, model):
        record = self.charts.get((model.id, model.revision))
        if record is None:
            return False
        return record.fingerprint == model_fingerprint(model, cancel=self.cancel)

    def grid_matches(self, model):
        record = self.grids.get(model.revision)
        if record is None:
            return False
        return record.fingerprint == model_fingerprint(model, cancel=self.cancel)
