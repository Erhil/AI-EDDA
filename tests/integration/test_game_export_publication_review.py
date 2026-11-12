"""Independent publication, replay, failure, and immutable identity checks."""

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from test_package_builder import snapshot as snapshot

from ai_edda.application import ApplicationService
from ai_edda.domain import SongProject
from ai_edda.game_export_catalog import (
    publish_game_export,
    reconcile_game_export,
    resolve_game_export,
    verify_game_export,
)
from ai_edda.packaging import ArtifactBuilder
from ai_edda.project_store import ProjectStore


@pytest.fixture
def publication(tmp_path, snapshot):
    export_id = str(uuid4())
    value = replace(snapshot(2), snapshot_id=export_id)
    with ProjectStore(tmp_path / "store") as store:
        state = store.create(
            SongProject(metadata=value.metadata), value.grid, value.selected_charts
        )
        project = store._dir(state.project.id)
    source = tmp_path / "source"
    ArtifactBuilder(tmp_path).build(value, source, "song")
    result = verify_game_export(source, "song")
    return {
        "project": project,
        "source": source,
        "export_id": export_id,
        "result": result,
        "context": {"project_id": state.project.id, "export_id": export_id, "project_revision": 0},
    }


def publish(value):
    return publish_game_export(
        value["project"],
        value["export_id"],
        "job",
        value["context"],
        value["result"],
        value["source"],
    )


