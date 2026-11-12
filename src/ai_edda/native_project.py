"""Validate a complete native project without changing it."""

import os
import re
import stat
from pathlib import Path

from .asset_catalog import _safe
from .game_export_catalog import resolve_game_export
from .import_receipts import validate_native_import_receipts
from .native_envelope import _PATTERNS, MAX_DECLARED_BYTES, MAX_FILES
from .native_generation import validate_native_generation
from .native_history import MAX_NODES, _read, validate_native_history
from .native_inputs import validate_native_inputs
from .native_media import validate_native_media
from .native_plans import validate_native_plans

_CATALOGS = {
    "audio",
    "assets",
    "analysis",
    "generation",
    "plan-inputs",
    "plans",
    "imports",
    "targets",
    "exports",
}


def validate_native_project(root, expected_project_id, cancel=None):
    cancellation = []

    def check():
        if cancel is not None:
            try:
                cancel()
            except BaseException as exc:
                cancellation.append(exc)
                raise

    def fail(error):
        raise error

    root = _safe(Path(root))
    if not root.is_dir():
        raise ValueError("Invalid project directory")
    files, directories, total = [], [], 0
    for current, dirs, names in os.walk(root, onerror=fail, followlinks=False):
        for name in dirs + names:
            check()
            path = _safe(Path(current) / name)
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if name in dirs:
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError("Invalid directory")
                directories.append(relative)
            else:
                if not stat.S_ISREG(info.st_mode) or not any(
                    re.fullmatch(p, relative) for p in _PATTERNS
                ):
                    raise ValueError("Unknown native file")
                files.append(relative)
                total += info.st_size
                if len(files) > MAX_FILES or total > MAX_DECLARED_BYTES:
                    raise ValueError("Native project exceeds limit")
    for directory in directories:
        if directory not in _CATALOGS and not any(
            path.startswith(directory + "/") for path in files
        ):
            raise ValueError("Unknown empty directory")
    try:
        history = validate_native_history(root, expected_project_id, cancel=check)
        audio, covers, analyses = validate_native_media(root, history, check)
        inputs, targets = validate_native_inputs(root, history, audio, analyses, check)
        plans = validate_native_plans(root, history, analyses, inputs, check)
        validate_native_generation(root, history, analyses, plans, targets, check)
        validate_native_import_receipts(root, history, check)
        exports = root / "exports"
        if exports.exists():
            for directory in exports.iterdir():
                check()
                publication = resolve_game_export(root, directory.name)
                if publication["project_id"] != expected_project_id:
                    raise ValueError("Export project mismatch")
                receipt = _read(
                    directory / (publication["folder_name"] + ".receipt.json"),
                    [8 * 1024 * 1024, MAX_NODES],
                )
                selected = {(item["chart_id"], item["revision"]) for item in receipt["selection"]}
                if not any(
                    head.project.revision == receipt["project_revision"]
                    and head.project.grid_revision == receipt["grid_revision"]
                    and selected
                    <= {(chart.id, chart.revision) for chart in head.project.chart_heads}
                    and head.project.audio is not None
                    and head.project.audio.sha256 == receipt["audio_sha256"]
                    and head.project.cover is not None
                    and head.project.cover.sha256 == receipt["members"]["cover.jpg"]["sha256"]
                    for head in history.heads
                ):
                    raise ValueError("Export has no matching historical snapshot")
        check()
        return history.materialize_current()
    except (TypeError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
        if cancellation:
            raise cancellation[0]
        raise ValueError("Invalid native project") from exc
    except ValueError:
        if cancellation:
            raise cancellation[0]
        raise
    except OSError:
        if cancellation:
            raise cancellation[0]
        raise
