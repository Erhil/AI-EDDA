"""Independent recovery checks for real backup publication boundaries."""

import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from test_native_export_publication import publication as publication
from test_native_export_publication import publish
from test_native_export_publication import snapshot as snapshot
from test_native_export_recovery import marker, recover
from test_native_export_worker import case as case

from ai_edda import native_export_catalog as catalog
from ai_edda.application import ApplicationService
from ai_edda.project_store import ProjectStore


def stage(value, content=None):
    value["wrapper"].mkdir(parents=True)
    (value["wrapper"] / "marker.json").write_bytes(marker(value) if content is None else content)


def test_no_final_or_stage_returns_false_without_creating_config(publication):
    assert not publication["config"].exists()
    assert recover(publication) == (False, None)
    assert recover(publication) == (False, None)
    assert not publication["config"].exists()


def test_actual_marker_fsync_failure_recovers_marker_only_stage(publication, monkeypatch):
    value = publication

    def fail(_):
        raise OSError("injected marker fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(catalog.os, "fsync", fail)
        with pytest.raises(OSError, match="marker fsync failure"):
            publish(value)
    assert (value["wrapper"] / "marker.json").read_bytes() == marker(value)
    assert not (value["wrapper"] / "export").exists() and not value["final"].exists()
    assert recover(value) == (False, None)
    assert not value["wrapper"].exists()
    assert publish(value) == value["record"]["public_result"]


def test_after_rename_cleanup_failure_recovers_without_source(publication, monkeypatch):
    value = publication

    def fail(*args):
        raise OSError("injected post-rename cleanup failure")

    with monkeypatch.context() as patch:
        patch.setattr(catalog, "_cleanup_native_wrapper", fail)
        with pytest.raises(OSError, match="post-rename"):
            publish(value)
    assert value["final"].is_dir() and (value["wrapper"] / "marker.json").is_file()
    assert not (value["wrapper"] / "export").exists()
    before = snapshot(value["final"])
    original = Path.open

    def no_source(path, *args, **kwargs):
        if path.is_relative_to(value["source"]) or path.is_relative_to(value["prepared"]):
            pytest.fail("Recovery read source or prepared files")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_source)
    assert recover(value) == (True, value["record"]["public_result"])
    assert recover(value) == (True, value["record"]["public_result"])
    assert not value["wrapper"].exists() and snapshot(value["final"]) == before


def test_later_real_project_commit_does_not_change_recovery(publication, case):
    value = publication
    publish(value)
    source, original, _ = case
    with ProjectStore(source.parent) as store:
        ApplicationService(store).execute(
            original.project.id,
            {
                "command_id": str(uuid4()),
                "kind": "update_song_metadata",
                "payload": {"title": "Later edit"},
                "base_project_revision": original.project.revision,
                "expected_grid_revision": original.grid.revision,
                "expected_chart_revisions": {chart.id: chart.revision for chart in original.charts},
            },
        )
    source_after = snapshot(source)
    stage(value)
    assert recover(value) == (True, value["record"]["public_result"])
    assert recover(value)[1]["project_revision"] == original.project.revision
    assert snapshot(source) == source_after


@pytest.mark.parametrize("limit_delta", [0, -1])
def test_actual_size_policy_is_checked_before_stage_cleanup(publication, monkeypatch, limit_delta):
    value = publication
    publish(value)
    stage(value)
    before = snapshot(value["final"])
    wrapper_before = snapshot(value["wrapper"])
    monkeypatch.setattr(
        catalog, "MAX_NATIVE_BACKUP_BYTES", value["result"]["archive_size"] + limit_delta
    )
    if limit_delta == 0:
        assert recover(value) == (True, value["record"]["public_result"])
        assert not value["wrapper"].exists()
    else:
        with pytest.raises(ValueError, match="exceeds allowed"):
            recover(value)
        assert snapshot(value["wrapper"]) == wrapper_before
    assert snapshot(value["final"]) == before


@pytest.mark.parametrize("corruption", ["same_size_archive", "record", "missing_file"])
def test_invalid_final_preserves_matching_stage(publication, corruption):
    value = publication
    publish(value)
    stage(value)
    if corruption == "same_size_archive":
        path = value["final"] / "backup.zip"
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    elif corruption == "record":
        (value["final"] / "publication.json").write_bytes(b"{}")
    else:
        (value["final"] / "backup.zip").unlink()
    before, wrapper = snapshot(value["final"]), snapshot(value["wrapper"])
    with pytest.raises(ValueError):
        recover(value)
    assert snapshot(value["final"]) == before and snapshot(value["wrapper"]) == wrapper


@pytest.mark.parametrize("contents", [b"", b"{", b"foreign marker"])
def test_unconfirmed_marker_is_preserved(publication, contents):
    stage(publication, contents)
    before = snapshot(publication["wrapper"])
    with pytest.raises(RuntimeError):
        recover(publication)
    assert snapshot(publication["wrapper"]) == before
    assert not publication["final"].exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
@pytest.mark.parametrize("where", ["wrapper", "export", "marker", "final"])
def test_recovery_refuses_junctions_without_deleting_targets(publication, tmp_path, where):
    value = publication
    external = tmp_path / "external-target"
    external.mkdir()
    (external / "sentinel").write_bytes(b"preserve")
    if where == "wrapper":
        value["parent"].mkdir(parents=True)
        link = value["wrapper"]
    else:
        stage(value)
        if where == "export":
            link = value["wrapper"] / "export"
        elif where == "marker":
            link = value["wrapper"] / "marker.json"
            link.unlink()
        else:
            link = value["final"]
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(external)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert link.is_junction()
    try:
        with pytest.raises((ValueError, RuntimeError)):
            recover(value)
        assert link.is_junction() and (external / "sentinel").read_bytes() == b"preserve"
        assert value["wrapper"].exists()
        assert not (external / "marker.json").exists()
    finally:
        link.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows drive layout")
def test_recovery_from_c_source_to_d_catalog_if_available(publication):
    base = Path.cwd().resolve()
    if publication["prepared"].resolve().drive.casefold() == base.drive.casefold():
        pytest.skip("source and repository are on the same drive")
    with tempfile.TemporaryDirectory(prefix="x-", dir=base) as temporary:
        config = Path(temporary).resolve()
        assert config.is_relative_to(base.resolve())
        assert publication["prepared"].resolve().drive.casefold() != config.drive.casefold()
        parent = config / "exports" / publication["result"]["project_id"]
        value = {
            **publication,
            "config": config,
            "parent": parent,
            "final": parent / publication["id"],
            "wrapper": parent / publication["wrapper"].name,
        }
        publish(value)
        stage(value)
        before = snapshot(value["final"])
        assert recover(value) == (True, value["record"]["public_result"])
        assert recover(value) == (True, value["record"]["public_result"])
        assert snapshot(value["final"]) == before and not value["wrapper"].exists()
