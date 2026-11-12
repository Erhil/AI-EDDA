import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from test_project_import_publication_review import (
    media as media,
)
from test_project_import_publication_review import (
    native_upload as native_upload,
)
from test_project_import_publication_review import (
    prepared as prepared,
)

from ai_edda import project_imports as imports
from ai_edda.application import ApplicationService
from ai_edda.import_receipts import ImportReceipt, build_import_receipt, publish_import_receipt
from ai_edda.project_store import ProjectStore, _json


def files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".writer.lock"
    }


@pytest.fixture
def absent(tmp_path):
    job = uuid4().hex
    request = {
        "request_id": str(uuid4()),
        "archive_kind": "native",
        "file_name": "source.zip",
        "file_size": 1,
    }
    record = build_import_receipt(request, job, "a" * 64, str(uuid4()), 0, "b" * 64, "c" * 64)
    with ProjectStore(tmp_path / "store") as store:
        yield {
            "store": store,
            "job_id": job,
            "context": {"request": request},
            "result": {"receipt": record.model_dump(mode="json")},
            "receipt": record.model_dump(mode="json"),
        }


def marker(value):
    return _json(
        {
            "version": 1,
            "job_id": value["job_id"],
            "project_id": value["receipt"]["project_id"],
            "receipt_sha256": hashlib.sha256(_json(value["receipt"])).hexdigest(),
        }
    )


def wrapper(value):
    return value["store"].root.resolve() / (".import-" + value["job_id"])


def destination(value):
    return value["store"].root / value["receipt"]["project_id"]


def partial(value):
    path = wrapper(value)
    path.mkdir()
    (path / "marker.json").write_bytes(marker(value))
    (path / "project").mkdir()
    (path / "project" / "partial").write_bytes(b"owned partial data")
    return path


def reconcile(value):
    return imports.reconcile_project_import(
        value["store"], value["job_id"], value["context"], value["result"]
    )


@pytest.mark.parametrize("prior", [False, True])
def test_no_publication_cleans_only_matching_wrapper_without_source_or_copy(
    absent, monkeypatch, prior
):
    value = absent
    if prior:
        value["context"]["prior_receipt"] = value["receipt"]
        value["job_id"] = uuid4().hex
    stage = partial(value)

    def forbidden(*args, **kwargs):
        raise AssertionError("reconciliation must not copy or validate a current snapshot")

    monkeypatch.setattr(imports, "capture_inventory", forbidden)
    monkeypatch.setattr(imports, "hash_inventory", forbidden)
    monkeypatch.setattr(imports, "validate_native_project", forbidden)
    monkeypatch.setattr(imports.os, "rename", forbidden)
    assert reconcile(value) == (False, None)
    assert not stage.exists() and not destination(value).exists()
    before = files(value["store"].root)
    assert reconcile(value) == (False, None)
    assert files(value["store"].root) == before


def test_actual_failure_before_rename_is_cleaned_without_republishing(prepared, monkeypatch):
    value = prepared

    def fail(*args):
        raise OSError("interrupted before rename")

    with monkeypatch.context() as patch:
        patch.setattr(imports.os, "rename", fail)
        patch.setattr(imports, "_cleanup_owned_wrapper", lambda *args: None)
        with pytest.raises(OSError, match="before rename"):
            imports.publish_project_import(
                value["store"], value["context"], value["result"], value["workspace"]
            )
    assert (wrapper(value) / "project" / "project.json").is_file()
    shutil.rmtree(value["workspace"])
    assert reconcile(value) == (False, None)
    assert not destination(value).exists() and not wrapper(value).exists()


@pytest.mark.parametrize("prior", [False, True])
def test_real_rename_lost_completion_then_later_edits_recovers_original_receipt(
    prepared, monkeypatch, prior
):
    value = prepared
    original_rename = imports.os.rename

    def lost_completion(source, target):
        original_rename(source, target)
        raise OSError("completion save lost")

    with monkeypatch.context() as patch:
        patch.setattr(imports.os, "rename", lost_completion)
        patch.setattr(imports, "_cleanup_owned_wrapper", lambda *args: None)
        with pytest.raises(OSError, match="completion save lost"):
            imports.publish_project_import(
                value["store"], value["context"], value["result"], value["workspace"]
            )
    assert destination(value).is_dir() and wrapper(value).is_dir()
    service = ApplicationService(value["store"])
    view = service.get(value["receipt"]["project_id"])
    later = service.execute(
        view.project.id,
        {
            "command_id": str(uuid4()),
            "kind": "update_song_metadata",
            "payload": {"title": "Later canonical edit"},
            "base_project_revision": view.project.revision,
            "expected_grid_revision": view.grid.revision,
            "expected_chart_revisions": {chart.id: chart.revision for chart in view.charts},
        },
    )
    shutil.rmtree(value["workspace"])
    if prior:
        # The previous operation's wrapper is cleaned separately; the verification job owns its own marker.
        imports._cleanup_owned_wrapper(value["store"], wrapper(value), marker(value))
        value["context"]["prior_receipt"] = value["receipt"]
        value["job_id"] = uuid4().hex
        partial(value)
    before = files(destination(value))
    original_open = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name != "project.json", "recovery must not read the current head"
        assert not path.is_relative_to(value["workspace"]), (
            "recovery must not read the missing workspace"
        )
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded)
        assert reconcile(value) == (True, value["receipt"]["public_result"])
    assert not wrapper(value).exists()
    assert files(destination(value)) == before
    assert service.get(later.project.id) == later
    assert value["receipt"]["public_result"]["revision"] < later.project.revision


