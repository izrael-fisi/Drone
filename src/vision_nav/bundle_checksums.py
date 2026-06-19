from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from vision_nav.bundle import load_manifest

CHECKSUM_FILENAME = "checksums.sha256"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write or verify map bundle SHA-256 checksums.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="Write checksums.sha256 for bundle files.")
    action.add_argument("--verify", action="store_true", help="Verify bundle files against checksums.sha256.")
    parser.add_argument("--checksum-file", help="Checksum file path. Defaults to checksums.sha256 in the bundle.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def bundle_dir_from_arg(bundle: str | Path) -> Path:
    bundle_dir, _ = load_manifest(bundle)
    return bundle_dir


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def checksum_path_for_bundle(bundle_dir: Path, checksum_file: str | Path | None = None) -> Path:
    if checksum_file is None:
        return bundle_dir / CHECKSUM_FILENAME
    path = Path(checksum_file)
    if path.is_absolute():
        return path
    return bundle_dir / path


def iter_bundle_files(bundle_dir: Path, checksum_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in bundle_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() == checksum_path.resolve():
            continue
        files.append(path)
    return sorted(files, key=lambda path: relative_posix(path, bundle_dir))


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_checksum_file(bundle: str | Path, checksum_file: str | Path | None = None) -> dict[str, Any]:
    bundle_dir = bundle_dir_from_arg(bundle)
    checksum_path = checksum_path_for_bundle(bundle_dir, checksum_file)
    checksum_path.parent.mkdir(parents=True, exist_ok=True)

    entries = []
    for path in iter_bundle_files(bundle_dir, checksum_path):
        entries.append({"sha256": sha256_file(path), "path": relative_posix(path, bundle_dir)})

    checksum_path.write_text("".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries))
    return {
        "status": "written",
        "bundle_dir": str(bundle_dir),
        "checksum_file": str(checksum_path),
        "entries": entries,
        "entry_count": len(entries),
    }


def parse_checksum_file(checksum_path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line_number, raw_line in enumerate(checksum_path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"{checksum_path}:{line_number}: expected '<sha256>  <path>'")
        digest, relative_path = parts
        if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            raise ValueError(f"{checksum_path}:{line_number}: invalid sha256 digest")
        entries.append({"sha256": digest.lower(), "path": relative_path.strip()})
    return entries


def verify_checksum_file(bundle: str | Path, checksum_file: str | Path | None = None) -> dict[str, Any]:
    bundle_dir = bundle_dir_from_arg(bundle)
    checksum_path = checksum_path_for_bundle(bundle_dir, checksum_file)
    if not checksum_path.exists():
        return {
            "status": "missing",
            "bundle_dir": str(bundle_dir),
            "checksum_file": str(checksum_path),
            "entry_count": 0,
            "missing": [],
            "mismatched": [],
            "extra_files": [],
        }

    entries = parse_checksum_file(checksum_path)
    missing: list[str] = []
    mismatched: list[dict[str, str]] = []
    expected_paths = {entry["path"] for entry in entries}

    for entry in entries:
        path = bundle_dir / entry["path"]
        if not path.exists():
            missing.append(entry["path"])
            continue
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            mismatched.append({"path": entry["path"], "expected": entry["sha256"], "actual": actual})

    current_paths = {relative_posix(path, bundle_dir) for path in iter_bundle_files(bundle_dir, checksum_path)}
    extra_files = sorted(current_paths - expected_paths)
    status = "failed" if missing or mismatched else "passed"
    return {
        "status": status,
        "bundle_dir": str(bundle_dir),
        "checksum_file": str(checksum_path),
        "entry_count": len(entries),
        "missing": missing,
        "mismatched": mismatched,
        "extra_files": extra_files,
    }


def print_human(summary: dict[str, Any]) -> None:
    print(f"Bundle: {summary['bundle_dir']}")
    print(f"Checksum file: {summary['checksum_file']}")
    print(f"Status: {summary['status']}")
    print(f"Entries: {summary['entry_count']}")
    if summary.get("missing"):
        print(f"Missing: {summary['missing']}")
    if summary.get("mismatched"):
        print(f"Mismatched: {summary['mismatched']}")
    if summary.get("extra_files"):
        print(f"Extra files not covered by checksum: {summary['extra_files']}")


def main() -> None:
    args = parse_args()
    if args.write:
        summary = write_checksum_file(args.bundle, args.checksum_file)
    else:
        summary = verify_checksum_file(args.bundle, args.checksum_file)

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_human(summary)

    if args.verify and summary["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

