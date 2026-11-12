"""Build a game export from the snapshot captured by the parent."""

from pathlib import Path

from .domain import AudioAsset, Chart, SongMetadata, TimingGrid
from .game_export_catalog import verify_game_export
from .packaging import ArtifactBuilder, ExportSnapshot


def game_export_worker(payload, progress, cancel):
    def check():
        if cancel.is_set():
            raise RuntimeError("Game export cancelled")

    check()
    project = Path(payload["project_dir"])
    workspace = Path(payload["workspace"])
    if not project.is_absolute() or not workspace.is_absolute():
        raise ValueError("Project and workspace paths must be absolute")
    value = payload["snapshot"]
    fields = {
        "metadata",
        "grid",
        "selected_charts",
        "audio",
        "cover_path",
        "cover_sha256",
        "project_revision",
        "snapshot_id",
    }
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError("Snapshot must contain exactly the ExportSnapshot fields")
    snapshot = ExportSnapshot(
        metadata=SongMetadata.model_validate(value["metadata"]),
        grid=TimingGrid.model_validate(value["grid"]),
        selected_charts=tuple(Chart.model_validate(c) for c in value["selected_charts"]),
        audio=AudioAsset.model_validate(value["audio"]),
        cover_path=value["cover_path"],
        cover_sha256=value["cover_sha256"],
        project_revision=value["project_revision"],
        snapshot_id=value["snapshot_id"],
    )
    check()
    prepared = workspace / "prepared"
    prepared.mkdir()
    check()
    progress(0.1)
    check()
    ArtifactBuilder(project).build(snapshot, prepared, payload["folder_name"])
    check()
    progress(0.8)
    check()
    result = verify_game_export(prepared, payload["folder_name"], cancel=check)
    check()
    progress(1.0)
    check()
    return result
