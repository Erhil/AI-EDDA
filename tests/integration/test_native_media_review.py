"""Native media boundaries, using real publishers and small extractor artifacts."""

import hashlib
import json
import zipfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from ai_edda.analysis_catalog import publish_analysis
from ai_edda.application import ApplicationService
from ai_edda.asset_catalog import publish_audio
from ai_edda.beat_analysis import MODEL_SHA256
from ai_edda.cover_catalog import prepare_cover, publish_prepared_cover
from ai_edda.domain import AudioAsset, CoverAsset, ObjectRef
from ai_edda.native_history import validate_native_history
from ai_edda.native_media import validate_native_media
from ai_edda.project_store import ProjectStore


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def publish_small_analysis(root, workspace, aid, audio):
    workspace.mkdir()
    common = {"audio_sha256": audio.sha256, "sample_rate": 22050, "hop_length": 441}
    configs = {
        "beat": {
            **common,
            "adapter_extractor_version": 1,
            "model_sha256": MODEL_SHA256,
            "n_fft": 1024,
            "mel_bands": 128,
            "log_multiplier": 1000,
            "chunk_size": 1500,
            "border_size": 6,
            "batch_size": 1,
            "overlap_mode": "keep_first",
            "timing": "frame/50",
            "postprocessor": "minimal",
            "device": "cpu",
            "float16": False,
            "versions": {
                key: "test"
                for key in ("beat-this", "torch", "torchaudio", "numpy", "soundfile", "soxr")
            },
        },
        "rhythm": {
            **common,
            "version": 1,
            "n_fft": 2048,
            "center": True,
            "frame_time": "frame * hop_length / sample_rate",
            "versions": {key: "test" for key in ("numpy", "librosa", "scipy", "soundfile", "soxr")},
        },
    }
    times = np.arange(11) / 50
    arrays = {
        "beat": {
            "beat_logits": np.zeros(11),
            "downbeat_logits": np.zeros(11),
            "beat_times": times[::5],
            "downbeat_times": times[:1],
        },
        "rhythm": {
            "times": times,
            "onset_envelope": np.zeros(11),
            "onset_times": times[::5],
            "rms": np.zeros(11),
            "activity": np.zeros(11),
        },
    }
    roles = {}
    for role, filename in (("beat", "beats.npz"), ("rhythm", "features.npz")):
        directory = workspace / role
        directory.mkdir()
        np.savez_compressed(directory / filename, **arrays[role])
        manifest = {"config": configs[role], "npz_sha256": digest(directory / filename)}
        if role == "beat":
            manifest["source"] = {
                "sample_rate": audio.sample_rate,
                "channels": audio.channels,
                "decoded_frames": audio.frames,
                "duration_sec": audio.duration_sec,
                "codec_path": "SoundFile:OGG/VORBIS",
            }
        write(directory / "manifest.json", manifest)
        roles[role] = {
            "extractor_version": 1,
            "manifest_path": f"{role}/manifest.json",
            "manifest_sha256": digest(directory / "manifest.json"),
            "npz_path": f"{role}/{filename}",
            "npz_sha256": digest(directory / filename),
        }
    analysis_id = str(uuid4())
    descriptor = {"audio_asset_id": aid, "audio": audio.model_dump(mode="json"), "roles": roles}
    result = publish_analysis(
        root,
        analysis_id,
        uuid4().hex,
        {"audio_asset_id": aid, "audio": audio.model_dump(mode="json")},
        descriptor,
        workspace,
    )
    return analysis_id, result


@pytest.fixture
def media(tmp_path):
    with ProjectStore(tmp_path / "store") as store:
        app = ApplicationService(store)
        view = app.create_project(str(uuid4()), {})
        root = store._dir(view.project.id)
        source = tmp_path / "source"
        source.mkdir()
        sf.write(source / "song.ogg", np.zeros(4410), 22050, format="OGG", subtype="VORBIS")
        info = sf.info(source / "song.ogg")
        raw = AudioAsset(
            path="song.ogg",
            sha256=digest(source / "song.ogg"),
            sample_rate=info.samplerate,
            channels=info.channels,
            frames=info.frames,
            duration_sec=info.duration,
            subtype="VORBIS",
        )
        audio_ids = [str(uuid4()) for _ in range(3)]
        audio = [
            AudioAsset.model_validate(publish_audio(root, aid, source, raw)["audio"])
            for aid in audio_ids
        ]
        Image.new("RGB", (16, 16), "blue").save(source / "source.png")
        cover = prepare_cover(source / "source.png", source)
        cover_id = str(uuid4())
        published = publish_prepared_cover(
            root,
            cover_id,
            source,
            cover,
            {"job_id": str(uuid4()), "context_sha256": "1" * 64, "result_sha256": "2" * 64},
        )
        aid, _ = publish_small_analysis(root, tmp_path / "analysis1", audio_ids[0], audio[0])
        orphan, _ = publish_small_analysis(root, tmp_path / "analysis2", audio_ids[0], audio[0])
        snapshot = store.load(view.project.id)
        for index in range(2):
            project = snapshot.project.model_copy(
                update={
                    "audio": audio[index],
                    "cover": CoverAsset.model_validate(published["cover"]) if index == 0 else None,
                    "analysis": ObjectRef(id=aid, revision=0) if index == 0 else None,
                }
            )
            store.commit(
                view.project.id,
                str(index),
                {},
                snapshot.project.revision,
                snapshot.grid.revision,
                {c.id: c.revision for c in snapshot.charts},
                project,
                snapshot.grid,
                snapshot.charts,
                {"ok": True},
            )
            snapshot = store.load(view.project.id)
        history = validate_native_history(root, view.project.id)
    return {
        "root": root,
        "history": history,
        "audio_ids": audio_ids,
        "cover_id": cover_id,
        "analysis_id": aid,
        "orphan_analysis": orphan,
    }


