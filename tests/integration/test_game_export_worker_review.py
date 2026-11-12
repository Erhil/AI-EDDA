"""Frozen worker payloads, actual artifacts, and cancellation boundaries."""

import json
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
import test_game_export_worker as _worker_test

from ai_edda.game_export_catalog import verify_game_export
from ai_edda.game_export_worker import game_export_worker
from ai_edda.packaging import ArtifactBuilder

basic_payload = _worker_test.payload


@pytest.fixture
def payload(basic_payload):
    # Coordinator payloads are plain JSON, including nested domain objects.
    return json.loads(
        json.dumps(basic_payload, default=lambda value: value.model_dump(mode="json"))
    )


@pytest.mark.parametrize("count", [1, 2, 3])
def test_actual_json_snapshot_builds_once_and_preserves_inputs(payload, monkeypatch, count):
    project = Path(payload["project_dir"])
    (project / "project.json").write_text("head is intentionally unreadable", encoding="utf-8")
    original = payload["snapshot"]["selected_charts"][0]
    charts = []
    for index in range(count):
        chart = json.loads(json.dumps(original))
        chart["id"] = str(uuid4())
        chart["metadata"]["rank"] = count - index
        charts.append(chart)
    payload["snapshot"]["selected_charts"] = charts
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    original_build = ArtifactBuilder.build
    calls, progress = [], []

    def build(self, snapshot, *args, **kwargs):
        calls.append(snapshot)
        return original_build(self, snapshot, *args, **kwargs)

    monkeypatch.setattr(ArtifactBuilder, "build", build)
    result = game_export_worker(payload, progress.append, Event())
    prepared = Path(payload["workspace"]) / "prepared"
    assert result == verify_game_export(prepared, "song")
    assert len(calls) == 1
    assert len(result["receipt"]["selection"]) == count
    assert progress == [0.1, 0.8, 1.0]
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == before
    assert not (project / "exports").exists()


@pytest.mark.parametrize("document", [None, [], {}, {"metadata": {}}, "invalid"])
def test_snapshot_shape_errors_are_validation_errors(payload, document):
    payload["snapshot"] = document
    with pytest.raises(ValueError):
        game_export_worker(payload, lambda _: None, Event())
    assert not (Path(payload["workspace"]) / "prepared").exists()


def test_snapshot_rejects_additional_fields_before_build(payload, monkeypatch):
    payload["snapshot"]["live_revision"] = 99

    def build(*args, **kwargs):
        pytest.fail("Malformed snapshot reached ArtifactBuilder")

    monkeypatch.setattr(ArtifactBuilder, "build", build)
    with pytest.raises(ValueError):
        game_export_worker(payload, lambda _: None, Event())
    assert not (Path(payload["workspace"]) / "prepared").exists()


@pytest.mark.parametrize("field", ["project_dir", "workspace"])
def test_relative_payload_paths_are_rejected(payload, field):
    payload[field] = "relative"
    with pytest.raises(ValueError, match="absolute"):
        game_export_worker(payload, lambda _: None, Event())


@pytest.mark.parametrize("folder", ["../escape", "Invalid", "nested/name", "", None])
def test_invalid_folder_names_cannot_publish_any_artifact(payload, folder):
    payload["folder_name"] = folder
    with pytest.raises((ValueError, TypeError)):
        game_export_worker(payload, lambda _: None, Event())
    assert not list((Path(payload["workspace"]) / "prepared").iterdir())


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_preexisting_prepared_and_unrelated_files_survive(payload, kind):
    workspace = Path(payload["workspace"])
    prepared = workspace / "prepared"
    marker = workspace / "unrelated.txt"
    marker.write_text("keep", encoding="utf-8")
    if kind == "directory":
        prepared.mkdir()
        (prepared / "keep.txt").write_text("existing", encoding="utf-8")
    else:
        prepared.write_text("existing", encoding="utf-8")
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        game_export_worker(payload, lambda _: None, Event())
    assert {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "stage", ["initial", "progress_initial", "build", "progress_built", "verify", "progress_final"]
)
def test_cancellation_is_checked_at_each_boundary(payload, monkeypatch, stage):
    import ai_edda.game_export_worker as module

    cancel = Event()
    if stage == "initial":
        cancel.set()
    original_build, original_verify = ArtifactBuilder.build, module.verify_game_export
    built, verified = [], []

    def build(*args, **kwargs):
        built.append(True)
        result = original_build(*args, **kwargs)
        if stage == "build":
            cancel.set()
        return result

    def verify(*args, **kwargs):
        verified.append(True)
        assert callable(kwargs["cancel"])
        result = original_verify(*args, **kwargs)
        if stage == "verify":
            cancel.set()
        return result

    def progress(value):
        if (stage, value) in (
            ("progress_initial", 0.1),
            ("progress_built", 0.8),
            ("progress_final", 1.0),
        ):
            cancel.set()

    monkeypatch.setattr(ArtifactBuilder, "build", build)
    monkeypatch.setattr(module, "verify_game_export", verify)
    with pytest.raises(RuntimeError, match="cancelled"):
        game_export_worker(payload, progress, cancel)
    assert len(built) == (0 if stage in ("initial", "progress_initial") else 1)
    assert len(verified) == (1 if stage in ("verify", "progress_final") else 0)


def test_verifier_receives_raising_cancellation_callback(payload, monkeypatch):
    import ai_edda.game_export_worker as module

    cancel = Event()

    def verify(*args, **kwargs):
        cancel.set()
        kwargs["cancel"]()
        pytest.fail("Cancellation callback returned")

    monkeypatch.setattr(module, "verify_game_export", verify)
    with pytest.raises(RuntimeError, match="cancelled"):
        game_export_worker(payload, lambda _: None, cancel)


def test_builder_failure_does_not_retry_or_remove_unrelated_files(payload, monkeypatch):
    workspace = Path(payload["workspace"])
    marker = workspace / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    calls = []
    error = OSError("builder failed")

    def build(*args, **kwargs):
        calls.append(True)
        raise error

    monkeypatch.setattr(ArtifactBuilder, "build", build)
    with pytest.raises(OSError) as caught:
        game_export_worker(payload, lambda _: None, Event())
    assert caught.value is error
    assert calls == [True]
    assert marker.read_text(encoding="utf-8") == "keep"


def test_verifier_failure_does_not_return_unverified_result(payload, monkeypatch):
    import ai_edda.game_export_worker as module

    error = ValueError("verification failed")

    def verify(*args, **kwargs):
        raise error

    monkeypatch.setattr(module, "verify_game_export", verify)
    with pytest.raises(ValueError) as caught:
        game_export_worker(payload, lambda _: None, Event())
    assert caught.value is error
