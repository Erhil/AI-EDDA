import ctypes
import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from .domain import Chart, ObjectRef, SongProject, TimingGrid


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _id(value):
    return str(uuid.UUID(str(value)))


def _documents():
    if os.name != "nt":
        return Path.home() / "Documents"
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    folder = GUID(
        0xFDD39AD0,
        0x238F,
        0x46AF,
        (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
    )
    path = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(folder), 0, None, ctypes.byref(path)
    ):
        raise OSError("Cannot resolve Documents folder")
    try:
        return Path(path.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


@dataclass(frozen=True)
class ProjectSnapshot:
    project: SongProject
    grid: TimingGrid
    charts: tuple[Chart, ...]


class ProjectStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else _documents() / "AI-EDDA"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lockfile = open(self.root / ".writer.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._lockfile.seek(0)
                msvcrt.locking(self._lockfile.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lockfile.close()
            raise RuntimeError("Project root is locked")
        self._mutex = threading.RLock()

    def close(self):
        with self._mutex:
            if not self._lockfile.closed:
                self._lockfile.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _dir(self, project_id):
        if self._lockfile.closed:
            raise RuntimeError("Store is closed")
        root = self.root.resolve()
        directory = self.root / _id(project_id)
        # A redirected store root is supported, but a project entry must stay
        # within that resolved root and must not itself be a reparse point.
        attrs = (
            getattr(directory.stat(follow_symlinks=False), "st_file_attributes", 0)
            if directory.exists()
            else 0
        )
        reparse = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if attrs & reparse:
            raise ValueError("Project directory cannot be a reparse point")
        resolved = directory.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Project directory escapes store root") from exc
        return directory

    def _write(self, path, value, replace=False):
        temp = path.with_name("." + uuid.uuid4().hex + ".tmp")
        try:
            with open(temp, "xb") as f:
                f.write(_json(value))
                f.flush()
                os.fsync(f.fileno())
            if not replace and path.exists():
                raise FileExistsError(path)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def _read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def create(self, project, grid, charts=()):
        with self._mutex:
            project_id = _id(project.id)
            directory = self._dir(project_id)
            grid = grid.model_copy(update={"revision": 0})
            charts = tuple(c.model_copy(update={"revision": 0, "grid_revision": 0}) for c in charts)
            for chart in charts:
                _id(chart.id)
            project = project.model_copy(
                update={
                    "id": project_id,
                    "revision": 0,
                    "grid_revision": 0,
                    "chart_heads": tuple(ObjectRef(id=c.id, revision=0) for c in charts),
                }
            )
            staging = self.root / (".create-" + uuid.uuid4().hex)
            staging.mkdir()
            try:
                self._write(staging / "grid-0.json", grid.model_dump(mode="json"))
                for c in charts:
                    self._write(staging / f"chart-{_id(c.id)}-0.json", c.model_dump(mode="json"))
                self._write(
                    staging / "project.json",
                    {"project": project.model_dump(mode="json"), "receipt": None},
                )
                os.rename(staging, directory)
            except BaseException:
                import shutil

                shutil.rmtree(staging, ignore_errors=True)
                raise
            return ProjectSnapshot(project, grid, charts)

    def list(self):
        result = []
        for path in self.root.iterdir():
            if path.is_dir() and (path / "project.json").exists():
                try:
                    if _id(path.name) == path.name:
                        result.append(self.load(path.name).project)
                except ValueError:
                    continue
        return result

    def load(self, project_id):
        directory = self._dir(project_id)
        head = self._read(directory / "project.json")
        project = SongProject.model_validate(head["project"])
        grid = TimingGrid.model_validate(
            self._read(directory / f"grid-{project.grid_revision}.json")
        )
        charts = tuple(
            Chart.model_validate(self._read(directory / f"chart-{_id(c.id)}-{c.revision}.json"))
            for c in project.chart_heads
        )
        return ProjectSnapshot(project, grid, charts)

    def lookup_result(self, project_id, command_id, payload):
        with self._mutex:
            directory = self._dir(project_id)
            receipt_name = self._read(directory / "project.json")["receipt"]
            digest = hashlib.sha256(_json(payload)).hexdigest()
            while receipt_name:
                receipt = self._read(directory / receipt_name)
                if receipt["command_id"] == command_id:
                    if receipt["payload_hash"] != digest:
                        raise ValueError("COMMAND_ID_CONFLICT")
                    return True, receipt["result"]
                receipt_name = receipt["previous_receipt"]
            return False, None

    def commit(
        self,
        project_id,
        command_id,
        payload,
        expected_project_revision,
        expected_grid_revision,
        expected_chart_revisions,
        project,
        grid,
        charts,
        result,
    ):
        with self._mutex:
            if self._lockfile.closed:
                raise RuntimeError("Store is closed")
            directory = self._dir(project_id)
            head = self._read(directory / "project.json")
            digest = hashlib.sha256(_json(payload)).hexdigest()
            receipt_name = head["receipt"]
            while receipt_name:
                receipt = self._read(directory / receipt_name)
                if receipt["command_id"] == command_id:
                    if receipt["payload_hash"] != digest:
                        raise ValueError("COMMAND_ID_CONFLICT")
                    return receipt["result"]
                receipt_name = receipt["previous_receipt"]
            current = self.load(project_id)
            if (
                current.project.revision != expected_project_revision
                or current.grid.revision != expected_grid_revision
                or {c.id: c.revision for c in current.charts} != dict(expected_chart_revisions)
            ):
                raise ValueError("STALE_REVISION")

            def next_revision(prefix):
                nums = [
                    int(p.stem[len(prefix) :])
                    for p in directory.glob(prefix + "*.json")
                    if p.stem[len(prefix) :].isdigit()
                ]
                return max(nums, default=-1) + 1

            grid_rev = next_revision("grid-")
            grid = grid.model_copy(update={"revision": grid_rev})
            self._write(directory / f"grid-{grid_rev}.json", grid.model_dump(mode="json"))
            updated = []
            for c in charts:
                cid = _id(c.id)
                revision = next_revision(f"chart-{cid}-")
                c = c.model_copy(
                    update={"id": cid, "revision": revision, "grid_revision": grid_rev}
                )
                self._write(directory / f"chart-{cid}-{revision}.json", c.model_dump(mode="json"))
                updated.append(c)
            project = project.model_copy(
                update={
                    "id": _id(project_id),
                    "revision": current.project.revision + 1,
                    "grid_revision": grid_rev,
                    "chart_heads": tuple(ObjectRef(id=c.id, revision=c.revision) for c in updated),
                }
            )
            new_head = {
                "project": project.model_dump(mode="json"),
                "receipt": "receipt-" + uuid.uuid4().hex + ".json",
            }
            result_value = (
                result(ProjectSnapshot(project, grid, tuple(updated)))
                if callable(result)
                else result
            )
            result_json = json.loads(_json(result_value))
            receipt = {
                "command_id": command_id,
                "payload_hash": digest,
                "previous_head": head,
                "new_head": new_head,
                "previous_receipt": head["receipt"],
                "result": result_json,
            }
            self._write(directory / new_head["receipt"], receipt)
            self._write(directory / "project.json", new_head, replace=True)
            return result_json