def validate(media):
    return validate_native_media(media["root"], media["history"])


def repair_hashes(directory):
    """Keep transport hashes valid so schema tests reach their intended boundary."""
    descriptor = read(directory / "descriptor.json")
    for role, filename in (("beat", "beats.npz"), ("rhythm", "features.npz")):
        descriptor["roles"][role]["manifest_sha256"] = digest(directory / role / "manifest.json")
        descriptor["roles"][role]["npz_sha256"] = digest(directory / role / filename)
    write(directory / "descriptor.json", descriptor)
    analysis = read(directory / "analysis.json")
    for item in analysis["manifests"]:
        role = Path(item["path"]).parts[-2]
        item["sha256"] = descriptor["roles"][role]["manifest_sha256"]
    write(directory / "analysis.json", analysis)
    publication = read(directory / "publication.json")
    publication["public_result"] = analysis
    for name in publication["artifacts"]:
        publication["artifacts"][name] = digest(directory / name)
    write(directory / "publication.json", publication)


def test_real_publishers_preserve_replaced_and_unselected_media(media):
    root = media["root"]
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    audio, covers, analyses = validate(media)
    assert set(audio) == set(media["audio_ids"])
    assert set(covers) == {media["cover_id"]}
    assert set(analyses) == {media["analysis_id"], media["orphan_analysis"]}
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "publication",
    [
        None,
        {},
        {"job_id": "bad", "context_sha256": "1" * 64, "result_sha256": "2" * 64},
        {"job_id": str(uuid4()), "context_sha256": "bad", "result_sha256": "2" * 64},
        {"job_id": str(uuid4()), "context_sha256": "1" * 64, "result_sha256": "2" * 64, "extra": 1},
    ],
)
def test_cover_optional_publication_is_validated(media, publication):
    path = media["root"] / "assets" / media["cover_id"] / "manifest.json"
    value = read(path)
    value["publication"] = publication
    write(path, value)
    with pytest.raises(ValueError):
        validate(media)


@pytest.mark.parametrize(
    "family, field, replacement",
    [("audio", "channels", True), ("audio", "frames", "4410"), ("assets", "width", "512")],
)
def test_media_integer_metadata_is_not_coerced(media, family, field, replacement):
    aid = media["audio_ids"][2] if family == "audio" else media["cover_id"]
    path = media["root"] / family / aid / "manifest.json"
    value = read(path)
    value["audio" if family == "audio" else "cover"][field] = replacement
    write(path, value)
    with pytest.raises(ValueError):
        validate(media)


@pytest.mark.parametrize(
    "location",
    [
        "descriptor",
        "role",
        "beat_manifest",
        "beat_config",
        "beat_source",
        "rhythm_manifest",
        "rhythm_config",
    ],
)
def test_unselected_analysis_nested_unknown_fields_are_rejected(media, location):
    directory = media["root"] / "analysis" / media["orphan_analysis"]
    path = directory / (
        "descriptor.json"
        if location in ("descriptor", "role")
        else "beat/manifest.json"
        if location.startswith("beat")
        else "rhythm/manifest.json"
    )
    value = read(path)
    target = (
        value["roles"]["beat"]
        if location == "role"
        else value["config"]
        if location.endswith("config")
        else value["source"]
        if location == "beat_source"
        else value
    )
    target["unknown_schema_field"] = 1
    write(path, value)
    repair_hashes(directory)
    with pytest.raises(ValueError):
        validate(media)


