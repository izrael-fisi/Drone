from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vision_nav.bench_readiness import check_gnss_denied_plan


DEFAULT_MISSION_PLAN_CANDIDATES = (
    "mission/mission_plan.json",
    "mission/qgc.plan",
    "mission_plan.json",
    "qgc.plan",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check GNSS-denied mission-prep metadata.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle", help="Terrain mission bundle containing mission/mission_plan.json or mission/qgc.plan.")
    source.add_argument("--plan", help="Mission Planner JSON or QGroundControl .plan file.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def evaluate_gnss_denied_plan(
    *,
    bundle_path: str | Path | None = None,
    plan_path: str | Path | None = None,
) -> dict[str, Any]:
    if bool(bundle_path) == bool(plan_path):
        raise ValueError("Provide exactly one of bundle_path or plan_path.")

    if plan_path is not None:
        plan_file = Path(plan_path).expanduser()
        plan_summary = load_plan_summary(plan_file)
        bundle = {"mission_plan": plan_summary}
        source_path = plan_file
    else:
        bundle_dir = Path(str(bundle_path)).expanduser()
        plan_summary = load_bundle_plan_summary(bundle_dir)
        bundle = {"bundle_id": bundle_dir.name, "mission_plan": plan_summary}
        source_path = bundle_dir

    check = check_gnss_denied_plan({"bundle": bundle})
    details = check.get("details") if isinstance(check.get("details"), dict) else {}
    return {
        "schema_version": "vision_nav_gnss_denied_plan_check_v1",
        "status": check.get("status"),
        "source_path": str(source_path),
        "mission_plan": plan_summary,
        "check": check,
        "missing_checks": details.get("missing_checks") or [],
        "failed_checks": details.get("failed_checks") or [],
        "field_ready": details.get("field_ready") or {},
    }


def load_bundle_plan_summary(bundle_dir: Path) -> dict[str, Any]:
    seen: set[str] = set()
    for candidate in DEFAULT_MISSION_PLAN_CANDIDATES:
        if candidate in seen:
            continue
        seen.add(candidate)
        path = bundle_dir / candidate
        if not path.exists():
            continue
        return load_plan_summary(path, relative_path=candidate)
    return {"status": "not_provided", "path": None, "mission_item_count": 0, "gnss_denied": {"status": "not_provided", "checks": []}}


def load_plan_summary(path: Path, *, relative_path: str | None = None) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "failed", "path": relative_path or str(path), "error": str(exc)}
    if not isinstance(plan, dict):
        return {"status": "failed", "path": relative_path or str(path), "error": "mission plan root is not a JSON object"}
    return {
        "status": "loaded",
        "path": relative_path or str(path),
        "mission_item_count": mission_item_count(plan),
        "gnss_denied": summarize_gnss_denied_plan(plan),
    }


def summarize_gnss_denied_plan(plan: dict[str, Any]) -> dict[str, Any]:
    raw = plan.get("gnss_denied") or plan.get("gnssDenied")
    vision_navigation = plan.get("visionNavigation")
    if not isinstance(raw, dict) and isinstance(vision_navigation, dict):
        raw = vision_navigation.get("gnss_denied") or vision_navigation.get("gnssDenied")
    if not isinstance(raw, dict):
        return {"status": "not_provided", "checks": []}

    checks = []
    for check in raw.get("checks") or []:
        if not isinstance(check, dict):
            continue
        checks.append(
            {
                "name": check.get("name"),
                "label": check.get("label"),
                "status": check.get("status"),
            }
        )

    return {
        "status": raw.get("status"),
        "checks": checks,
        "satellite_source_disabled": raw.get("satellite_source_disabled") is True,
        "map_position_reset_set": raw.get("map_position_reset") is not None,
        "home_position_set": raw.get("home_position") is not None,
        "heading_set": isinstance(raw.get("heading_deg"), (int, float)),
        "estimator_health": raw.get("estimator_health"),
        "updated_at": raw.get("updated_at"),
    }


def mission_item_count(plan: dict[str, Any]) -> int:
    mission = plan.get("mission") if isinstance(plan.get("mission"), dict) else {}
    items = mission.get("items")
    if isinstance(items, list):
        return len(items)
    qgc_items = mission.get("plannedHomePosition")
    if qgc_items is not None:
        return len([item for item in mission.get("items") or [] if isinstance(item, dict)])
    return 0


def print_human(report: dict[str, Any]) -> None:
    check = report.get("check") if isinstance(report.get("check"), dict) else {}
    mission_plan = report.get("mission_plan") if isinstance(report.get("mission_plan"), dict) else {}
    print(f"GNSS-denied mission prep: {report.get('source_path')}")
    print(f"Status: {report.get('status')}")
    print(f"Mission plan: {mission_plan.get('path') or 'not provided'}")
    print(f"Message: {check.get('message') or 'n/a'}")
    missing = report.get("missing_checks") or []
    failed_checks = report.get("failed_checks") or []
    if missing:
        print(f"Missing checks: {', '.join(str(item) for item in missing)}")
    if failed_checks:
        print(f"Failed checks: {', '.join(str(item) for item in failed_checks)}")
    field_ready = report.get("field_ready") if isinstance(report.get("field_ready"), dict) else {}
    if field_ready:
        print("Field readiness:")
        for key, value in field_ready.items():
            print(f"- {key}: {'ready' if value else 'not_ready'}")
    print(f"__VISION_NAV_GNSS_DENIED_PLAN__={report.get('source_path')}")
    print(f"__VISION_NAV_GNSS_DENIED_PLAN_STATUS__={report.get('status')}")


def main() -> None:
    args = parse_args()
    report = evaluate_gnss_denied_plan(bundle_path=args.bundle, plan_path=args.plan)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report.get("status") != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