def final(value):
    return value["project"] / "exports" / value["export_id"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_real_project_identity_and_retry_after_later_edits(publication):
    value = publication
    head = value["project"] / "project.json"
    before = head.read_bytes()
    public = publish(value)
    assert head.read_bytes() == before
    with ProjectStore(value["project"].parent) as store:
        app = ApplicationService(store)
        view = app.get(value["context"]["project_id"])
        app.execute(
            view.project.id,
            {
                "command_id": "later-edit",
                "kind": "update_song_metadata",
                "payload": {"title": "changed"},
                "base_project_revision": view.project.revision,
                "expected_grid_revision": view.grid.revision,
                "expected_chart_revisions": {c.id: c.revision for c in view.charts},
            },
        )
    edited = head.read_bytes()
    value["source"] = value["source"].with_name("source-is-no-longer-available")
    assert publish(value) == public
    assert reconcile_game_export(
        value["project"], value["export_id"], "job", value["context"], value["result"]
    ) == (True, public)
    assert head.read_bytes() == edited
    assert public["project_revision"] == 0


def test_wrong_real_project_id_is_rejected_before_publication(publication):
    publication["context"]["project_id"] = str(uuid4())
    with pytest.raises(ValueError, match="Project"):
        publish(publication)
    assert not final(publication).exists()


def test_full_caller_context_is_hashed_and_required_for_reconcile(publication):
    publication["context"]["selection"] = ["caller-captured-selection"]
    public = publish(publication)
    stored = resolve_game_export(publication["project"], publication["export_id"])
    canonical = json.dumps(
        publication["context"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert stored["context_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert reconcile_game_export(
        publication["project"],
        publication["export_id"],
        "job",
        publication["context"],
        publication["result"],
    ) == (True, public)
    changed = {**publication["context"], "selection": ["different"]}
    with pytest.raises(ValueError, match="conflict"):
        reconcile_game_export(
            publication["project"], publication["export_id"], "job", changed, publication["result"]
        )


@pytest.mark.parametrize("location", ["grid_revision", "member_size"])
def test_copied_result_metadata_cannot_coerce(publication, location):
    result = publication["result"]
    if location == "grid_revision":
        result["receipt"]["grid_revision"] = False
    else:
        entry = result["receipt"]["members"]["info.dat"]
        entry["size"] = float(entry["size"])
    with pytest.raises(ValueError):
        publish(publication)
    assert not final(publication).exists()


@pytest.mark.parametrize(
    "field, replacement",
    [("version", True), ("context_sha256", "f" * 64), ("result_sha256", "f" * 64), ("job_id", "")],
)
def test_publication_schema_and_context_digests_are_verified(publication, field, replacement):
    publish(publication)
    path = final(publication) / "publication.json"
    value = read(path)
    value[field] = replacement
    write(path, value)
    if field == "context_sha256":
        # Full caller context is not stored. Resolver checks hash shape; trusted reconcile checks meaning.
        assert (
            resolve_game_export(publication["project"], publication["export_id"])["context_sha256"]
            == replacement
        )
        with pytest.raises(ValueError, match="conflict"):
            reconcile_game_export(
                publication["project"],
                publication["export_id"],
                "job",
                publication["context"],
                publication["result"],
            )
        return
    with pytest.raises(ValueError):
        resolve_game_export(publication["project"], publication["export_id"])


@pytest.mark.parametrize("field", ["revision", "site_check"])
def test_public_result_types_are_strict(publication, field):
    publish(publication)
    path = final(publication) / "publication.json"
    value = read(path)
    if field == "revision":
        value["public_result"]["project_revision"] = False
    else:
        value["public_result"]["site_check"]["passed"] = 1
    write(path, value)
    with pytest.raises(ValueError):
        resolve_game_export(publication["project"], publication["export_id"])


def test_foreign_project_publication_cannot_be_rebound_in_place(publication):
    publish(publication)
    path = final(publication) / "publication.json"
    value = read(path)
    value["project_id"] = value["public_result"]["project_id"] = str(uuid4())
    context = {**publication["context"], "project_id": value["project_id"]}
    canonical = json.dumps(
        context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    value["context_sha256"] = hashlib.sha256(canonical).hexdigest()
    write(path, value)
    with pytest.raises(ValueError):
        resolve_game_export(publication["project"], publication["export_id"])


def test_postrename_failure_reconciles_without_republishing(publication, monkeypatch):
    import ai_edda.game_export_catalog as module

    original = module.os.rename

    def rename_then_fail(source, destination):
        original(source, destination)
        raise OSError("injected response failure after rename")

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "rename", rename_then_fail)
        with pytest.raises(OSError, match="after rename"):
            publish(publication)
    assert final(publication).is_dir()
    found, public = reconcile_game_export(
        publication["project"],
        publication["export_id"],
        "job",
        publication["context"],
        publication["result"],
    )
    assert found
    assert publish(publication) == public
    assert list(final(publication).parent.iterdir()) == [final(publication)]


def test_preexisting_destination_is_never_cleaned(publication):
    destination = final(publication)
    destination.mkdir(parents=True)
    marker = destination / "unrelated-owner.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        publish(publication)
    assert marker.read_text(encoding="utf-8") == "keep"
    assert list(destination.iterdir()) == [marker]


@pytest.mark.skipif(
    os.name != "nt", reason="This publication contract targets Windows no-replacement rename"
)
def test_destination_appearing_before_rename_is_preserved(publication, monkeypatch):
    import ai_edda.game_export_catalog as module

    original = module.os.rename

    def competing_rename(source, destination):
        destination.mkdir()
        (destination / "competitor.txt").write_text("keep", encoding="utf-8")
        original(source, destination)

    monkeypatch.setattr(module.os, "rename", competing_rename)
    with pytest.raises(OSError):
        publish(publication)
    assert (final(publication) / "competitor.txt").read_text(encoding="utf-8") == "keep"
    assert list(final(publication).parent.iterdir()) == [final(publication)]


def test_changed_prepared_source_is_rejected_by_copied_stage_verification(publication, monkeypatch):
    import ai_edda.game_export_catalog as module

    original = module.verify_game_export
    changed = []

    def verify_then_mutate(directory, name, *args, **kwargs):
        result = original(directory, name, *args, **kwargs)
        if Path(directory) == publication["source"] and not changed:
            changed.append(True)
            with (Path(directory) / name / "info.dat").open("ab") as stream:
                stream.write(b" ")
        return result

    monkeypatch.setattr(module, "verify_game_export", verify_then_mutate)
    with pytest.raises(ValueError):
        publish(publication)
    assert not final(publication).exists()
    assert not list((publication["project"] / "exports").iterdir())


def test_copy_reads_are_bounded_and_all_written_files_are_fsynced(publication, monkeypatch):
    import ai_edda.game_export_catalog as module

    original_open = Path.open
    original_fsync = module.os.fsync
    reads, flushed, written_paths = [], [], {}

    class Tracked:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size=-1):
            assert 0 < size <= 1024**2
            reads.append(size)
            return self.stream.read(size)

    def opened(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if args == ("xb",):
            written_paths[stream.fileno()] = path
        return (
            Tracked(stream)
            if path.is_relative_to(publication["source"]) and args == ("rb",)
            else stream
        )

    def fsync(fd):
        flushed.append(written_paths[fd])
        return original_fsync(fd)

    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(module.os, "fsync", fsync)
    publish(publication)
    assert reads
    assert len(flushed) == len(publication["result"]["receipt"]["members"]) + 4
    assert len(set(flushed)) == len(flushed)
    assert flushed[0].name == "marker.json"
    wrapper = flushed[0].parent
    assert wrapper.parent == publication["project"].parent
    assert all(path.is_relative_to(wrapper / "export") for path in flushed[1:])
    assert {path.name for path in flushed[1:]} == set(
        publication["result"]["receipt"]["members"]
    ) | {"song.zip", "song.receipt.json", "publication.json"}
