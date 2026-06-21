from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any


EXTERNAL_NAV = 6
NONE = 0
BARO = 1
COMPASS = 1

ARDUPILOT_PARAM_REFERENCES = {
    "non_gps_position": "https://ardupilot.org/dev/docs/mavlink-nongps-position-estimation.html",
    "ekf_sources": "https://ardupilot.org/copter/docs/common-ekf-sources.html",
    "gps_non_gps_transitions": "https://ardupilot.org/copter/docs/common-non-gps-to-gps.html",
    "home_and_origin": "https://ardupilot.org/dev/docs/mavlink-get-set-home-and-origin.html",
    "mavlink_odometry": "https://mavlink.io/en/messages/common.html#ODOMETRY",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check ArduPilot parameter export readiness for vision-nav ExternalNav bench tests."
    )
    parser.add_argument("--params", required=True, help="ArduPilot/Mission Planner parameter export file.")
    parser.add_argument("--source-set", type=int, default=1, choices=[1, 2, 3], help="EK3_SRC source set to check.")
    parser.add_argument("--gnss-denied", action="store_true", help="Warn when GPS appears enabled for GNSS-denied validation.")
    parser.add_argument("--vision-height-valid", action="store_true", help="Treat ExternalNav vertical position as validated.")
    parser.add_argument("--vision-velocity-valid", action="store_true", help="Treat ExternalNav velocity as validated.")
    parser.add_argument("--vision-yaw-valid", action="store_true", help="Treat ExternalNav yaw as validated.")
    parser.add_argument("--extrinsics-measured", action="store_true", help="Treat VISO_POS_X/Y/Z as measured even when all zero.")
    parser.add_argument(
        "--require-source-switch",
        action="store_true",
        help="Require an RCx_OPTION=90 switch for manual EKF source-set testing.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def load_ardupilot_params(path: str | Path) -> dict[str, float | str]:
    source = Path(path).expanduser()
    text = source.read_text(errors="replace")
    try:
        return params_from_json(json.loads(text))
    except Exception:
        return params_from_text(text)


def params_from_json(value: Any) -> dict[str, float | str]:
    if isinstance(value, dict):
        if isinstance(value.get("parameters"), list):
            output: dict[str, float | str] = {}
            for item in value["parameters"]:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("param_id") or item.get("id")
                    raw_value = item.get("value")
                    if name is not None and raw_value is not None:
                        output[str(name)] = numeric_or_string(raw_value)
            return output
        if isinstance(value.get("parameters"), dict):
            return {str(key): numeric_or_string(raw) for key, raw in value["parameters"].items()}
        return {str(key): numeric_or_string(raw) for key, raw in value.items() if looks_like_param_name(str(key))}
    raise ValueError("Unsupported JSON parameter format")


def params_from_text(text: str) -> dict[str, float | str]:
    output: dict[str, float | str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        tokens = re.split(r"[\s,;=]+", stripped)
        for index, token in enumerate(tokens):
            name = token.strip()
            if not looks_like_param_name(name):
                continue
            value = first_numeric_after(tokens, index + 1)
            if value is not None:
                output[name] = value
                break
    return output


def check_ardupilot_external_nav_params(
    params: dict[str, float | str],
    *,
    source_set: int = 1,
    gnss_denied: bool = False,
    vision_height_valid: bool = False,
    vision_velocity_valid: bool = False,
    vision_yaw_valid: bool = False,
    extrinsics_measured: bool = False,
    require_source_switch: bool = False,
) -> dict[str, Any]:
    if source_set not in {1, 2, 3}:
        raise ValueError("source_set must be 1, 2, or 3")

    issues: list[dict[str, str]] = []
    source_prefix = f"EK3_SRC{source_set}"

    ek3_enable = optional_int(params.get("EK3_ENABLE"))
    ek2_enable = optional_int(params.get("EK2_ENABLE"))
    ahrs_ekf_type = optional_int(params.get("AHRS_EKF_TYPE"))
    viso_type = optional_int(params.get("VISO_TYPE"))
    viso_pos = [optional_float(params.get(name)) for name in ("VISO_POS_X", "VISO_POS_Y", "VISO_POS_Z")]
    src_posxy = optional_int(params.get(f"{source_prefix}_POSXY"))
    src_velxy = optional_int(params.get(f"{source_prefix}_VELXY"))
    src_posz = optional_int(params.get(f"{source_prefix}_POSZ"))
    src_velz = optional_int(params.get(f"{source_prefix}_VELZ"))
    src_yaw = optional_int(params.get(f"{source_prefix}_YAW"))
    src_options = optional_int(params.get("EK3_SRC_OPTIONS"))
    gps_type = optional_int(params.get("GPS_TYPE"))
    source_switch_channels = rc_source_switch_channels(params)

    if ek3_enable is not None and ek3_enable != 1:
        add_issue(issues, "error", "EK3_ENABLE is not 1; EKF3 must be active for the ExternalNav source-set path.")
    elif ek3_enable is None:
        add_issue(issues, "warning", "EK3_ENABLE is missing; EKF3 state is unknown.")

    if ek2_enable is not None and ek2_enable != 0:
        add_issue(issues, "warning", "EK2_ENABLE is not 0; confirm EKF3 is the active estimator.")
    if ahrs_ekf_type is not None and ahrs_ekf_type != 3:
        add_issue(issues, "error", "AHRS_EKF_TYPE is not 3; ArduPilot EKF source-set docs expect EKF3.")
    elif ahrs_ekf_type is None:
        add_issue(issues, "warning", "AHRS_EKF_TYPE is missing; active EKF type is unknown.")

    if viso_type is None:
        add_issue(issues, "error", "VISO_TYPE is missing; ArduPilot ExternalNav visual odometry input is not documented.")
    elif viso_type != 3:
        add_issue(issues, "error", "VISO_TYPE is not 3; official ExternalNav MAVLink setup recommends VOXL-style visual odometry input.")

    if any(value is None for value in viso_pos):
        add_issue(issues, "warning", "One or more VISO_POS_X/Y/Z camera-to-body offsets are missing.")
    elif not extrinsics_measured and all(abs(float(value or 0.0)) < 1e-6 for value in viso_pos):
        add_issue(issues, "warning", "VISO_POS_X/Y/Z are all zero; confirm measured camera-to-body geometry.")

    if src_posxy != EXTERNAL_NAV:
        add_issue(issues, "error", f"{source_prefix}_POSXY is not 6; horizontal position is not sourced from ExternalNav.")

    if vision_velocity_valid:
        if src_velxy != EXTERNAL_NAV:
            add_issue(issues, "error", f"{source_prefix}_VELXY is not 6 while ExternalNav velocity is marked valid.")
        if src_velz != EXTERNAL_NAV:
            add_issue(issues, "error", f"{source_prefix}_VELZ is not 6 while ExternalNav velocity is marked valid.")
    else:
        if src_velxy == EXTERNAL_NAV:
            add_issue(issues, "warning", f"{source_prefix}_VELXY fuses ExternalNav velocity before velocity output is marked valid.")
        elif src_velxy is not None and src_velxy != NONE:
            add_issue(issues, "warning", f"{source_prefix}_VELXY is {src_velxy}; expected 0 until velocity output is validated.")
        if src_velz == EXTERNAL_NAV:
            add_issue(issues, "warning", f"{source_prefix}_VELZ fuses ExternalNav vertical velocity before velocity output is marked valid.")
        elif src_velz is not None and src_velz != NONE:
            add_issue(issues, "warning", f"{source_prefix}_VELZ is {src_velz}; expected 0 until velocity output is validated.")

    if vision_height_valid:
        if src_posz != EXTERNAL_NAV:
            add_issue(issues, "error", f"{source_prefix}_POSZ is not 6 while ExternalNav vertical position is marked valid.")
    else:
        if src_posz == EXTERNAL_NAV:
            add_issue(issues, "warning", f"{source_prefix}_POSZ fuses ExternalNav height before visual height is marked valid.")
        elif src_posz is not None and src_posz != BARO:
            add_issue(issues, "warning", f"{source_prefix}_POSZ is {src_posz}; barometer height is the conservative default.")

    if vision_yaw_valid:
        if src_yaw != EXTERNAL_NAV:
            add_issue(issues, "error", f"{source_prefix}_YAW is not 6 while ExternalNav yaw is marked valid.")
    else:
        if src_yaw == EXTERNAL_NAV:
            add_issue(issues, "warning", f"{source_prefix}_YAW fuses ExternalNav yaw before vision yaw is marked valid.")
        elif src_yaw is not None and src_yaw != COMPASS:
            add_issue(issues, "warning", f"{source_prefix}_YAW is {src_yaw}; compass yaw is the conservative default.")

    if src_options is None:
        add_issue(issues, "warning", "EK3_SRC_OPTIONS is missing; velocity-fusion/source-alignment options are unknown.")
    elif src_options & 1:
        add_issue(issues, "warning", "EK3_SRC_OPTIONS FuseAllVelocities bit is set; confirm all fused velocity sources share one frame.")

    if gnss_denied and gps_type is not None and gps_type != 0:
        add_issue(issues, "warning", "GPS_TYPE is nonzero while GNSS-denied validation is requested.")

    if require_source_switch and not source_switch_channels:
        add_issue(issues, "error", "No RCx_OPTION=90 source switch found for manual EKF source-set testing.")

    status = "failed" if any(issue["severity"] == "error" for issue in issues) else "passed"
    if status == "passed" and any(issue["severity"] == "warning" for issue in issues):
        status = "degraded"

    return {
        "status": status,
        "parameters": {
            "source_set": source_set,
            "EK3_ENABLE": ek3_enable,
            "EK2_ENABLE": ek2_enable,
            "AHRS_EKF_TYPE": ahrs_ekf_type,
            "VISO_TYPE": viso_type,
            "VISO_POS_XYZ": viso_pos,
            f"{source_prefix}_POSXY": src_posxy,
            f"{source_prefix}_VELXY": src_velxy,
            f"{source_prefix}_POSZ": src_posz,
            f"{source_prefix}_VELZ": src_velz,
            f"{source_prefix}_YAW": src_yaw,
            "EK3_SRC_OPTIONS": src_options,
            "GPS_TYPE": gps_type,
            "source_switch_channels": source_switch_channels,
        },
        "config": {
            "gnss_denied": gnss_denied,
            "vision_height_valid": vision_height_valid,
            "vision_velocity_valid": vision_velocity_valid,
            "vision_yaw_valid": vision_yaw_valid,
            "extrinsics_measured": extrinsics_measured,
            "require_source_switch": require_source_switch,
        },
        "issues": issues,
        "references": dict(ARDUPILOT_PARAM_REFERENCES),
    }


def evaluate_ardupilot_param_file(path: str | Path, **kwargs: Any) -> dict[str, Any]:
    params = load_ardupilot_params(path)
    report = check_ardupilot_external_nav_params(params, **kwargs)
    report["param_file"] = str(Path(path).expanduser())
    report["param_count"] = len(params)
    return report


def rc_source_switch_channels(params: dict[str, float | str]) -> list[str]:
    channels: list[str] = []
    for index in range(1, 17):
        name = f"RC{index}_OPTION"
        if optional_int(params.get(name)) == 90:
            channels.append(name)
    return channels


def looks_like_param_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value))


