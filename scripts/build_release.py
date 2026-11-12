"""Build a wheel and a reproducible source release from a prepared checkout."""

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "pyproject.toml",
    "uv.lock",
    "setup.ps1",
    "start.ps1",
    "README.md",
    ".gitignore",
    ".editorconfig",
    ".gitattributes",
    ".python-version",
    ".github/workflows/checks.yml",
    "web/demo-public/mockServiceWorker.js",
    "web/demo-public/demo-cover.jpg",
    "LICENSE",
    "docs/DEPENDENCIES.md",
    "THIRD_PARTY_NOTICES.md",
    "MODEL_MANIFEST.json",
    "docs/MODEL_CHECKS.md",
    "web/README.md",
    "scripts/model_smoke.py",
    "docs/INSTALL_WINDOWS.md",
    "scripts/check.ps1",
    "scripts/export_schema.py",
    "scripts/build_release.py",
    "scripts/check_frontend.mjs",
    "scripts/frontend-checks.json",
    "scripts/frontend-checks.md",
    "web/package.json",
    "web/package-lock.json",
    "web/tsconfig.json",
    "web/vite.config.ts",
    "web/index.html",
)
TREES = {
    "src/ai_edda": {".py", ".html", ".js", ".css", ".svg", ".png", ".ico", ".woff", ".woff2"},
    "web/src": {".ts", ".tsx", ".css", ".svg", ".html"},
    "web/public": {".svg", ".png", ".ico"},
    "web/schema": {".json"},
    "fixtures": {".json"},
    "tests": {".py"},
    "web/tests": {".mjs", ".cjs"},
}
DENY = {".venv", ".tools", "node_modules", "__pycache__", "models", "projects", "music"}


def validate_package_inputs(root):
    """Reject links anywhere in package input trees before invoking the build backend."""
    root = root.resolve(strict=True)
    for name in ("src/ai_edda",):
        base = root / name
        current = root
        for part in Path(name).parts:
            current = current / part
            if current.is_symlink() or current.is_junction():
                raise ValueError(f"Linked package input is not allowed: {current}")
        if base.exists():
            for path in base.rglob("*"):
                if path.is_symlink() or path.is_junction():
                    raise ValueError(f"Linked package input is not allowed: {path}")


def inventory(root):
    root = root.resolve(strict=True)

    def safe(path):
        # Reject reparse points in every component and verify the final target.
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if current.is_symlink() or current.is_junction():
                return False
        try:
            path.resolve(strict=True).relative_to(root)
            return True
        except (OSError, ValueError):
            return False

    paths = {root / name for name in FILES if (root / name).is_file() and safe(root / name)}
    manifest = root / "scripts/frontend-checks.json"
    if manifest.exists():
        if not safe(manifest):
            raise ValueError("Linked frontend manifest is not allowed")
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("Invalid frontend manifest")
        for name in entries:
            if (
                not isinstance(name, str)
                or "\\" in name
                or ":" in name
                or not name.startswith("web/tests/regression/")
                or any(part in ("", ".", "..") for part in name.split("/"))
                or Path(name).suffix not in (".mjs", ".cjs")
            ):
                raise ValueError(f"Invalid frontend manifest path: {name}")
            path = root / name
            if not path.is_file() or not safe(path):
                raise ValueError(f"Missing or linked frontend check: {name}")
            paths.add(path)
    for tree, extensions in TREES.items():
        base = root / tree
        if base.exists() and safe(base):
            for path in base.rglob("*"):
                if (
                    path.is_file()
                    and safe(path)
                    and not any(part.lower() in DENY for part in path.relative_to(root).parts)
                    and path.suffix.lower() in extensions
                ):
                    paths.add(path)
    return sorted(paths, key=lambda p: p.relative_to(root).as_posix())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def assemble(root, output):
    root, output = root.resolve(), output.resolve()
    static = root / "src/ai_edda/static/index.html"
    if not static.is_file():
        raise ValueError(f"Prebuilt static index is missing: {static}")
    for tree in TREES:
        if output == (root / tree).resolve() or (root / tree).resolve() in output.parents:
            raise ValueError("Output must be outside included source directories")
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    if (
        not isinstance(version, str)
        or not version
        or Path(version).name != version
        or any(c in version for c in "/\\:")
    ):
        raise ValueError("Project version must be a safe filename component")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"ai-edda-{version}-windows-source.zip"
    hashes = {}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in inventory(root):
            name = path.relative_to(root).as_posix()
            data = path.read_bytes()
            hashes[name] = digest(data)
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    manifest = {
        "version": version,
        "files": hashes,
        "archive": {"name": archive.name, "sha256": digest(archive.read_bytes())},
    }
    (output / f"ai-edda-{version}-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        if not (ROOT / "src/ai_edda/static/index.html").is_file():
            raise ValueError(
                "Prebuilt src/ai_edda/static/index.html is missing; build the web frontend first"
            )
        uv = ROOT / ".tools/uv.exe"
        if not uv.is_file():
            raise ValueError(f"uv executable is missing: {uv}")
        validate_package_inputs(ROOT)
        # Validate the destination before invoking the build.
        output = args.output.resolve()
        for tree in TREES:
            base = (ROOT / tree).resolve()
            if output == base or base in output.parents:
                raise ValueError("Output must be outside included source directories")
        output.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(uv), "build", "--wheel", "--out-dir", str(output)], cwd=ROOT, check=True
        )
        print(assemble(ROOT, output))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
