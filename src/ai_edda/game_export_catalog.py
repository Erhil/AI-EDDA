"""Read-only verification of published game exports."""

import hashlib
import json
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path

from .asset_catalog import _safe
from .native_history import MAX_NODES, _integer, _read, _uuid
from .safe_zip import validate_member_paths

MAX_GAME_EXPORT_BYTES = 2 * 1024**3


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _request(export_id, job_id, context, result):
    _uuid(export_id)
    if (
        not isinstance(job_id, str)
        or not job_id
        or not isinstance(context, dict)
        or not isinstance(result, dict)
    ):
        raise ValueError("Invalid publication request")
    _uuid(context.get("project_id"))
    _uuid(context.get("export_id"))
    _integer(context.get("project_revision"))
    if context["export_id"] != export_id:
        raise ValueError("Export mismatch")
    if set(result) != {"folder_name", "receipt", "zip_sha256", "receipt_sha256"} or not isinstance(
        result["receipt"], dict
    ):
        raise ValueError("Invalid result")
    if not isinstance(result["folder_name"], str) or not re.fullmatch(
        r"[a-z]+", result["folder_name"]
    ):
        raise ValueError("Invalid folder name")
    for key in ("zip_sha256", "receipt_sha256"):
        if not isinstance(result[key], str) or not re.fullmatch(r"[0-9a-f]{64}", result[key]):
            raise ValueError("Invalid digest")
    receipt = result["receipt"]
    if (
        receipt.get("snapshot_id") != export_id
        or type(receipt.get("project_revision")) is not int
        or receipt["project_revision"] != context["project_revision"]
    ):
        raise ValueError("Receipt mismatch")
    return _hash(context), _hash(result)


def _public(context, result):
    return {
        "project_id": context["project_id"],
        "export_id": context["export_id"],
        "kind": "game",
        "project_revision": context["project_revision"],
        "folder_name": result["folder_name"],
        "site_check": result["receipt"]["site_check"],
    }


def resolve_game_export(project_dir, export_id):
    _uuid(export_id)
    root = _safe(Path(project_dir) / "exports" / export_id)
    publication = _read(root / "publication.json", [8 * 1024 * 1024, MAX_NODES])
    keys = {
        "version",
        "project_id",
        "export_id",
        "job_id",
        "context_sha256",
        "result_sha256",
        "folder_name",
        "zip_sha256",
        "receipt_sha256",
        "public_result",
    }
    if (
        not isinstance(publication, dict)
        or set(publication) != keys
        or type(publication["version"]) is not int
        or publication["version"] != 1
    ):
        raise ValueError("Invalid publication")
    _uuid(publication["project_id"])
    _uuid(publication["export_id"])
    if (
        publication["export_id"] != export_id
        or not isinstance(publication["job_id"], str)
        or not publication["job_id"]
    ):
        raise ValueError("Invalid publication identity")
    head = _safe(Path(project_dir)) / "project.json"
    if head.exists() or head.is_symlink():
        data = _read(head, [8 * 1024 * 1024, MAX_NODES])
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("project"), dict)
            or data["project"].get("id") != publication["project_id"]
        ):
            raise ValueError("Project mismatch")
    for key in ("context_sha256", "result_sha256", "zip_sha256", "receipt_sha256"):
        if not isinstance(publication[key], str) or not re.fullmatch(
            r"[0-9a-f]{64}", publication[key]
        ):
            raise ValueError("Invalid digest")
    result = verify_game_export(root, publication["folder_name"])
    context = {
        "project_id": publication["project_id"],
        "export_id": export_id,
        "project_revision": result["receipt"]["project_revision"],
    }
    _request(export_id, publication["job_id"], context, result)
    if (
        {p.name for p in root.iterdir()}
        != {
            result["folder_name"],
            result["folder_name"] + ".zip",
            result["folder_name"] + ".receipt.json",
            "publication.json",
        }
        or _hash(result) != publication["result_sha256"]
        or result["zip_sha256"] != publication["zip_sha256"]
        or result["receipt_sha256"] != publication["receipt_sha256"]
        or _canonical(_public(context, result)) != _canonical(publication["public_result"])
    ):
        raise ValueError("Publication mismatch")
    return publication


def reconcile_game_export(project_dir, export_id, job_id, context, result):
    context_hash, result_hash = _request(export_id, job_id, context, result)
    project = _safe(Path(project_dir))
    wrapper = _game_stage_path(project, context["project_id"], export_id, job_id)
    final = Path(project_dir) / "exports" / export_id
    publication = None
    if final.exists() or final.is_symlink():
        publication = resolve_game_export(project_dir, export_id)
        if (publication["job_id"], publication["context_sha256"], publication["result_sha256"]) != (
            job_id,
            context_hash,
            result_hash,
        ):
            raise ValueError("Publication conflict")
    _cleanup_game_stage(
        wrapper, project.parent, context["project_id"], export_id, job_id, context_hash, result_hash
    )
    return (True, publication["public_result"]) if publication is not None else (False, None)


