from uuid import uuid4

import pytest
from PIL import Image

from ai_edda.cover_catalog import publish_cover, resolve_cover


def test_publish_repeat_conflict_and_tamper(tmp_path):
    asset_id = str(uuid4())
    source = tmp_path / "source.png"
    Image.new("RGBA", (800, 400), (255, 0, 0, 100)).save(source)
    result = publish_cover(tmp_path, asset_id, source)
    assert resolve_cover(tmp_path, asset_id).model_dump(mode="json") == result["cover"]
    assert publish_cover(tmp_path, asset_id, source) == result
    Image.new("RGB", (400, 800), "blue").save(source)
    with pytest.raises(ValueError):
        publish_cover(tmp_path, asset_id, source)
    (tmp_path / result["cover"]["path"]).write_bytes(b"bad")
    with pytest.raises(Exception):
        resolve_cover(tmp_path, asset_id)


def test_jpeg(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (700, 300), "green").save(source)
    asset_id = str(uuid4())
    publish_cover(tmp_path, asset_id, source)
    assert resolve_cover(tmp_path, asset_id).width == 512
