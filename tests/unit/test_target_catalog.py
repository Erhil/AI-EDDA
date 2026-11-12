import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from ai_edda import target_catalog
from ai_edda.arrangement_domain import TargetProfile
from ai_edda.domain import ObjectRef
from ai_edda.target_catalog import publish_target, resolve_target


def test_publish_resolve_retry_conflict(tmp_path):
    target = {
        "id": str(uuid.uuid4()),
        "version": 1,
        "name": "test",
        "movement_profile": "free",
        "allowed_subdivisions": [2, 4],
    }
    ref = publish_target(tmp_path, target)
    assert resolve_target(tmp_path, ref).movement_profile == "free"
    assert publish_target(tmp_path, target) == ref
    with pytest.raises(ValueError):
        publish_target(tmp_path, {**target, "name": "other"})
    assert resolve_target(tmp_path, ref).name == "test"


def test_invalid_and_corrupt(tmp_path):
    target = {"id": str(uuid.uuid4()), "version": 1, "name": "test"}
    for change in ({"id": "../bad"}, {"version": True}, {"syncopation_budget": True}):
        with pytest.raises(ValueError):
            publish_target(tmp_path, {**target, **change})
    with pytest.raises(FileNotFoundError):
        resolve_target(tmp_path, {"id": target["id"], "revision": 1})
    ref = publish_target(tmp_path, target)
    path = tmp_path / "targets" / f"{target['id']}-1.json"
    path.write_text(json.dumps({**target, "version": 2}))
    with pytest.raises(ValueError):
        resolve_target(tmp_path, ref)


def _profile(**changes):
    return {"id": str(uuid.uuid4()), "version": 1, "name": "test", **changes}


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "../bad"},
        {"id": str(uuid.uuid4()).upper()},
        {"version": True},
        {"version": "1"},
        {"version": 1.0},
        {"version": 0},
        {"intended_rank": True},
        {"max_hits_in_window": True, "window_sec": 1},
        {"window_sec": True, "max_hits_in_window": 1},
        {"max_same_hand_burst": 1.0},
        {"max_simultaneous_notes": 3},
        {"allowed_subdivisions": [2, True]},
        {"allowed_subdivisions": ["4"]},
        {"allowed_subdivisions": [0]},
        {"syncopation_budget": True},
        {"ornament_budget": "0.2"},
        {"target_hits_per_second": float("inf")},
        {"transition_complexity": float("nan")},
        {"double_event_fraction": -0.1},
        {"min_same_hand_interval_sec": -1},
        {"motif_consistency": 1.1},
        {"max_hits_in_window": 2},
        {"unknown": 1},
    ],
)
def test_rejects_invalid_profile_before_creating_files(tmp_path, changes):
    with pytest.raises(ValueError):
        publish_target(tmp_path, _profile(**changes))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"intended_rank": True},
        {"allowed_subdivisions": (True,)},
        {"syncopation_budget": True},
        {"max_hits_in_window": 2},
    ],
)
def test_revalidates_constructed_profiles(tmp_path, changes):
    record = TargetProfile.model_construct(**_profile(**changes))
    with pytest.raises(ValueError):
        publish_target(tmp_path, record)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("revision", [True, "1", 1.0, 0, -1])