def _game_stage_path(project, project_id, export_id, job_id):
    token = hashlib.sha256(_canonical([project_id, export_id, job_id])).hexdigest()
    return project.parent / (".game-export-" + token)


def _cleanup_game_stage(wrapper, parent, project_id, export_id, job_id, context_hash, result_hash):
    def inspect(path, kind):
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or not kind(info.st_mode)
        ):
            raise ValueError("Unsafe game export recovery data")

    try:
        inspect(wrapper, stat.S_ISDIR)
    except FileNotFoundError:
        return
    if wrapper.parent.resolve() != parent.resolve():
        raise ValueError("Unsafe game export recovery wrapper")
    marker = wrapper / "marker.json"
    payload = wrapper / "export"
    inspect(marker, stat.S_ISREG)
    entries = {item.name for item in wrapper.iterdir()}
    if entries not in ({"marker.json"}, {"marker.json", "export"}):
        raise ValueError("Unexpected game export recovery data")
    if "export" in entries:
        inspect(payload, stat.S_ISDIR)
    expected = {
        "version": 1,
        "project_id": project_id,
        "export_id": export_id,
        "job_id": job_id,
        "context_sha256": context_hash,
        "result_sha256": result_hash,
    }
    if _canonical(_read(marker, [8192, 100])) != _canonical(expected):
        raise ValueError("Game export recovery ownership conflict")
    shutil.rmtree(wrapper)


def publish_game_export(project_dir, export_id, job_id, context, result, prepared_dir):
    context_hash, result_hash = _request(export_id, job_id, context, result)
    found, public = reconcile_game_export(project_dir, export_id, job_id, context, result)
    if found:
        return public
    project = _safe(Path(project_dir))
    head = project / "project.json"
    if head.exists() or head.is_symlink():
        data = _read(head, [8 * 1024 * 1024, MAX_NODES])
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("project"), dict)
            or data["project"].get("id") != context["project_id"]
            or data["project"].get("id") != context["project_id"]
        ):
            raise ValueError("Project mismatch")
    if _canonical(verify_game_export(prepared_dir, result["folder_name"])) != _canonical(result):
        raise ValueError("Result mismatch")
    exports = project / "exports"
    exports.mkdir(exist_ok=True)
    _safe(exports)
    wrapper = _game_stage_path(project, context["project_id"], export_id, job_id)
    wrapper.mkdir()
    marker = {
        "version": 1,
        "project_id": context["project_id"],
        "export_id": export_id,
        "job_id": job_id,
        "context_sha256": context_hash,
        "result_sha256": result_hash,
    }
    try:
        with (wrapper / "marker.json").open("xb") as out:
            out.write(_canonical(marker))
            out.flush()
            os.fsync(out.fileno())
        stage = wrapper / "export"
        stage.mkdir()
        name = result["folder_name"]
        (stage / name).mkdir()
        source = _safe(Path(prepared_dir))
        for src, dst in [
            (source / name / p.name, stage / name / p.name) for p in (source / name).iterdir()
        ] + [
            (source / (name + suffix), stage / (name + suffix))
            for suffix in (".zip", ".receipt.json")
        ]:
            src = _safe(src)
            if not stat.S_ISREG(src.lstat().st_mode):
                raise ValueError("Expected regular file")
            with src.open("rb") as inp, dst.open("xb") as out:
                while block := inp.read(1024 * 1024):
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
        if _canonical(verify_game_export(stage, name)) != _canonical(result):
            raise ValueError("Copied result mismatch")
        public = _public(context, result)
        publication = {
            "version": 1,
            "project_id": context["project_id"],
            "export_id": export_id,
            "job_id": job_id,
            "context_sha256": context_hash,
            "result_sha256": result_hash,
            "folder_name": name,
            "zip_sha256": result["zip_sha256"],
            "receipt_sha256": result["receipt_sha256"],
            "public_result": public,
        }
        with (stage / "publication.json").open("xb") as out:
            out.write(_canonical(publication))
            out.flush()
            os.fsync(out.fileno())
        os.rename(stage, exports / export_id)
        return public
    finally:
        _cleanup_game_stage(
            wrapper,
            project.parent,
            context["project_id"],
            export_id,
            job_id,
            context_hash,
            result_hash,
        )


