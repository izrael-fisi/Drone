from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
import zipfile

from vision_nav.autonomy_handoff import artifact_availability


DEFAULT_MAX_ARTIFACT_BYTES = 25_000_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package autonomy readiness report, handoff, and small referenced evidence artifacts into a support-review ZIP."
    )
    parser.add_argument("--report", required=True, help="Path to autonomy_readiness_report.json.")
    parser.add_argument("--handoff", help="Optional Markdown handoff path. Defaults to report sibling .md.")
    parser.add_argument("--output", help="Optional output ZIP path. Defaults to report sibling .evidence.zip.")
    parser.add_argument(
        "--max-artifact-bytes",
        type=int,
        default=DEFAULT_MAX_ARTIFACT_BYTES,
        help="Maximum size for each referenced evidence artifact copied into the package.",
    )
    return parser.parse_args()


def create_evidence_package(
    report_path: str | Path,
    *,
    handoff_path: str | Path | None = None,
    output_path: str | Path | None = None,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> dict[str, Any]:
    report_file = Path(report_path).expanduser()
    report = json.loads(report_file.read_text())
    handoff_file = Path(handoff_path).expanduser() if handoff_path else report_file.with_suffix(".md")
    output_file = Path(output_path).expanduser() if output_path else report_file.with_suffix(".evidence.zip")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    included: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used_names: set[str] = set()

    with zipfile.ZipFile(output_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        add_file(archive, report_file, "reports/autonomy_readiness_report.json", "autonomy_report", included, used_names)
        if handoff_file.exists() and handoff_file.is_file():
            add_file(archive, handoff_file, "reports/autonomy_readiness_report.md", "autonomy_handoff", included, used_names)
        else:
            missing.append({"label": "autonomy_handoff", "path": str(handoff_file)})

        for artifact in artifact_availability(report):
            label = str(artifact.get("label") or "artifact")
            raw_path = str(artifact.get("path") or "")
            if not raw_path:
                continue
            source = Path(raw_path).expanduser()
            if same_file(source, report_file) or same_file(source, handoff_file):
                continue
            if not source.exists():
                missing.append({"label": label, "path": raw_path})
                continue
            if not source.is_file():
                skipped.append({"label": label, "path": raw_path, "reason": "not_a_file"})
                continue
            size = source.stat().st_size
            if size > max_artifact_bytes:
                skipped.append(
                    {
                        "label": label,
                        "path": raw_path,
                        "reason": "too_large",
                        "size_bytes": size,
                        "max_artifact_bytes": max_artifact_bytes,
                    }
                )
                continue
            arcname = unique_arcname(
                f"artifacts/{safe_name(label)}-{safe_name(source.name)}",
                used_names,
            )
            add_file(archive, source, arcname, label, included, used_names)

        manifest = {
            "schema_version": "vision_nav_autonomy_evidence_package_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_report": str(report_file),
            "source_handoff": str(handoff_file),
            "output_path": str(output_file),
            "readiness_status": report.get("status"),
            "ready_for_goal_completion": (report.get("evidence_manifest") or {}).get("ready_for_goal_completion")
            if isinstance(report.get("evidence_manifest"), dict)
            else None,
            "max_artifact_bytes": max_artifact_bytes,
            "included": included,
            "missing": missing,
            "skipped": skipped,
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    result = {
        "zip_path": str(output_file),
        "manifest": manifest,
        "included_count": len(included),
        "missing_count": len(missing),
        "skipped_count": len(skipped),
    }
    return result


def add_file(
    archive: zipfile.ZipFile,
    source: Path,
    arcname: str,
    label: str,
    included: list[dict[str, Any]],
    used_names: set[str],
) -> None:
    final_arcname = unique_arcname(arcname, used_names)
    archive.write(source, final_arcname)
    included.append(
        {
            "label": label,
            "path": str(source),
            "archive_path": final_arcname,
            "size_bytes": source.stat().st_size,
        }
    )


def unique_arcname(name: str, used_names: set[str]) -> str:
    base = name
    index = 2
    while name in used_names:
        stem = Path(base).with_suffix("").as_posix()
        suffix = Path(base).suffix
        name = f"{stem}-{index}{suffix}"
        index += 1
    used_names.add(name)
    return name


def safe_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    sanitized = sanitized.strip("._-")
    return sanitized or "artifact"


def same_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and left.resolve() == right.resolve()
    except OSError:
        return False


def main() -> None:
    args = parse_args()
    result = create_evidence_package(
        args.report,
        handoff_path=args.handoff,
        output_path=args.output,
        max_artifact_bytes=args.max_artifact_bytes,
    )
    print(f"Autonomy evidence package: {result['zip_path']}")
    print(f"Included: {result['included_count']} Missing: {result['missing_count']} Skipped: {result['skipped_count']}")
    print(f"__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__={result['zip_path']}")


if __name__ == "__main__":
    main()
