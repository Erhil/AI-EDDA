import json
import subprocess
import sys
from dataclasses import replace
from uuid import uuid4

import pytest
from test_package_builder import snapshot as snapshot

from ai_edda import game_export_catalog as catalog
from ai_edda.native_capture import capture_inventory
from ai_edda.packaging import ArtifactBuilder


@pytest.mark.parametrize("after", [False, True])
def test_process_exit_recovery(tmp_path, snapshot, after):
    project_id, export_id = str(uuid4()), str(uuid4())
    project = tmp_path / "project"
    project.mkdir()
    head = json.dumps({"project": {"id": project_id}}).encode()
    (project / "project.json").write_bytes(head)
    source = tmp_path / "source"
    ArtifactBuilder(tmp_path).build(replace(snapshot(1), snapshot_id=export_id), source, "song")
    result = catalog.verify_game_export(source, "song")
    context = {
        "project_id": project_id,
        "export_id": export_id,
        "project_revision": result["receipt"]["project_revision"],
    }
    job = "arbitrary/job"
    packet = tmp_path / "packet.json"
    packet.write_text(
        json.dumps([str(project), export_id, job, context, result, str(source), after]),
        encoding="utf-8",
    )
    script = """import json, os, sys
from ai_edda import game_export_catalog as c
p, e, j, ctx, result, source, after = json.load(open(sys.argv[1], encoding="utf-8"))
rename = os.rename
def stop(src, dst):
    if after: rename(src, dst)
    os._exit(98 if after else 97)
c.os.rename = stop
c.publish_game_export(p, e, j, ctx, result, source)
"""
    child = subprocess.run([sys.executable, "-c", script, str(packet)], timeout=30)
    assert child.returncode == (98 if after else 97)
    wrapper = catalog._game_stage_path(project, project_id, export_id, job)
    assert wrapper.exists()
    capture_inventory(project, project_id, head)
    found, public = catalog.reconcile_game_export(project, export_id, job, context, result)
    assert found is after
    assert not wrapper.exists()
    assert catalog.reconcile_game_export(project, export_id, job, context, result) == (
        found,
        public,
    )
    capture_inventory(project, project_id, head)
    assert catalog.publish_game_export(project, export_id, job, context, result, source) == (
        public if after else catalog._public(context, result)
    )


@pytest.mark.parametrize("marker", [None, {"version": True}, {"version": 1, "foreign": True}])
def test_unowned_wrapper_is_preserved(tmp_path, marker):
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    sentinel = wrapper / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    if marker is not None:
        (wrapper / "marker.json").write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        catalog._cleanup_game_stage(
            wrapper, tmp_path, "project", "export", "job", "context", "result"
        )
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_payload_link_is_preserved(tmp_path):
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("preserve", encoding="utf-8")
    marker = {
        "version": 1,
        "project_id": "project",
        "export_id": "export",
        "job_id": "job",
        "context_sha256": "context",
        "result_sha256": "result",
    }
    (wrapper / "marker.json").write_text(json.dumps(marker), encoding="utf-8")
    try:
        (wrapper / "export").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks unavailable")
    with pytest.raises(ValueError):
        catalog._cleanup_game_stage(
            wrapper, tmp_path, "project", "export", "job", "context", "result"
        )
    assert (outside / "sentinel").read_text(encoding="utf-8") == "preserve"


def test_boolean_marker_version_is_not_owned(tmp_path):
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    marker = {
        "version": True,
        "project_id": "project",
        "export_id": "export",
        "job_id": "job",
        "context_sha256": "context",
        "result_sha256": "result",
    }
    (wrapper / "marker.json").write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(ValueError):
        catalog._cleanup_game_stage(
            wrapper, tmp_path, "project", "export", "job", "context", "result"
        )
    assert wrapper.exists()
