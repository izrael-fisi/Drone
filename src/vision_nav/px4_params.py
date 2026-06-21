from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any


EKF2_EV_HORIZONTAL_POS_BIT = 0
EKF2_EV_VERTICAL_POS_BIT = 1
EKF2_EV_VELOCITY_BIT = 2
EKF2_EV_YAW_BIT = 3

PX4_PARAM_REFERENCES = {
    "external_position": "https://docs.px4.io/main/en/ros/external_position_estimation",
    "ekf2_tuning": "https://docs.px4.io/main/en/advanced_config/tuning_the_ecl_ekf",
    "parameter_reference": "https://docs.px4.io/main/en/advanced_config/parameter_reference",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check PX4 parameter export readiness for vision-nav external-position bench tests.")
    parser.add_argument("--params", required=True, help="PX4/QGroundControl parameter export file.")
    parser.add_argument("--gnss-denied", action="store_true", help="Require GNSS fusion to be disabled for controlled GNSS-denied validation.")
    parser.add_argument("--vision-height-valid", action="store_true", help="Treat vision vertical position/height reference as intentionally valid.")
    parser.add_argument("--vision-velocity-valid", action="store_true", help="Treat external-vision velocity fusion as intentionally valid.")
    parser.add_argument("--vision-yaw-valid", action="store_true", help="Treat external-vision yaw fusion as intentionally valid.")
    parser.add_argument("--extrinsics-measured", action="store_true", help="Treat EKF2_EV_POS_X/Y/Z as measured even if all exported values are zero.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def load_px4_params(path: str | Path) -> dict[str, float | str]:
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
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        tokens = re.split(r"[\s,;]+", stripped)
        for index, token in enumerate(tokens):
            name = token.strip()
            if not looks_like_param_name(name):
                continue
            value = first_numeric_after(tokens, index + 1)
            if value is not None:
                output[name] = value
                break
    return output


def check_px4_external_vision_params(
    params: dict[str, float | str],
    *,
    gnss_denied: bool = False,
    vision_height_valid: bool = False,
    vision_velocity_valid: bool = False,
    vision_yaw_valid: bool = False,
    extrinsics_measured: bool = False,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    ev_ctrl = optional_int(params.get("EKF2_EV_CTRL"))
    hgt_ref = optional_int(params.get("EKF2_HGT_REF"))
    gps_ctrl = optional_int(params.get("EKF2_GPS_CTRL"))
    noise_mode = optional_int(params.get("EKF2_EV_NOISE_MD"))
    ev_delay = optional_float(params.get("EKF2_EV_DELAY"))

    if ev_ctrl is None:
        add_issue(issues, "error", "EKF2_EV_CTRL is missing; external-vision fusion state is unknown.")
        enabled_bits: list[int] = []
    else:
        enabled_bits = [bit for bit in range(4) if bit_is_set(ev_ctrl, bit)]
        if not bit_is_set(ev_ctrl, EKF2_EV_HORIZONTAL_POS_BIT):
            add_issue(issues, "error", "EKF2_EV_CTRL bit 0 is not set; horizontal external-vision position will not be fused.")
        if bit_is_set(ev_ctrl, EKF2_EV_VERTICAL_POS_BIT) and not vision_height_valid:
            add_issue(issues, "warning", "EKF2_EV_CTRL bit 1 fuses vision vertical position, but this project only treats vision height as valid after explicit vertical validation.")
        if bit_is_set(ev_ctrl, EKF2_EV_VELOCITY_BIT) and not vision_velocity_valid:
            add_issue(issues, "warning", "EKF2_EV_CTRL bit 2 fuses external-vision velocity, but current runtime velocity output must be validated first.")
        if bit_is_set(ev_ctrl, EKF2_EV_YAW_BIT) and not vision_yaw_valid:
            add_issue(issues, "warning", "EKF2_EV_CTRL bit 3 fuses external-vision yaw, but map/vision yaw must be validated before use.")

    if hgt_ref is None:
        add_issue(issues, "warning", "EKF2_HGT_REF is missing; PX4 EKF height-reference behavior is unknown.")
    elif hgt_ref == 1 and gnss_denied:
        add_issue(issues, "error", "EKF2_HGT_REF is GPS while GNSS-denied validation is requested.")
    elif hgt_ref == 3 and not vision_height_valid:
        add_issue(issues, "error", "EKF2_HGT_REF is Vision but vision height has not been marked valid.")
    elif hgt_ref not in {0, 1, 2, 3}:
        add_issue(issues, "warning", f"EKF2_HGT_REF has unexpected value {hgt_ref}.")

    if gps_ctrl is None:
        add_issue(issues, "warning", "EKF2_GPS_CTRL is missing; GNSS aiding state is unknown.")
    elif gnss_denied and gps_ctrl != 0:
        add_issue(issues, "error", "EKF2_GPS_CTRL is nonzero while GNSS-denied validation is requested.")

    if noise_mode is None:
        add_issue(issues, "warning", "EKF2_EV_NOISE_MD is missing; covariance source is unknown.")
    elif noise_mode != 0:
        add_issue(issues, "warning", "EKF2_EV_NOISE_MD is not 0; PX4 may use parameter noise instead of message covariance.")

    if ev_delay is None:
        add_issue(issues, "warning", "EKF2_EV_DELAY is missing; camera processing delay is not documented in parameters.")
    elif ev_delay < -200.0 or ev_delay > 500.0:
        add_issue(issues, "warning", f"EKF2_EV_DELAY={ev_delay:g} ms is outside the conservative bench range.")

    ev_pos = [optional_float(params.get(name)) for name in ("EKF2_EV_POS_X", "EKF2_EV_POS_Y", "EKF2_EV_POS_Z")]
    if any(value is None for value in ev_pos):
        add_issue(issues, "warning", "One or more EKF2_EV_POS_X/Y/Z camera-to-body offsets are missing.")
    elif not extrinsics_measured and all(abs(float(value or 0.0)) < 1e-6 for value in ev_pos):
        add_issue(issues, "warning", "EKF2_EV_POS_X/Y/Z are all zero; confirm this is measured camera-to-body geometry, not an untouched default.")

    status = "failed" if any(issue["severity"] == "error" for issue in issues) else "passed"
    if status == "passed" and any(issue["severity"] == "warning" for issue in issues):
        status = "degraded"

    return {
        "status": status,
        "parameters": {
            "EKF2_EV_CTRL": ev_ctrl,
            "EKF2_EV_CTRL_bits": enabled_bits,
            "EKF2_HGT_REF": hgt_ref,
            "EKF2_GPS_CTRL": gps_ctrl,
            "EKF2_EV_NOISE_MD": noise_mode,
            "EKF2_EV_DELAY": ev_delay,
            "EKF2_EV_POS_XYZ": ev_pos,
        },
        "config": {
            "gnss_denied": gnss_denied,
            "vision_height_valid": vision_height_valid,
            "vision_velocity_valid": vision_velocity_valid,
            "vision_yaw_valid": vision_yaw_valid,
            "extrinsics_measured": extrinsics_measured,
        },
        "issues": issues,
        "references": dict(PX4_PARAM_REFERENCES),
    }


def evaluate_px4_param_file(path: str | Path, **kwargs: Any) -> dict[str, Any]:
    params = load_px4_params(path)
    report = check_px4_external_vision_params(params, **kwargs)
    report["param_file"] = str(Path(path).expanduser())
    report["param_count"] = len(params)
    return report


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


def bit_is_set(value: int, bit: int) -> bool:
    return bool(int(value) & (1 << int(bit)))


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
    print(f"PX4 external-vision parameter check: {report.get('param_file')}")
    print(f"Status: {report['status']}")
    params = report["parameters"]
    print(f"EKF2_EV_CTRL: {params['EKF2_EV_CTRL']} bits={params['EKF2_EV_CTRL_bits']}")
    print(f"EKF2_HGT_REF: {params['EKF2_HGT_REF']}")
    print(f"EKF2_GPS_CTRL: {params['EKF2_GPS_CTRL']}")
    for issue in report["issues"]:
        print(f"[{issue['severity'].upper()}] {issue['message']}")


def main() -> None:
    args = parse_args()
    report = evaluate_px4_param_file(
        args.params,
        gnss_denied=args.gnss_denied,
        vision_height_valid=args.vision_height_valid,
        vision_velocity_valid=args.vision_velocity_valid,
        vision_yaw_valid=args.vision_yaw_valid,
        extrinsics_measured=args.extrinsics_measured,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
