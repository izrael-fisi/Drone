from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any

from vision_nav.replay_case_manifest import EXPECTED_BEHAVIORS, sanitize_filename


DATASET_TYPES = {"field", "bench", "synthetic"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register a replay/runtime log as a replay validation case.")
    parser.add_argument("--manifest", required=True, help="Replay case manifest to create or update.")
    parser.add_argument("--case-name", required=True, help="Stable replay case name.")
    parser.add_argument("--expected", required=True, choices=sorted(EXPECTED_BEHAVIORS), help="Expected replay-gate behavior.")
    parser.add_argument("--dataset-type", required=True, choices=sorted(DATASET_TYPES), help="field, bench, or synthetic.")
    parser.add_argument("--condition", action="append", default=[], help="Validation condition tag. Repeat for multiple tags.")
    parser.add_argument("--bundle", help="Bundle path or provenance label used for replay.")
    parser.add_argument("--log", required=True, help="Runtime/replay JSONL log to register.")
    parser.add_argument("--notes", help="Human-readable setup notes.")
    parser.add_argument("--copy-log", action="store_true", help="Copy the log under the manifest directory.")
    parser.add_argument("--case-dir", help="Manifest-relative directory for copied log. Defaults to <dataset-type>/<case-name>.")
    parser.add_argument("--replace", action="store_true", help="Replace an existing case with the same case_name.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def register_replay_case(
    *,
    manifest_path: str | Path,
    case_name: str,
    expected: str,
    dataset_type: str,
    conditions: list[str],
    log_path: str | Path,
    bundle: str | None = None,
    notes: str | None = None,
    copy_log: bool = False,
    case_dir: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    if expected not in EXPECTED_BEHAVIORS:
        raise ValueError(f"Unsupported expected behavior: {expected}")
    if dataset_type not in DATASET_TYPES:
        raise ValueError(f"Unsupported dataset_type: {dataset_type}")
    if not conditions:
        raise ValueError("At least one --condition is required.")

    manifest_file = Path(manifest_path).expanduser()
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest = load_or_create_manifest(manifest_file)
    cases = [case for case in manifest.get("cases") or [] if isinstance(case, dict)]
    duplicate_indexes = [index for index, case in enumerate(cases) if case.get("case_name") == case_name]
    if duplicate_indexes and not replace:
        raise ValueError(f"Replay case already exists: {case_name}. Use --replace to update it.")
    if duplicate_indexes:
        cases = [case for case in cases if case.get("case_name") != case_name]

    source_log = Path(log_path).expanduser()
    if not source_log.exists():
        raise FileNotFoundError(f"Replay log does not exist: {source_log}")

    if copy_log:
        target_dir = manifest_file.parent / (case_dir or f"{dataset_type}/{sanitize_filename(case_name)}")
        target_dir.mkdir(parents=True, exist_ok=True)
        target_log = target_dir / source_log.name
        if source_log.resolve() != target_log.resolve():
            shutil.copy2(source_log, target_log)
        stored_log = manifest_relative_path(manifest_file, target_log)
    else:
        stored_log = manifest_relative_path(manifest_file, source_log)

    case = {
        "case_name": case_name,
        "expected": expected,
        "dataset_type": dataset_type,
        "conditions": normalize_conditions(conditions),
        "bundle": bundle or "",
        "log": stored_log,
        "notes": notes or "",
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    cases.append(case)
    cases.sort(key=lambda item: str(item.get("case_name") or ""))
    manifest["cases"] = cases
    if "version" not in manifest:
        manifest["version"] = "0.1.0"
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {
        "status": "registered",
        "manifest_path": str(manifest_file),
        "case": case,
        "case_count": len(cases),
        "copied_log": copy_log,
    }


def load_or_create_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": "0.1.0", "cases": []}
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("Replay manifest must be a JSON object.")
    raw.setdefault("cases", [])
    return raw


def manifest_relative_path(manifest_path: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    root = manifest_path.parent.expanduser().resolve()
    try:
        return str(resolved.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(resolved)


def normalize_conditions(values: list[str]) -> list[str]:
    conditions: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in str(value).replace(",", " ").split():
            normalized = part.strip().lower().replace("-", "_")
            if normalized and normalized not in seen:
                seen.add(normalized)
                conditions.append(normalized)
    return conditions


def print_human(result: dict[str, Any]) -> None:
    case = result["case"]
    print(f"Replay case registered: {case['case_name']}")
    print(f"Manifest: {result['manifest_path']}")
    print(f"Expected: {case['expected']}")
    print(f"Dataset: {case['dataset_type']}")
    print(f"Conditions: {', '.join(case['conditions'])}")
    print(f"Log: {case['log']}")


def main() -> None:
    args = parse_args()
    try:
        result = register_replay_case(
            manifest_path=args.manifest,
            case_name=args.case_name,
            expected=args.expected,
            dataset_type=args.dataset_type,
            conditions=args.condition,
            log_path=args.log,
            bundle=args.bundle,
            notes=args.notes,
            copy_log=args.copy_log,
            case_dir=args.case_dir,
            replace=args.replace,
        )
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"Replay case registration failed: {exc}")
        raise SystemExit(1)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_human(result)


if __name__ == "__main__":
    main()