def test_unrelated_destination_is_normal_false_and_is_never_removed(absent):
    value = absent
    destination(value).mkdir()
    (destination(value) / "foreign-data").write_bytes(b"keep")
    partial(value)
    before = files(destination(value))
    assert reconcile(value) == (False, None)
    assert not wrapper(value).exists()
    assert files(destination(value)) == before


@pytest.mark.parametrize("fault", ["json", "wrong-receipt", "coercion", "directory", "unreadable"])
def test_ambiguous_expected_receipt_raises_and_preserves_destination_and_wrapper(
    absent, monkeypatch, fault
):
    value = absent
    dest = destination(value)
    dest.mkdir()
    publish_import_receipt(dest, value["receipt"])
    path = dest / "imports" / (value["receipt"]["request_id"] + ".json")
    if fault == "json":
        path.write_bytes(b"{broken private data")
    elif fault == "wrong-receipt":
        path.write_bytes(_json({**value["receipt"], "job_id": uuid4().hex}))
    elif fault == "coercion":
        path.write_bytes(_json({**value["receipt"], "version": True}))
    elif fault == "directory":
        path.unlink()
        path.mkdir()
    partial(value)
    before = files(value["store"].root)
    original_open = Path.open

    def unreadable(item, *args, **kwargs):
        if item == path:
            raise PermissionError("inaccessible receipt")
        return original_open(item, *args, **kwargs)

    with monkeypatch.context() as patch:
        if fault == "unreadable":
            patch.setattr(Path, "open", unreadable)
        with pytest.raises(RuntimeError, match="recovery required"):
            reconcile(value)
    assert files(value["store"].root) == before


@pytest.mark.parametrize("fault", ["foreign", "missing", "marker-directory"])
def test_ambiguous_wrapper_is_preserved_even_with_exact_destination_receipt(absent, fault):
    value = absent
    dest = destination(value)
    dest.mkdir()
    publish_import_receipt(dest, value["receipt"])
    stage = partial(value)
    marker_path = stage / "marker.json"
    if fault == "foreign":
        marker_path.write_bytes(b"foreign marker")
    else:
        marker_path.unlink()
        if fault == "marker-directory":
            marker_path.mkdir()
    before = files(value["store"].root)
    with pytest.raises(RuntimeError, match="recovery required"):
        reconcile(value)
    assert files(value["store"].root) == before


@pytest.mark.parametrize(
    "fault", ["job", "fresh-job-mismatch", "context", "result", "unchecked-model", "prior-mismatch"]
)
def test_invalid_job_context_result_never_cleans_owned_data(absent, fault):
    value = absent
    partial(value)
    if fault == "job":
        value["job_id"] = "NOT-A-JOB"
    elif fault == "fresh-job-mismatch":
        value["result"]["receipt"] = {**value["receipt"], "job_id": uuid4().hex}
    elif fault == "context":
        value["context"]["extra"] = True
    elif fault == "result":
        value["result"]["extra"] = True
    elif fault == "unchecked-model":
        value["result"]["receipt"] = ImportReceipt.model_validate(value["receipt"]).model_copy(
            update={"version": True}
        )
    else:
        value["context"]["prior_receipt"] = {**value["receipt"], "job_id": uuid4().hex}
    before = files(value["store"].root)
    with pytest.raises(ValueError):
        reconcile(value)
    assert files(value["store"].root) == before


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
@pytest.mark.parametrize("location", ["destination", "wrapper", "wrapper-project"])
@pytest.mark.parametrize("dangling", [False, True])
def test_windows_reparse_entries_are_never_followed_or_removed(
    absent, tmp_path, location, dangling
):
    value = absent
    target = value["store"].root / ".external-target"
    target.mkdir()
    (target / "marker.json").write_bytes(marker(value))
    (target / "sentinel").write_bytes(b"keep")
    if location == "destination":
        link = destination(value)
        partial(value)
    elif location == "wrapper":
        link = wrapper(value)
    else:
        partial(value)
        (wrapper(value) / "project" / "partial").unlink()
        (wrapper(value) / "project").rmdir()
        link = wrapper(value) / "project"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if dangling:
        (target / "marker.json").unlink()
        (target / "sentinel").unlink()
        target.rmdir()
    try:
        with pytest.raises((RuntimeError, ValueError, OSError)):
            reconcile(value)
        assert link.lstat().st_file_attributes & 0x400
        assert wrapper(value).lstat()
        if not dangling:
            assert (target / "sentinel").read_bytes() == b"keep"
    finally:
        link.rmdir()


def test_recovery_on_store_volume_does_not_need_original_workspace_volume(prepared):
    value = prepared
    scratch = Path(__file__).resolve().parents[2] / "src" / "scratch" / "github-preparation"
    scratch.mkdir(parents=True, exist_ok=True)
    assert scratch.resolve().is_relative_to(Path(__file__).resolve().parents[2])
    if scratch.drive.lower() == value["workspace"].drive.lower():
        pytest.skip("no second test volume is available")
    with tempfile.TemporaryDirectory(prefix="import-recovery-volume-", dir=scratch) as directory:
        assert Path(directory).resolve().is_relative_to(scratch.resolve())
        with ProjectStore(Path(directory) / "store") as store:
            imports.publish_project_import(
                store, value["context"], value["result"], value["workspace"]
            )
            shutil.rmtree(value["workspace"])
            assert imports.reconcile_project_import(
                store, value["job_id"], value["context"], value["result"]
            ) == (True, value["receipt"]["public_result"])
