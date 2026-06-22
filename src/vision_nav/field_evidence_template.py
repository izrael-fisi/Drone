from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from vision_nav.field_capture_metadata import capture_checklist_template, capture_metadata_template
from vision_nav.field_conditions import (
    REQUIRED_FIELD_CONDITIONS,
    expected_behavior_for_condition,
    label_for_condition,
    notes_for_condition,
)
from vision_nav.replay_case_manifest import sanitize_filename


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a ready-to-fill field replay manifest template for autonomy-readiness evidence."
    )
    parser.add_argument(
        "--output",
        default="data/replay_cases/field_manifest.template.json",
        help="Replay manifest template path to write.",
    )
    parser.add_argument("--site-name", default="field-site", help="Short site or test-area label.")
    parser.add_argument(
        "--bundle",
        default="TODO: mission_bundle path or map provenance",
        help="Bundle path or provenance label to place in each template case.",
    )
    parser.add_argument(
        "--log-root",
        default="field",
        help="Manifest-relative root folder for placeholder terrain log paths.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    parser.add_argument(
        "--seed-manifest",
        help="Optional active field_manifest.json path to seed from the template when it does not already exist.",
    )
    parser.add_argument(
        "--seed-force",
        action="store_true",
        help="Overwrite --seed-manifest if it already exists.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def build_field_evidence_manifest_template(
    *,
    site_name: str = "field-site",
    bundle: str = "TODO: mission_bundle path or map provenance",
    log_root: str = "field",
) -> dict[str, Any]:
    site_slug = sanitize_filename(site_name)
    cases = []
    for condition in REQUIRED_FIELD_CONDITIONS:
        expected = expected_behavior_for_condition(condition)
        case_name = sanitize_filename(f"{site_slug}-{condition}")
        cases.append(
            {
                "case_name": case_name,
                "expected": expected,
                "dataset_type": "field",
                "conditions": [condition],
                "bundle": bundle,
                "log": f"{log_root.rstrip('/')}/{condition}/terrain_matches.jsonl",
                "notes": notes_for_condition(condition),
                "template_label": label_for_condition(condition),
                "template_status": "replace_log_path_and_notes_after_capture",
                "capture_metadata": capture_metadata_template(
                    site_name=site_name,
                    condition=condition,
                    expected=expected,
                    bundle=bundle,
                    notes=notes_for_condition(condition),
                ),
                "capture_checklist": capture_checklist_template(condition),
            }
        )
    return {
        "version": "0.1.0",
        "description": (
            "Field replay evidence template for GNSS-denied terrain navigation. "
            "Replace placeholder log paths with captured Pi runtime/replay logs before running full gates."
        ),
        "template": {
            "schema_version": "vision_nav_field_evidence_template_v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "site_name": site_name,
            "required_conditions": REQUIRED_FIELD_CONDITIONS,
            "instructions": [
                "Capture or replay one real field log for each case.",
                "Keep dataset_type=field for real-world evidence.",
                "Use vision-nav-evaluate-replay-manifest --schema-only while logs are still placeholders.",
                "Run vision-nav-field-evidence-gate only after every placeholder log path points to an existing JSONL log.",
            ],
        },
        "cases": cases,
    }


def create_field_evidence_template(
    *,
    output_path: str | Path,
    site_name: str = "field-site",
    bundle: str = "TODO: mission_bundle path or map provenance",
    log_root: str = "field",
    force: bool = False,
    seed_manifest_path: str | Path | None = None,
    seed_force: bool = False,
) -> dict[str, Any]:
    output = Path(output_path).expanduser()
    if output.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing template: {output}. Use --force to replace it.")
    manifest = build_field_evidence_manifest_template(site_name=site_name, bundle=bundle, log_root=log_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    seed_manifest: dict[str, Any] | None = None
    if seed_manifest_path is not None:
        seed = Path(seed_manifest_path).expanduser()
        if seed.resolve() == output.resolve():
            seed_manifest = {
                "path": str(seed),
                "written": True,
                "reason": "same_as_template_output",
            }
        elif seed.exists() and not seed_force:
            seed_manifest = {
                "path": str(seed),
                "written": False,
                "reason": "already_exists",
            }
        else:
            seed.parent.mkdir(parents=True, exist_ok=True)
            seed.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            seed_manifest = {
                "path": str(seed),
                "written": True,
                "reason": "seeded_from_template",
            }
    return {
        "status": "written",
        "output_path": str(output),
        "case_count": len(manifest["cases"]),
        "required_conditions": list(REQUIRED_FIELD_CONDITIONS),
        "seed_manifest": seed_manifest,
        "manifest": manifest,
    }


def print_human(result: dict[str, Any]) -> None:
    print(f"Field evidence template: {result['output_path']}")
    print(f"Cases: {result['case_count']}")
    print(f"Required conditions: {', '.join(result['required_conditions'])}")
    print("Next:")
    print(f"  vision-nav-evaluate-replay-manifest --manifest {result['output_path']} --schema-only")
    print("  Replace placeholder logs, then run vision-nav-field-evidence-gate.")
    seed = result.get("seed_manifest")
    if isinstance(seed, dict):
        state = "seeded" if seed.get("written") else "left unchanged"
        print(f"Active manifest {state}: {seed.get('path')}")


def main() -> None:
    args = parse_args()
    try:
        result = create_field_evidence_template(
            output_path=args.output,
            site_name=args.site_name,
            bundle=args.bundle,
            log_root=args.log_root,
            force=args.force,
            seed_manifest_path=args.seed_manifest,
            seed_force=args.seed_force,
        )
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"Field evidence template creation failed: {exc}")
        raise SystemExit(1)
    if args.json:
        print(json.dumps({key: value for key, value in result.items() if key != "manifest"}, indent=2, sort_keys=True))
    else:
        print_human(result)


if __name__ == "__main__":
    main()