def verify_game_export(directory, folder_name, cancel=None):
    cancel_error = None

    def check():
        nonlocal cancel_error
        if cancel:
            try:
                cancel()
            except Exception as exc:
                cancel_error = exc
                raise

    def digest(stream, limit=None):
        if limit is None:
            limit = MAX_GAME_EXPORT_BYTES
        h = hashlib.sha256()
        size = 0
        while True:
            check()
            block = stream.read(min(1024 * 1024, limit - size + 1))
            if not block:
                return size, h.hexdigest()
            size += len(block)
            if size > limit:
                raise ValueError("Size limit exceeded")
            h.update(block)

    def regular(path):
        path = _safe(path)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Expected regular file")
        return path

    def hash_file(path, limit=None):
        with regular(path).open("rb") as stream:
            return digest(stream, limit)

    def hash_value(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Invalid hash")

    check()
    if not isinstance(folder_name, str) or not re.fullmatch(r"[a-z]+", folder_name):
        raise ValueError("Invalid folder name")
    root = _safe(Path(directory))
    folder = _safe(root / folder_name)
    publication_path = root / "publication.json"
    if publication_path.exists() or publication_path.is_symlink():
        regular(publication_path)
    archive_path = regular(root / (folder_name + ".zip"))
    receipt_path = regular(root / (folder_name + ".receipt.json"))
    if not folder.is_dir() or {p.name for p in root.iterdir()} - {
        folder_name,
        folder_name + ".zip",
        folder_name + ".receipt.json",
        "publication.json",
    }:
        raise ValueError("Invalid export directory")
    receipt = _read(receipt_path, [8 * 1024 * 1024, MAX_NODES])
    check()
    keys = {
        "snapshot_id",
        "project_revision",
        "grid_revision",
        "audio_sha256",
        "selection",
        "members",
        "profile",
        "local_check",
        "site_check",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != keys
        or receipt["profile"] != "ragnarock-ragnacustoms-v1"
        or receipt["local_check"] != "passed"
    ):
        raise ValueError("Invalid receipt")
    _uuid(receipt["snapshot_id"])
    _integer(receipt["project_revision"])
    _integer(receipt["grid_revision"])
    hash_value(receipt["audio_sha256"])
    selection = receipt["selection"]
    if not isinstance(selection, list) or not 1 <= len(selection) <= 3:
        raise ValueError("Invalid selection")
    names = {"info.dat", "song.ogg", "cover.jpg"}
    ids = set()
    for item, slot in zip(selection, ("Easy.dat", "Normal.dat", "Hard.dat")):
        if (
            not isinstance(item, dict)
            or set(item) != {"chart_id", "revision", "filename"}
            or item["filename"] != slot
        ):
            raise ValueError("Invalid selection")
        _uuid(item["chart_id"])
        _integer(item["revision"])
        if item["chart_id"] in ids:
            raise ValueError("Duplicate chart")
        ids.add(item["chart_id"])
        names.add(slot)
    members = receipt["members"]
    if (
        not isinstance(members, dict)
        or set(members) != names
        or {p.name for p in folder.iterdir()} != names
    ):
        raise ValueError("Invalid members")
    total = 0
    for name, entry in members.items():
        if not isinstance(entry, dict) or set(entry) != {"size", "sha256"}:
            raise ValueError("Invalid member")
        _integer(entry["size"])
        hash_value(entry["sha256"])
        size, sha = hash_file(folder / name, MAX_GAME_EXPORT_BYTES - total)
        total += size
        if total > MAX_GAME_EXPORT_BYTES or (size, sha) != (entry["size"], entry["sha256"]):
            raise ValueError("Member mismatch")
    if members["song.ogg"]["sha256"] != receipt["audio_sha256"]:
        raise ValueError("Audio mismatch")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            validate_member_paths((i.orig_filename, i.is_dir()) for i in entries)
            if len(entries) != len(names) or {i.filename for i in entries} != names:
                raise ValueError("Invalid ZIP members")
            total = 0
            for info in entries:
                kind = stat.S_IFMT(info.external_attr >> 16)
                if info.is_dir() or info.flag_bits & 1 or kind not in (0, stat.S_IFREG):
                    raise ValueError("Invalid ZIP member")
                with archive.open(info) as stream:
                    size, sha = digest(stream, MAX_GAME_EXPORT_BYTES - total)
                total += size
                if total > MAX_GAME_EXPORT_BYTES or (size, sha) != (
                    members[info.filename]["size"],
                    members[info.filename]["sha256"],
                ):
                    raise ValueError("ZIP mismatch")
    except zipfile.BadZipFile as exc:
        if exc is cancel_error:
            raise
        raise ValueError("Invalid ZIP") from exc
    zip_size, zip_sha = hash_file(archive_path)
    errors = []
    if zip_size > 15 * 1024**2:
        errors.append("ZIP exceeds 15 MiB")
    if any(entry["size"] > 50 * 1024**2 for entry in members.values()):
        errors.append("member exceeds 50 MiB")
    site = receipt["site_check"]
    if (
        not isinstance(site, dict)
        or set(site) != {"passed", "errors"}
        or type(site["passed"]) is not bool
        or site != {"passed": not errors, "errors": errors}
    ):
        raise ValueError("Invalid site check")
    return {
        "folder_name": folder_name,
        "receipt": receipt,
        "zip_sha256": zip_sha,
        "receipt_sha256": hash_file(receipt_path)[1],
    }
