from __future__ import annotations

from pathlib import Path
from typing import Any


EXPECTED_BEHAVIORS = {"good_map", "degraded", "wrong_map"}
DATASET_TYPES = {"field", "bench", "synthetic"}
REQUIRED_CASE_FIELDS = ("case_name", "expected", "dataset_type", "conditions", "log")
RECOMMENDED_CASE_FIELDS = ("bundle", "notes")

REPLAY_CASE_MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/izrael-fisi/Drone/data/replay_cases/replay_case_manifest.schema.json",
    "title": "Drone Vision Replay Case Manifest",
    "type": "object",
    "additionalProperties": True,
    "required": ["version", "cases"],
    "properties": {
        "version": {"type": "string"},
        "description": {"type": "string"},
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": list(REQUIRED_CASE_FIELDS),
                "properties": {
                    "case_name": {"type": "string", "minLength": 1},
                    "expected": {"type": "string", "enum": sorted(EXPECTED_BEHAVIORS)},
                    "dataset_type": {"type": "string", "enum": sorted(DATASET_TYPES)},
                    "conditions": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "condition_tags": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "bundle": {"type": "string"},
                    "log": {"type": "string", "minLength": 1},
                    "notes": {"type": "string"},
                    "registered_at": {"type": "string"},
                    "capture_metadata": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "capture_checklist": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            },
        },
    },
}


def replay_case_schema_path(repo_root: str | Path | None = None) -> Path:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    return root / "data" / "replay_cases" / "replay_case_manifest.schema.json"


def evaluate_replay_case_schema(raw: Any, *, manifest_path: str | Path | None = None) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return {
            "status": "failed",
            "schema_path": str(replay_case_schema_path()),
            "issue_count": 1,
            "issues": [schema_issue("error", "", "Replay manifest must be a JSON object.")],
        }

    if not isinstance(raw.get("version"), str) or not raw.get("version"):
        issues.append(schema_issue("error", "version", "Replay manifest must include a non-empty string version."))
    if "description" in raw and not isinstance(raw.get("description"), str):
        issues.append(schema_issue("error", "description", "description must be a string when present."))

    cases = raw.get("cases")
    if not isinstance(cases, list):
        issues.append(schema_issue("error", "cases", "Replay manifest must include a cases array."))
        cases = []

    names: dict[str, int] = {}
    for index, case in enumerate(cases):
        case_path = f"cases[{index}]"
        if not isinstance(case, dict):
            issues.append(schema_issue("error", case_path, "Replay case must be a JSON object."))
            continue
        for field in REQUIRED_CASE_FIELDS:
            if field not in case:
                issues.append(schema_issue("error", f"{case_path}.{field}", f"Replay case is missing required field {field}."))
        case_name = case.get("case_name")
        if isinstance(case_name, str) and case_name.strip():
            previous = names.get(case_name)
            if previous is not None:
                issues.append(
                    schema_issue(
                        "error",
                        f"{case_path}.case_name",
                        f"Duplicate case_name {case_name!r}; first used at cases[{previous}].",
                    )
                )
            names[case_name] = index
        elif "case_name" in case:
            issues.append(schema_issue("error", f"{case_path}.case_name", "case_name must be a non-empty string."))

        expected = case.get("expected")
        if "expected" in case and expected not in EXPECTED_BEHAVIORS:
            issues.append(
                schema_issue(
                    "error",
                    f"{case_path}.expected",
                    f"expected must be one of {', '.join(sorted(EXPECTED_BEHAVIORS))}.",
                )
            )

        dataset_type = case.get("dataset_type")
        if "dataset_type" in case and dataset_type not in DATASET_TYPES:
            issues.append(
                schema_issue(
                    "error",
                    f"{case_path}.dataset_type",
                    f"dataset_type must be one of {', '.join(sorted(DATASET_TYPES))}.",
                )
            )

        conditions = case.get("conditions")
        if "conditions" in case:
            issues.extend(validate_string_array(f"{case_path}.conditions", conditions, required_non_empty=True))

        for field in ("condition_tags", "tags"):
            if field in case:
                issues.extend(validate_string_array(f"{case_path}.{field}", case.get(field), required_non_empty=False))

        log = case.get("log")
        if "log" in case and (not isinstance(log, str) or not log.strip()):
            issues.append(schema_issue("error", f"{case_path}.log", "log must be a non-empty string path."))

        for field in ("bundle", "notes", "registered_at"):
            if field in case and not isinstance(case.get(field), str):
                issues.append(schema_issue("error", f"{case_path}.{field}", f"{field} must be a string when present."))

        for field in ("capture_metadata", "capture_checklist"):
            if field in case and not isinstance(case.get(field), dict):
                issues.append(schema_issue("error", f"{case_path}.{field}", f"{field} must be an object when present."))

        for field in RECOMMENDED_CASE_FIELDS:
            if not case.get(field):
                issues.append(schema_issue("warning", f"{case_path}.{field}", f"Replay case should include {field} provenance."))

    status = "failed" if any(issue["severity"] == "error" for issue in issues) else "passed"
    if status == "passed" and issues:
        status = "degraded"
    return {
        "status": status,
        "schema_path": str(replay_case_schema_path()),
        "issue_count": len(issues),
        "issues": issues,
    }


def schema_issue(severity: str, path: str, message: str) -> dict[str, str]:
    return {"severity": severity, "path": path, "message": message}


def validate_string_array(path: str, value: Any, *, required_non_empty: bool) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(value, list) or (required_non_empty and not value):
        requirement = "a non-empty array" if required_non_empty else "an array"
        issues.append(schema_issue("error", path, f"{path.rsplit('.', 1)[-1]} must be {requirement}."))
        return issues
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(schema_issue("error", f"{path}[{index}]", "entries must be non-empty strings."))
    return issues
