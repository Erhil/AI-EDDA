"""Owned sibling recovery: strict markers, Windows junctions, and evidence retention."""

import os
import subprocess
from pathlib import Path

import pytest
from test_game_export_publication_review import (
    final,
    publish,
    read,
    write,
)
from test_game_export_publication_review import (
    publication as publication,
)
from test_package_builder import snapshot as snapshot

from ai_edda import game_export_catalog as catalog
from ai_edda.native_capture import capture_inventory


def marker(value):
    return {
        "version": 1,
        "project_id": value["context"]["project_id"],
        "export_id": value["export_id"],
        "job_id": "job",
        "context_sha256": catalog._hash(value["context"]),
        "result_sha256": catalog._hash(value["result"]),
    }


def wrapper(value):
    return catalog._game_stage_path(
        value["project"], value["context"]["project_id"], value["export_id"], "job"
    )


def reconcile(value):
    return catalog.reconcile_game_export(
        value["project"], value["export_id"], "job", value["context"], value["result"]
    )


def make_stage(value, payload=True):
    path = wrapper(value)
    path.mkdir()
    write(path / "marker.json", marker(value))
    if payload:
        (path / "export").mkdir()
        (path / "export" / "sentinel.txt").write_text("keep", encoding="utf-8")
    return path


def test_marker_only_owned_stage_can_be_recovered_without_inventory_changes(publication):
    project = publication["project"]
    head = (project / "project.json").read_bytes()
    before = capture_inventory(project, publication["context"]["project_id"], head)
    path = make_stage(publication, payload=False)
    assert capture_inventory(project, publication["context"]["project_id"], head) == before
    assert reconcile(publication) == (False, None)
    assert not path.exists()
    assert capture_inventory(project, publication["context"]["project_id"], head) == before


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("version", True),
        ("version", 1.0),
        ("project_id", "foreign"),
        ("export_id", "foreign"),
        ("job_id", "other"),
        ("context_sha256", "A" * 64),
        ("result_sha256", "0" * 64),
        ("project_name", "legacy-marker-field"),
    ],
)
def test_marker_requires_exact_typed_identity_before_cleanup(publication, field, replacement):
    path = make_stage(publication)
    value = marker(publication)
    value[field] = replacement
    write(path / "marker.json", value)
    before = {p: p.read_bytes() for p in path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        reconcile(publication)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("change", ["missing", "oversized", "unreadable"])
def test_ambiguous_marker_is_preserved(publication, monkeypatch, change):
    path = make_stage(publication)
    marker_path = path / "marker.json"
    if change == "missing":
        marker_path.unlink()
    elif change == "oversized":
        with marker_path.open("ab") as output:
            output.write(b" " * 10000)
    else:
        original = Path.open

        def opened(item, *args, **kwargs):
            if item == marker_path:
                raise PermissionError("ownership marker unavailable")
            return original(item, *args, **kwargs)

        monkeypatch.setattr(Path, "open", opened)
    with pytest.raises((ValueError, OSError)):
        reconcile(publication)
    assert (path / "export" / "sentinel.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("conflict", ["job", "invalid_receipt"])
def test_final_conflict_preserves_matching_wrapper_evidence(publication, conflict):
    publish(publication)
    path = make_stage(publication)
    receipt_path = final(publication) / "publication.json"
    value = read(receipt_path)
    value["job_id" if conflict == "job" else "version"] = "other" if conflict == "job" else True
    write(receipt_path, value)
    before = {p: p.read_bytes() for p in path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        reconcile(publication)
    assert {p: p.read_bytes() for p in before} == before
    assert final(publication).is_dir()


def test_unknown_old_random_stage_is_not_removed_or_hidden(publication):
    project = publication["project"]
    old = project / "exports" / ".game-export-old-random"
    old.mkdir(parents=True)
    sentinel = old / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    assert reconcile(publication) == (False, None)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    with pytest.raises(ValueError, match="Unknown native path"):
        capture_inventory(
            project, publication["context"]["project_id"], (project / "project.json").read_bytes()
        )


def test_marker_fsync_failure_cleans_only_proven_owned_wrapper(publication, monkeypatch):
    error = OSError("marker fsync failed")

    def fsync(fd):
        raise error

    monkeypatch.setattr(catalog.os, "fsync", fsync)
    with pytest.raises(OSError) as caught:
        publish(publication)
    assert caught.value is error
    assert not wrapper(publication).exists()
    assert not final(publication).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction containment")
@pytest.mark.parametrize("location", ["wrapper", "marker", "payload", "ancestor"])
def test_real_windows_junctions_are_rejected_before_marker_reads(
    publication, monkeypatch, location
):
    path = wrapper(publication)
    outside = publication["project"].parent / "junction-target"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("external keep", encoding="utf-8")
    if location == "wrapper":
        link, target = path, outside
        write(outside / "marker.json", marker(publication))
    elif location == "marker":
        path.mkdir()
        link, target = path / "marker.json", outside
    elif location == "payload":
        make_stage(publication, payload=False)
        link, target = path / "export", outside
    else:
        make_stage(publication)
        link, target = (
            publication["project"].parent / "junction-parent",
            publication["project"].parent,
        )
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    original = Path.open

    def opened(item, *args, **kwargs):
        assert item.name != "marker.json", (
            "Ownership marker was opened through unsafe recovery data"
        )
        return original(item, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    try:
        with pytest.raises(ValueError):
            if location == "ancestor":
                aliased = link / path.name
                catalog._cleanup_game_stage(
                    aliased,
                    link,
                    publication["context"]["project_id"],
                    publication["export_id"],
                    "job",
                    marker(publication)["context_sha256"],
                    marker(publication)["result_sha256"],
                )
            else:
                reconcile(publication)
        assert sentinel.read_text(encoding="utf-8") == "external keep"
    finally:
        # The fixture owns the junction entry. rmdir removes its entry, never its target.
        link.rmdir()