@pytest.mark.parametrize("family", ["audio", "assets", "analysis"])
def test_unknown_catalog_files_are_rejected(media, family):
    aid = (
        media["audio_ids"][2]
        if family == "audio"
        else media["cover_id"]
        if family == "assets"
        else media["orphan_analysis"]
    )
    (media["root"] / family / aid / "unknown.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        validate(media)


def test_missing_historical_audio_is_rejected(media):
    (media["root"] / "audio" / media["audio_ids"][0] / "song.ogg").unlink()
    with pytest.raises(ValueError):
        validate(media)


def test_shared_expansion_budget_covers_all_catalogs(media, monkeypatch):
    import ai_edda.native_media as module

    sizes = []
    for aid in (media["analysis_id"], media["orphan_analysis"]):
        sizes.append(
            sum(
                info.file_size
                for path in (media["root"] / "analysis" / aid).rglob("*.npz")
                for info in zipfile.ZipFile(path).infolist()
            )
        )
    monkeypatch.setattr(module, "MAX_ANALYSIS_EXPANSION_BYTES", max(sizes))
    with pytest.raises(ValueError, match="budget"):
        validate(media)


def test_unsafe_npz_is_rejected_before_numpy_load(media, monkeypatch):
    directory = media["root"] / "analysis" / media["orphan_analysis"]
    np.savez_compressed(directory / "beat" / "beats.npz", unexpected=np.zeros(1))
    manifest = read(directory / "beat" / "manifest.json")
    manifest["npz_sha256"] = digest(directory / "beat" / "beats.npz")
    write(directory / "beat" / "manifest.json", manifest)
    repair_hashes(directory)
    original = np.load

    def guarded(path, *args, **kwargs):
        assert Path(path) != directory / "beat" / "beats.npz", "Unsafe NPZ reached numpy"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(np, "load", guarded)
    with pytest.raises(ValueError):
        validate(media)


def test_historical_analysis_must_match_its_own_audio(media):
    history = media["history"]
    from copy import copy

    changed = copy(history)
    changed.heads = [
        replace(
            h,
            project=h.project.model_copy(
                update={"audio": history.materialize_current().project.audio}
            ),
        )
        if h.project.analysis is not None
        else h
        for h in history.heads
    ]
    with pytest.raises(ValueError, match="historical analysis"):
        validate_native_media(media["root"], changed)


@pytest.mark.parametrize("document", [None, [], {"roles": {"beat": None}}])
def test_malformed_analysis_descriptor_is_validation_error(media, document):
    directory = media["root"] / "analysis" / media["orphan_analysis"]
    write(directory / "descriptor.json", document)
    publication = read(directory / "publication.json")
    publication["artifacts"]["descriptor.json"] = digest(directory / "descriptor.json")
    write(directory / "publication.json", publication)
    with pytest.raises(ValueError):
        validate(media)


@pytest.mark.parametrize("location", ["analysis", "descriptor", "source"])
def test_nested_analysis_audio_metadata_cannot_coerce(media, location):
    directory = media["root"] / "analysis" / media["orphan_analysis"]
    path = directory / ("beat/manifest.json" if location == "source" else location + ".json")
    value = read(path)
    value["source" if location == "source" else "audio"]["channels"] = True
    write(path, value)
    # Preserve the valid integer public result when testing analysis.json coercion.
    publication = read(directory / "publication.json")
    if location == "analysis":
        publication["artifacts"]["analysis.json"] = digest(path)
        write(directory / "publication.json", publication)
    else:
        repair_hashes(directory)
    with pytest.raises(ValueError):
        validate(media)


@pytest.mark.parametrize(
    "field, replacement",
    [("duration_sec", "invalid"), ("codec_path", {}), ("sample_rate", -1), ("decoded_frames", -99)],
)
def test_beat_source_matches_verified_audio(media, field, replacement):
    directory = media["root"] / "analysis" / media["orphan_analysis"]
    path = directory / "beat" / "manifest.json"
    value = read(path)
    value["source"][field] = replacement
    write(path, value)
    repair_hashes(directory)
    with pytest.raises(ValueError):
        validate(media)


def test_analysis_publication_preserves_nonempty_job_identity_contract(media):
    path = media["root"] / "analysis" / media["orphan_analysis"] / "publication.json"
    value = read(path)
    # analysis_catalog._receipt permits nonempty strings. The handler test uses 'job'.
    value["job_id"] = "job"
    write(path, value)
    assert media["orphan_analysis"] in validate(media)[2]


def test_aggregate_media_json_limit_rejects_before_resolvers(media, monkeypatch):
    import ai_edda.native_media as module

    monkeypatch.setattr(module, "MAX_MEDIA_JSON_BYTES", 1)
    monkeypatch.setattr(
        module, "resolve_audio", lambda *args: pytest.fail("Resolver ran before bounded JSON check")
    )
    with pytest.raises(ValueError, match="limit"):
        validate(media)


def test_duplicate_json_keys_are_rejected(media):
    path = media["root"] / "assets" / media["cover_id"] / "manifest.json"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw[:-1] + ',"asset_id": "' + media["cover_id"] + '"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        validate(media)


@pytest.mark.parametrize(
    "family, name",
    [("audio", "song.ogg"), ("assets", "cover.jpg"), ("analysis", "descriptor.json")],
)
def test_catalog_required_files_must_be_regular_files(media, family, name):
    aid = (
        media["audio_ids"][2]
        if family == "audio"
        else media["cover_id"]
        if family == "assets"
        else media["orphan_analysis"]
    )
    path = media["root"] / family / aid / name
    path.unlink()
    path.mkdir()
    with pytest.raises(ValueError):
        validate(media)