@pytest.mark.parametrize("as_model", [False, True])
def test_rejects_invalid_references(tmp_path, revision, as_model):
    ref = {"id": str(uuid.uuid4()), "revision": revision}
    if as_model:
        ref = ObjectRef.model_construct(**ref)
    with pytest.raises(ValueError):
        resolve_target(tmp_path, ref)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": str(uuid.uuid4())},
        {"version": 2},
        {"version": True},
        {"allowed_subdivisions": [True]},
        {"target_hits_per_second": "2"},
        {"window_sec": 1},
        {"extra": 1},
    ],
)
def test_resolve_rejects_corrupt_profile(tmp_path, changes):
    record = _profile()
    ref = publish_target(tmp_path, record)
    path = tmp_path / "targets" / f"{record['id']}-1.json"
    path.write_text(json.dumps({**record, **changes}), encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_target(tmp_path, ref)


def test_preserves_all_controls_and_independent_versions(tmp_path):
    record = _profile(
        intended_rank=0,
        allowed_subdivisions=[2, 3, 4],
        syncopation_budget=0.5,
        ornament_budget=0.2,
        target_hits_per_second=3,
        max_hits_in_window=4,
        window_sec=2,
        double_event_fraction=0.1,
        max_simultaneous_notes=2,
        min_same_hand_interval_sec=0.1,
        max_same_hand_burst=3,
        movement_profile="future-generator-movement",
        transition_complexity=0.8,
        recovery_policy="future-policy",
        accent_preference="future-accent",
        motif_consistency=0.9,
        style_id="future-style",
    )
    first = publish_target(tmp_path, record)
    second = publish_target(tmp_path, {**record, "version": 2, "name": "updated"})
    assert first.revision == 1 and second.revision == 2
    assert resolve_target(tmp_path, first) == TargetProfile.model_validate(record)
    assert resolve_target(tmp_path, second).name == "updated"
    assert publish_target(tmp_path, TargetProfile.model_validate(record)) == first
    assert not list((tmp_path / "targets").glob(".staging-*"))


@pytest.mark.parametrize("failure_point", ["fsync", "link"])
def test_failed_write_preserves_published_version_and_cleans_staging(
    tmp_path, monkeypatch, failure_point
):
    record = _profile()
    ref = publish_target(tmp_path, record)
    path = tmp_path / "targets" / f"{record['id']}-1.json"
    original = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("injected failure")

    with monkeypatch.context() as patch:
        patch.setattr(target_catalog.os, failure_point, fail)
        with pytest.raises(OSError, match="injected failure"):
            publish_target(tmp_path, {**record, "version": 2})
    assert path.read_bytes() == original
    assert list((tmp_path / "targets").iterdir()) == [path]
    assert resolve_target(tmp_path, ref).name == record["name"]


def test_flushes_file_before_atomic_publish(tmp_path, monkeypatch):
    original_fsync, original_link = os.fsync, os.link
    calls = []

    def fsync(fd):
        calls.append("fsync")
        return original_fsync(fd)

    def link(source, destination):
        assert calls == ["fsync"]
        assert not destination.exists()
        assert json.loads(Path(source).read_bytes())["name"] == "test"
        calls.append("link")
        return original_link(source, destination)

    monkeypatch.setattr(target_catalog.os, "fsync", fsync)
    monkeypatch.setattr(target_catalog.os, "link", link)
    publish_target(tmp_path, _profile())
    assert calls == ["fsync", "link"]


@pytest.mark.parametrize("contents", [b"{broken", b"[]", b"null", b'{"unexpected": true}'])
def test_corrupt_file_is_never_replaced_by_retry(tmp_path, contents):
    record = _profile()
    ref = publish_target(tmp_path, record)
    path = tmp_path / "targets" / f"{record['id']}-1.json"
    path.write_bytes(contents)
    with pytest.raises(ValueError):
        resolve_target(tmp_path, ref)
    with pytest.raises(ValueError, match="conflict"):
        publish_target(tmp_path, record)
    assert path.read_bytes() == contents
    assert not list(path.parent.glob(".staging-*"))


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junctions")
@pytest.mark.parametrize("junction_is_project", [False, True])
def test_windows_junction_cannot_redirect_catalog(tmp_path, junction_is_project):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    if junction_is_project:
        junction = project
    else:
        project.mkdir()
        junction = project / "targets"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        record = _profile()
        with pytest.raises(ValueError, match="Unsafe path"):
            publish_target(project, record)
        with pytest.raises(ValueError, match="Unsafe path"):
            resolve_target(project, {"id": record["id"], "revision": 1})
        assert list(outside.iterdir()) == []
    finally:
        junction.rmdir()