def first_numeric_after(tokens: list[str], start: int) -> float | None:
    for token in tokens[start:]:
        value = optional_float(token)
        if value is not None:
            return value
    return None


def numeric_or_string(value: Any) -> float | str:
    numeric = optional_float(value)
    if numeric is not None:
        return numeric
    return str(value)


def optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        output = float(value)
        return output if math.isfinite(output) else None
    except (TypeError, ValueError):
        return None


def optional_int(value: Any) -> int | None:
    number = optional_float(value)
    if number is None:
        return None
    return int(number)


def add_issue(issues: list[dict[str, str]], severity: str, message: str) -> None:
    issues.append({"severity": severity, "message": message})


def print_human(report: dict[str, Any]) -> None:
    print(f"ArduPilot ExternalNav parameter check: {report.get('param_file')}")
    print(f"Status: {report['status']}")
    params = report["parameters"]
    source_set = params["source_set"]
    source_prefix = f"EK3_SRC{source_set}"
    print(f"Source set: EK3_SRC{source_set}")
    print(f"VISO_TYPE: {params['VISO_TYPE']}")
    print(f"{source_prefix}_POSXY: {params[source_prefix + '_POSXY']}")
    print(f"{source_prefix}_VELXY: {params[source_prefix + '_VELXY']}")
    print(f"{source_prefix}_POSZ: {params[source_prefix + '_POSZ']}")
    print(f"{source_prefix}_VELZ: {params[source_prefix + '_VELZ']}")
    print(f"{source_prefix}_YAW: {params[source_prefix + '_YAW']}")
    for issue in report["issues"]:
        print(f"[{issue['severity'].upper()}] {issue['message']}")


def main() -> None:
    args = parse_args()
    report = evaluate_ardupilot_param_file(
        args.params,
        source_set=args.source_set,
        gnss_denied=args.gnss_denied,
        vision_height_valid=args.vision_height_valid,
        vision_velocity_valid=args.vision_velocity_valid,
        vision_yaw_valid=args.vision_yaw_valid,
        extrinsics_measured=args.extrinsics_measured,
        require_source_switch=args.require_source_switch,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
