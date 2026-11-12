import hashlib
import threading
import uuid
from typing import Any

from . import (
    analysis_commands,
    asset_commands,
    draft_decisions,
    grid_suggestions,
    plan_input_commands,
    target_commands,
)
from .commands import dispatch
from .domain import (
    Chart,
    ChartMetadata,
    Model,
    SongMetadata,
    SongProject,
    TempoSegment,
    TimingGrid,
)
from .history import HistoryRecord
from .project_store import _json


class ProjectView(Model):
    project: SongProject
    grid: TimingGrid
    charts: tuple[Chart, ...]


class CommandEnvelope(Model):
    command_id: str
    base_project_revision: int
    expected_grid_revision: int
    expected_chart_revisions: dict[str, int]
    kind: str
    payload: dict[str, Any]


class ApplyDraftPayload(Model):
    draft_id: uuid.UUID


def _view(snapshot):
    return ProjectView(project=snapshot.project, grid=snapshot.grid, charts=snapshot.charts)


class ApplicationService:
    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._history = {}
        self._pending_history = {}

    def _apply_history(self, history_key, pending):
        undo, redo = self._history.setdefault(history_key, ([], []))
        direction = pending.get("direction")
        record = pending.get("record")
        if direction == "undo":
            undo.pop()
            redo.append(record)
        elif direction == "redo":
            redo.pop()
            undo.append(record)
        elif record is not None:
            undo.append(record)
            redo.clear()

    def create_project(self, request_id, metadata):
        with self._lock:
            project_id = str(uuid.UUID(str(request_id)))
            metadata = SongMetadata.model_validate(metadata)
            digest = hashlib.sha256(_json(metadata.model_dump(mode="json"))).hexdigest()
            try:
                snapshot = self.store.load(project_id)
            except FileNotFoundError:
                project = SongProject(
                    id=project_id, metadata=metadata, creation_request_hash=digest
                )
                grid = TimingGrid(
                    segments=(
                        TempoSegment(
                            start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4
                        ),
                    )
                )
                snapshot = self.store.create(
                    project, grid, (Chart(metadata=ChartMetadata(rank=5, speed=15)),)
                )
            if snapshot.project.creation_request_hash != digest:
                raise ValueError("REQUEST_ID_CONFLICT")
            return _view(snapshot)

    def list(self):
        return self.store.list()

    def get(self, project_id):
        return _view(self.store.load(project_id))

    def inspect_draft(self, project_id, draft_id):
        with self._lock:
            snapshot = self.store.load(project_id)
            return draft_decisions.inspect_draft(self.store._dir(project_id), snapshot, draft_id)

    def reject_draft(self, project_id, draft_id, decision_id):
        with self._lock:
            self.store.load(project_id)
            return draft_decisions.reject_draft(self.store._dir(project_id), draft_id, decision_id)

    def execute(self, project_id, command, writer_session="local", *, record_history=True):
        command = CommandEnvelope.model_validate(command)
        with self._lock:
            snapshot = self.store.load(project_id)
            meaning = command.model_dump(mode="json", exclude={"command_id"})
            history_key = (project_id, writer_session)
            pending = self._pending_history.get(history_key)
            if pending is not None:
                try:
                    found_pending, pending_result = self.store.lookup_result(
                        project_id, pending["command_id"], pending["meaning"]
                    )
                except Exception:
                    raise
                if found_pending:
                    self._apply_history(history_key, pending)
                    del self._pending_history[history_key]
                else:
                    del self._pending_history[history_key]
            found, result = self.store.lookup_result(project_id, command.command_id, meaning)
            if found:
                return ProjectView.model_validate(result)
            undo, redo = self._history.setdefault(history_key, ([], []))
            direction = command.kind if command.kind in ("undo", "redo") else None
            if direction:
                if command.payload:
                    raise ValueError("history payload must be empty")
                source = undo if direction == "undo" else redo
                if not source:
                    raise ValueError("HISTORY_EMPTY")
                record = source[-1]
                updated = record.restore(snapshot, direction)
            else:
                updated = self._dispatch(
                    project_id, snapshot, command.kind, command.payload, command.command_id
                )
                record = (
                    HistoryRecord.between(snapshot, updated)
                    if record_history and command.kind != "review_grid"
                    else None
                )
            prepared = {
                "command_id": command.command_id,
                "meaning": meaning,
                "direction": direction,
                "record": record,
            }
            self._pending_history[history_key] = prepared
            try:
                result = self.store.commit(
                    project_id,
                    command.command_id,
                    meaning,
                    command.base_project_revision,
                    command.expected_grid_revision,
                    command.expected_chart_revisions,
                    updated.project,
                    updated.grid,
                    updated.charts,
                    lambda actual: _view(actual).model_dump(mode="json"),
                )
            except Exception:
                try:
                    found, result = self.store.lookup_result(
                        project_id, command.command_id, meaning
                    )
                except Exception:
                    raise
                if not found:
                    self._pending_history.pop(history_key, None)
                    raise
            self._apply_history(history_key, prepared)
            self._pending_history.pop(history_key, None)
            return ProjectView.model_validate(result)

    def _dispatch(self, project_id, snapshot, kind, payload, operation_id, *, publish=True):
        if kind == "set_chart_target":
            return target_commands.apply(
                snapshot, payload, self.store._dir(project_id), publish=publish
            )
        if kind == "apply_draft":
            draft = ApplyDraftPayload.model_validate(payload)
            return draft_decisions.apply_draft(
                self.store._dir(project_id), snapshot, str(draft.draft_id)
            )
        if kind == "accept_grid_suggestion":
            return grid_suggestions.apply(self.store._dir(project_id), snapshot, payload)
        if kind == "set_musical_anchors":
            return plan_input_commands.apply(
                snapshot, payload, self.store._dir(project_id), publish=publish
            )
        if kind == "select_analysis":
            return analysis_commands.apply(snapshot, payload, self.store._dir(project_id))
        if kind in ("attach_audio", "replace_audio", "set_cover"):
            return asset_commands.apply(snapshot, kind, payload, self.store._dir(project_id))
        candidate = dispatch(snapshot, kind, payload, operation_id)
        if kind == "delete_chart":
            return plan_input_commands.remove_deleted_chart_scopes(
                snapshot,
                candidate,
                payload["chart_id"],
                self.store._dir(project_id),
                publish=publish,
            )
        return candidate

    def preview(self, project_id, kind, payload):
        with self._lock:
            return _view(
                self._dispatch(
                    project_id,
                    self.store.load(project_id),
                    kind,
                    payload,
                    str(uuid.uuid4()),
                    publish=False,
                )
            )
