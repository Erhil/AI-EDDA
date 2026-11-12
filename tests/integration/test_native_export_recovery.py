import shutil

import pytest
from test_native_export_publication import publication as publication
from test_native_export_publication import publish
from test_native_export_publication import snapshot as snapshot
from test_native_export_worker import case as case

from ai_edda import native_export_catalog as catalog
from ai_edda.project_store import _json


def recover(value):
    return catalog.reconcile_native_export(
        value["config"], value["id"], "native-job", value["context"], value["result"]
    )


def marker(value):
    return _json(
        {
            "version": 1,
            "project_id": value["result"]["project_id"],
            "export_id": value["id"],
            "job_id": "native-job",
            "record_sha256": catalog._hash(value["record"]),
        }
    )


def test_partial_stage_and_repeated_recovery(publication):
    value = publication
    value["wrapper"].mkdir(parents=True)
    (value["wrapper"] / "marker.json").write_bytes(marker(value))
    (value["wrapper"] / "export").mkdir()
    assert recover(value) == (False, None)
    assert recover(value) == (False, None)


def test_published_recovery_does_not_need_source(publication):
    value = publication
    publish(value)
    value["wrapper"].mkdir()
    (value["wrapper"] / "marker.json").write_bytes(marker(value))
    shutil.rmtree(value["source"])
    shutil.rmtree(value["prepared"])
    assert recover(value) == (True, value["record"]["public_result"])
    assert recover(value) == (True, value["record"]["public_result"])


def test_foreign_marker_is_preserved(publication):
    value = publication
    value["wrapper"].mkdir(parents=True)
    (value["wrapper"] / "marker.json").write_bytes(b"foreign")
    before = snapshot(value["wrapper"])
    with pytest.raises(RuntimeError):
        recover(value)
    assert snapshot(value["wrapper"]) == before


def test_final_conflict_preserves_wrapper(publication):
    value = publication
    publish(value)
    value["wrapper"].mkdir()
    (value["wrapper"] / "marker.json").write_bytes(marker(value))
    (value["final"] / "backup.zip").write_bytes(b"bad")
    before = snapshot(value["wrapper"])
    with pytest.raises(ValueError):
        recover(value)
    assert snapshot(value["wrapper"]) == before
