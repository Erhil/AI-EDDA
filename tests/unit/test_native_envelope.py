import json

import pytest

from ai_edda.native_envelope import parse_native_envelope, serialize_native_envelope

ID = "12345678-1234-1234-1234-123456789abc"


def payload(paths=("project.json",)):
    return {
        "archive_version": 1,
        "schema_version": 1,
        "project_id": ID,
        "files": [{"path": p, "size": 0, "sha256": "a" * 64} for p in paths],
    }


def parse(obj):
    return parse_native_envelope(json.dumps(obj).encode())


def test_roundtrip():
    paths = [
        "project.json",
        "grid-0.json",
        f"chart-{ID}-0.json",
        "receipt-ab.json",
        f"audio/{ID}/song.ogg",
        f"assets/{ID}/cover.jpg",
        f"analysis/{ID}/beat/beats.npz",
        f"generation/{ID}/draft.json",
        f"plan-inputs/{ID}.json",
        f"plans/{ID}.json",
        f"imports/{ID}.json",
        f"exports/{ID}/publication.json",
    ]
    value = parse(payload(paths))
    assert parse_native_envelope(serialize_native_envelope(value)) == value


def test_target_paths():
    assert parse(payload([f"targets/{ID}-1.json"]))
    for path in (
        f"targets/{ID}-0.json",
        f"targets/{ID}--1.json",
        f"targets/{ID}-1.5.json",
        "targets/arbitrary.json",
    ):
        with pytest.raises(ValueError):
            parse(payload([path]))


@pytest.mark.parametrize(
    "field,value",
    [("archive_version", True), ("schema_version", 2), ("project_id", "BAD"), ("extra", 1)],
)
def test_bad_envelope(field, value):
    obj = payload()
    obj[field] = value
    with pytest.raises(ValueError):
        parse(obj)


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", True),
        ("size", -1),
        ("sha256", "A" * 64),
        ("path", "../project.json"),
        ("path", "project.json/"),
        ("path", "arbitrary.json"),
    ],
)
def test_bad_entry(field, value):
    obj = payload()
    obj["files"][0][field] = value
    with pytest.raises(ValueError):
        parse(obj)


def test_duplicate_key():
    with pytest.raises(ValueError):
        parse_native_envelope(b'{"archive_version":1,"archive_version":1}')


def test_collision():
    with pytest.raises(ValueError):
        parse(payload(["project.json", "project.json"]))
