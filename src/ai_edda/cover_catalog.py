import json
import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from .asset_catalog import _digest, _safe, _source
from .domain import CoverAsset
from .project_store import _id, _json


def resolve_cover(project_dir, asset_id):
    asset_id = _id(asset_id)
    directory = _safe(Path(project_dir) / "assets" / asset_id)
    manifest = json.loads(_safe(directory / "manifest.json").read_text(encoding="utf-8"))
    cover = CoverAsset.model_validate(manifest["cover"])
    if manifest.get("asset_id") != asset_id or cover.path != f"assets/{asset_id}/cover.jpg":
        raise ValueError("Invalid asset path")
    path = _safe(directory / "cover.jpg")
    with Image.open(path) as image:
        if image.format != "JPEG" or image.size != (512, 512) or image.mode != "RGB":
            raise ValueError("Invalid cover image")
        image.load()
    if (cover.width, cover.height) != (512, 512) or _digest(path) != cover.sha256:
        raise ValueError("Cover metadata mismatch")
    return cover


def prepare_cover(source_image, workspace):
    source = _safe(source_image)
    workspace = _safe(workspace)
    target = _source(workspace, "cover.jpg")
    with Image.open(source) as image:
        if image.format not in ("JPEG", "PNG", "WEBP") or image.width * image.height > 40_000_000:
            raise ValueError("Invalid cover image")
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("Animated cover image")
        image.load()
        normalized = ImageOps.fit(
            ImageOps.exif_transpose(image).convert("RGB"),
            (512, 512),
            method=Image.Resampling.LANCZOS,
        )
        with target.open("wb") as output:
            normalized.save(
                output, format="JPEG", quality=90, exif=b"", icc_profile=None, comment=b""
            )
            output.flush()
            os.fsync(output.fileno())
    return CoverAsset(path="cover.jpg", sha256=_digest(target), width=512, height=512)


def publish_prepared_cover(project_dir, asset_id, workspace, cover, publication=None):
    asset_id = _id(asset_id)
    cover = CoverAsset.model_validate(cover)
    if cover.path != "cover.jpg" or (cover.width, cover.height) != (512, 512):
        raise ValueError("Invalid cover metadata")
    source = _source(_safe(workspace), cover.path)
    project = _safe(project_dir)
    assets = _safe(project / "assets")
    assets.mkdir(exist_ok=True)
    final = _safe(assets / asset_id)
    temp = Path(tempfile.mkdtemp(prefix=".staging-", dir=assets))
    try:
        with source.open("rb") as src, (temp / "cover.jpg").open("wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        copied = temp / "cover.jpg"
        with Image.open(copied) as image:
            if image.format != "JPEG" or image.size != (512, 512) or image.mode != "RGB":
                raise ValueError("Invalid cover image")
            image.load()
        if _digest(copied) != cover.sha256:
            raise ValueError("Cover metadata mismatch")
        result = cover.model_copy(update={"path": f"assets/{asset_id}/cover.jpg"})
        manifest = {"asset_id": asset_id, "cover": result.model_dump(mode="json")}
        if publication is not None:
            manifest["publication"] = publication
        with (temp / "manifest.json").open("wb") as output:
            output.write(_json(manifest))
            output.flush()
            os.fsync(output.fileno())
        try:
            os.rename(temp, final)
        except OSError:
            if final.exists():
                if resolve_cover(project, asset_id) == result and (
                    publication is None
                    or json.loads((final / "manifest.json").read_text(encoding="utf-8")).get(
                        "publication"
                    )
                    == publication
                ):
                    return manifest
                raise ValueError("Asset ID conflict")
            raise
        return manifest
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def publish_cover(project_dir, asset_id, source_image):
    with tempfile.TemporaryDirectory() as workspace:
        cover = prepare_cover(source_image, workspace)
        return publish_prepared_cover(project_dir, asset_id, workspace, cover)
