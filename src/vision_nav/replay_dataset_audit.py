from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any

from vision_nav.field_capture_metadata import audit_capture_metadata
from vision_nav.replay_case_manifest import load_replay_case_manifest


@dataclass(frozen=True)
class ReplayCoverageRequirement:
    key: str
    label: str
    expected: tuple[str, ...]
    aliases: tuple[str, ...]


REPLAY_COVERAGE_REQUIREMENTS: tuple[ReplayCoverageRequirement, ...] = (
    ReplayCoverageRequirement(
        key="good_texture",
        label="Good texture / matching map",
        expected=("good_map",),
        aliases=("good_texture", "clear_texture", "nominal_texture", "clear-ground-texture"),
    ),
    ReplayCoverageRequirement(
        key="low_texture",
        label="Low texture / weak features",
        expected=("degraded",),
        aliases=("low_texture", "low-texture", "weak_texture", "sparse_features"),
    ),
    ReplayCoverageRequirement(
        key="blur",
        label="Blur / motion blur",
        expected=("degraded",),
        aliases=("blur", "motion_blur", "defocus", "soft_focus"),
    ),
    ReplayCoverageRequirement(
        key="seasonal_change",
        label="Seasonal or map age change",
        expected=("degraded", "wrong_map"),
        aliases=("seasonal_change", "seasonal", "map_age", "stale_map", "vegetation_change"),
    ),
    ReplayCoverageRequirement(
        key="lighting_change",
        label="Lighting or shadow change",
        expected=("degraded",),
        aliases=("lighting_change", "lighting", "shadow", "glare", "low_sun"),
    ),
    ReplayCoverageRequirement(
        key="altitude_scale_change",
        label="Altitude / visual scale change",
        expected=("good_map", "degraded"),
        aliases=("altitude_scale_change", "altitude_change", "scale_change", "height_change"),
    ),
    ReplayCoverageRequirement(
        key="repeated_patterns",
        label="Repeated patterns / ambiguity",
        expected=("degraded", "wrong_map"),
        aliases=("repeated_patterns", "repeating_pattern", "ambiguous_pattern", "repetitive_texture"),
    ),
    ReplayCoverageRequirement(
        key="wrong_map",
        label="Wrong map rejection",
        expected=("wrong_map",),
        aliases=("wrong_map", "wrong-map", "map_mismatch", "incorrect_map"),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit replay-case manifest coverage against field-validation requirements.")
    parser.add_argument("--manifest", required=True, help="Replay case manifest JSON.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Allow synthetic/bench cases to satisfy coverage. Use only for smoke testing the audit itself.",
    )
    parser.add_argument(
        "--skip-log-exists",
        action="store_true",
        help="Do not fail cases whose replay log path is missing.",
    )
    parser.add_argument(
        "--require-capture-metadata",
        action="store_true",
        help="Fail field cases that still have missing or placeholder capture metadata.",
    )
    return parser.parse_args()


def audit_replay_dataset_coverage(
    manifest_path: str | Path,
    *,
    require_field_logs: bool = True,
    require_log_exists: bool = True,
    require_capture_metadata: bool = False,
) -> dict[str, Any]:
    manifest = load_replay_case_manifest(manifest_path)
    cases = [normalize_case(case) for case in manifest["cases"]]
    requirement_reports = [
        audit_requirement(requirement, cases, require_field_logs=require_field_logs)
        for requirement in REPLAY_COVERAGE_REQUIREMENTS
    ]
    case_issues = audit_case_paths(
        cases,
        require_log_exists=require_log_exists,
        require_capture_metadata=require_capture_metadata,
    )
    missing = [report for report in requirement_reports if report["status"] == "missing"]
    synthetic_only = [report for report in requirement_reports if report["status"] == "synthetic_only"]
    failed_case_paths = [issue for issue in case_issues if issue["severity"] == "error"]
    capture_metadata_issue_count = sum(
        1
        for issue in case_issues
        if str(issue.get("field") or "").startswith("capture_metadata")
    )

    schema = manifest.get("schema") or {}
    schema_failed = schema.get("status") == "failed"

    if missing or synthetic_only or failed_case_paths or schema_failed:
        status = "failed"
    else:
        status = "passed"

    return {
        "status": status,
        "manifest_path": manifest["manifest_path"],
        "version": manifest.get("version"),
        "schema": schema,
        "case_count": len(cases),
        "field_case_count": sum(1 for case in cases if case["dataset_type"] == "field"),
        "synthetic_case_count": sum(1 for case in cases if case["dataset_type"] == "synthetic"),
        "bench_case_count": sum(1 for case in cases if case["dataset_type"] == "bench"),
        "capture_metadata_issue_count": capture_metadata_issue_count,
        "requirements": requirement_reports,
        "case_issues": case_issues,
        "config": {
            "require_field_logs": require_field_logs,
            "require_log_exists": require_log_exists,
            "require_capture_metadata": require_capture_metadata,
            "required_conditions": [asdict(requirement) for requirement in REPLAY_COVERAGE_REQUIREMENTS],
        },
    }


def normalize_case(case: dict[str, Any]) -> dict[str, Any]:
    expected = str(case.get("expected") or "")
    bundle = str(case.get("bundle") or "")
    dataset_type = normalize_dataset_type(case)
    tags = case_tokens(case)
    return {
        "case_name": str(case.get("case_name") or ""),
        "expected": expected,
        "bundle": bundle,
        "log": case.get("log"),
        "notes": case.get("notes"),
        "dataset_type": dataset_type,
        "conditions": sorted(tags),
        "capture_metadata": case.get("capture_metadata"),
    }


def normalize_dataset_type(case: dict[str, Any]) -> str:
    value = str(case.get("dataset_type") or "").strip().lower()
    if value in {"field", "bench", "synthetic"}:
        return value
    tokens = case_tokens(case, include_dataset_type=False)
    bundle = str(case.get("bundle") or "").lower()
    if "synthetic" in tokens or bundle == "synthetic":
        return "synthetic"
    if "bench" in tokens or "sitl" in tokens or "sim" in tokens:
        return "bench"
    if "field" in tokens or "flight" in tokens or "outdoor" in tokens:
        return "field"
    return "unknown"


def case_tokens(case: dict[str, Any], *, include_dataset_type: bool = True) -> set[str]:
    tokens: set[str] = set()
    for key in ("conditions", "condition_tags", "tags"):
        value = case.get(key)
        if isinstance(value, list):
            for item in value:
                tokens.update(tokenize(str(item)))
                tokens.update(alias_forms(str(item)))
        elif isinstance(value, str):
            tokens.update(tokenize(value))
            tokens.update(alias_forms(value))
    for key in ("case_name", "notes", "bundle"):
        value = case.get(key)
        if value is not None:
            tokens.update(tokenize(str(value)))
    if include_dataset_type and case.get("dataset_type") is not None:
        tokens.update(tokenize(str(case.get("dataset_type"))))
    return tokens


def tokenize(value: str) -> set[str]:
    text = value.lower()
    raw = [part for part in re.split(r"[^a-z0-9]+", text) if part]
    tokens = set(raw)
    for left, right in zip(raw, raw[1:]):
        tokens.add(f"{left}_{right}")
        tokens.add(f"{left}-{right}")
    return tokens


def audit_requirement(
    requirement: ReplayCoverageRequirement,
    cases: list[dict[str, Any]],
    *,
    require_field_logs: bool,
) -> dict[str, Any]:
    aliases = set()
    for alias in requirement.aliases:
        aliases.update(alias_forms(alias))
    matches = [
        case
        for case in cases
        if case["expected"] in requirement.expected and aliases.intersection(set(case["conditions"]))
    ]
    field_matches = [case for case in matches if case["dataset_type"] == "field"]
    if not matches:
        status = "missing"
    elif require_field_logs and not field_matches:
        status = "synthetic_only"
    else:
        status = "covered"
    return {
        "key": requirement.key,
        "label": requirement.label,
        "expected": list(requirement.expected),
        "status": status,
        "case_count": len(matches),
        "field_case_count": len(field_matches),
        "cases": [
            {
                "case_name": case["case_name"],
                "expected": case["expected"],
                "dataset_type": case["dataset_type"],
                "log": case["log"],
            }
            for case in matches
        ],
    }


def audit_case_paths(
    cases: list[dict[str, Any]],
    *,
    require_log_exists: bool,
    require_capture_metadata: bool,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for case in cases:
        case_name = str(case.get("case_name") or "unnamed-case")
        dataset_type = case.get("dataset_type")
        if dataset_type == "unknown":
            issues.append(
                {
                    "severity": "warning",
                    "case_name": case_name,
                    "message": "Replay case has no dataset_type and could not be inferred.",
                }
            )
        log = case.get("log")
        if not log:
            issues.append({"severity": "error", "case_name": case_name, "message": "Replay case is missing a log path."})
        elif require_log_exists and not Path(str(log)).expanduser().exists():
            issues.append({"severity": "error", "case_name": case_name, "message": f"Replay log does not exist: {log}"})
        if not case.get("bundle"):
            issues.append({"severity": "warning", "case_name": case_name, "message": "Replay case is missing bundle provenance."})
        if require_capture_metadata and dataset_type == "field":
            for metadata_issue in audit_capture_metadata(
                case.get("capture_metadata"),
                conditions=case.get("conditions") or [],
                expected=str(case.get("expected") or ""),
            ):
                issues.append(
                    {
                        "severity": metadata_issue["severity"],
                        "case_name": case_name,
                        "message": metadata_issue["message"],
                        "field": metadata_issue.get("field", "capture_metadata"),
                    }
                )
    return issues


def alias_forms(value: str) -> set[str]:
    parts = [part for part in re.split(r"[^a-z0-9]+", value.lower()) if part]
    if not parts:
        return set()
    joined_underscore = "_".join(parts)
    joined_dash = "-".join(parts)
    return {joined_underscore, joined_dash}


def print_human(report: dict[str, Any]) -> None:
    print(f"Replay coverage audit: {report['manifest_path']}")
    print(f"Status: {report['status']}")
    print(
        "Cases: "
        f"{report['case_count']} total, "
        f"{report['field_case_count']} field, "
        f"{report['bench_case_count']} bench, "
        f"{report['synthetic_case_count']} synthetic"
    )
    for requirement in report["requirements"]:
        print(
            f"- {requirement['key']}: {requirement['status']} "
            f"({requirement['field_case_count']} field / {requirement['case_count']} total)"
        )
    for issue in report["case_issues"]:
        print(f"[{issue['severity'].upper()}] {issue['case_name']}: {issue['message']}")


def main() -> None:
    args = parse_args()
    report = audit_replay_dataset_coverage(
        args.manifest,
        require_field_logs=not args.allow_synthetic,
        require_log_exists=not args.skip_log_exists,
        require_capture_metadata=args.require_capture_metadata,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
