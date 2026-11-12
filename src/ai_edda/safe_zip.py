import re
import shutil
import stat
import zipfile
from pathlib import Path

_BLOCK = 1024 * 1024
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$", re.I)
_INVALID_COMPONENT = re.compile(r'[<>:"|?*\x00-\x1f]')


def validate_member_paths(entries):
    """Validate member paths and return their components in input order."""
    seen = {}
    result = []
    for name, is_dir in entries:
        if not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            raise ValueError("Invalid archive path")
        parts = name.rstrip("/").split("/")
        if any(
            not p
            or p in (".", "..")
            or _INVALID_COMPONENT.search(p)
            or p[-1] in " ."
            or _RESERVED.match(p)
            for p in parts
        ):
            raise ValueError("Invalid archive path")
        for i in range(1, len(parts) + 1):
            key = "/".join(parts[:i]).casefold()
            spelling = "/".join(parts[:i])
            expected = "dir" if i < len(parts) or is_dir else "file"
            old = seen.get(key)
            if old and (
                old[0] != spelling
                or old[1] != expected
                or (i == len(parts) and (expected == "file" or old[2]))
            ):
                raise ValueError("Conflicting archive paths")
            seen[key] = (spelling, expected, i == len(parts) or (old and old[2]))
        result.append(parts)
    return result


def extract_zip(source, destination, max_entries=10000, max_bytes=2 * 1024**3, cancel=None):
    """Extract a ZIP into a newly created directory, or remove it on failure."""
    dest = Path(destination).absolute()
    lexical_parent = dest.parent
    if dest.name in ("", ".", ".."):
        raise ValueError("Invalid destination")
    # Inspect lexical ancestry before resolve, so junctions remain visible.
    for path in (lexical_parent, *lexical_parent.parents):
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
            raise ValueError("Reparse destination")
    parent = lexical_parent.resolve(strict=True)
    if lexical_parent.resolve() != parent:
        raise ValueError("Invalid destination")
    created = False
    try:
        if cancel:
            cancel()
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > max_entries:
                raise ValueError("Too many entries")
            for info in entries:
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if info.flag_bits & 1 or (kind and kind not in (stat.S_IFREG, stat.S_IFDIR)):
                    raise ValueError("Unsupported entry")
                is_dir = info.is_dir()
                if kind == stat.S_IFDIR and not is_dir or kind == stat.S_IFREG and is_dir:
                    raise ValueError("Inconsistent entry type")
            paths = validate_member_paths((info.orig_filename, info.is_dir()) for info in entries)
            prepared = [(info, parts, info.is_dir()) for info, parts in zip(entries, paths)]
            if max_bytes < 0 or max_entries < 0:
                raise ValueError("Invalid limits")
            dest.mkdir(mode=0o700)
            created = True
            total = 0
            result = []
            for info, parts, is_dir in prepared:
                if cancel:
                    cancel()
                target = dest.joinpath(*parts)
                if is_dir:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("xb") as out:
                    while chunk := src.read(_BLOCK):
                        if cancel:
                            cancel()
                        total += len(chunk)
                        if total > max_bytes or len(chunk) > shutil.disk_usage(dest).free:
                            raise ValueError("Archive exceeds available space or size limit")
                        out.write(chunk)
                result.append("/".join(parts))
            return result
    except BaseException:
        if created and dest.parent.resolve() == parent and dest.exists() and not dest.is_symlink():
            shutil.rmtree(dest)
        raise
