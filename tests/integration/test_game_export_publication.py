from dataclasses import replace
from uuid import uuid4

import pytest
from test_package_builder import snapshot as snapshot

from ai_edda.game_export_catalog import (
    publish_game_export,
    reconcile_game_export,
    resolve_game_export,
    verify_game_export,
)
from ai_edda.packaging import ArtifactBuilder


@pytest.mark.parametrize("count", [1, 2, 3])
def test_publication_replay_and_conflict(tmp_path, snapshot, count):
    export_id = str(uuid4())
    source = tmp_path / "source"
    ArtifactBuilder(tmp_path).build(replace(snapshot(count), snapshot_id=export_id), source, "song")
    result = verify_game_export(source, "song")
    context = {
        "project_id": str(uuid4()),
        "export_id": export_id,
        "project_revision": result["receipt"]["project_revision"],
    }
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    public = publish_game_export(tmp_path, export_id, "job", context, result, source)
    assert reconcile_game_export(tmp_path, export_id, "job", context, result) == (True, public)
    assert publish_game_export(tmp_path, export_id, "job", context, result, source) == public
    assert resolve_game_export(tmp_path, export_id)["public_result"] == public
    with pytest.raises(ValueError):
        reconcile_game_export(tmp_path, export_id, "other", context, result)
    assert {p: p.read_bytes() for p in before} == before


def test_copy_failure_cleans_stage(tmp_path, snapshot, monkeypatch):
    export_id = str(uuid4())
    source = tmp_path / "source"
    ArtifactBuilder(tmp_path).build(replace(snapshot(1), snapshot_id=export_id), source, "song")
    result = verify_game_export(source, "song")
    context = {
        "project_id": str(uuid4()),
        "export_id": export_id,
        "project_revision": result["receipt"]["project_revision"],
    }
    monkeypatch.setattr(
        "ai_edda.game_export_catalog.os.fsync", lambda fd: (_ for _ in ()).throw(OSError("failure"))
    )
    with pytest.raises(OSError):
        publish_game_export(tmp_path, export_id, "job", context, result, source)
    assert list((tmp_path / "exports").iterdir()) == []
