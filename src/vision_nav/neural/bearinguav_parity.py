"""Compare checksum-bound reference reports across isolated environments.

This is inference compatibility evidence for one sample, not training parity,
model accuracy, or permission to publish a navigation estimate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


IDENTITY_FIELDS = (
    "sample_id", "dataset_release_id", "split", "input_kind", "request_sha256",
    "source_revision", "weights_revision", "checkpoint_sha256",
    "configuration_sha256", "adapter_sha256", "preprocessing", "inputs",
    "patch_order", "tensor_shape", "coordinate_frame", "parameter_count",
)
ATOL = 1e-6
RTOL = 1e-5


def compare(reference: dict, candidate: dict) -> dict:
    for report in (reference, candidate):
        if not isinstance(report, dict):
            raise ValueError("Expected reference report objects")
        if (report.get("schema_version") != "vision_nav_bearinguav_reference_v1"
                or report.get("status") != "research_inference_only"
                or report.get("repeat_exact") is not True
                or report.get("position_authority") is not False
                or report.get("navigation_mode") != "NO_GLOBAL_FIX"
                or report.get("px4_external_vision") != "disabled"
                or report.get("field_ready") is not False):
            raise ValueError("Only successful, deterministic, shadow reference reports can be compared")
        for key in ("metric_pose", "covariance", "heading_confidence"):
            if key not in report or report[key] is not None:
                raise ValueError("Reference evidence must not claim a qualified pose")
        if not isinstance(report.get("environment"), dict):
            raise ValueError("Missing environment provenance")
        for key in ("torch", "torchvision", "numpy", "opencv", "device", "threads", "python"):
            if key not in report["environment"] or report["environment"][key] in (None, ""):
                raise ValueError(f"Missing environment field: {key}")
        for key in IDENTITY_FIELDS:
            if key not in report or report[key] in (None, "", [], {}):
                raise ValueError(f"Missing comparison identity: {key}")
    mismatches = [key for key in IDENTITY_FIELDS if reference[key] != candidate[key]]
    if mismatches:
        raise ValueError(f"Not the same model/input experiment: {', '.join(mismatches)}")

    heads = {}
    for key in ("relative_position_xy", "direction_xy"):
        vectors = [report.get(key) for report in (reference, candidate)]
        if any(not isinstance(v, list) or len(v) != 2
               or any(type(x) not in (float, int) or not math.isfinite(x) for x in v)
               for v in vectors):
            raise ValueError(f"Expected two finite numeric values: {key}")
        delta = [abs(b - a) for a, b in zip(*vectors)]
        limits = [ATOL + RTOL * abs(a) for a in vectors[0]]
        heads[key] = {"reference": vectors[0], "candidate": vectors[1],
                      "absolute_delta": delta, "maximum_absolute_delta": max(delta),
                      "allowed_delta": limits,
                      "passed": all(d <= limit for d, limit in zip(delta, limits))}
    return {
        "schema_version": "vision_nav_bearinguav_environment_parity_v1",
        "status": "passed" if all(head["passed"] for head in heads.values()) else "failed",
        "scope": "single_sample_inference_compatibility_only",
        "absolute_tolerance": ATOL, "relative_tolerance": RTOL,
        "identity": {key: reference[key] for key in IDENTITY_FIELDS},
        "reference_environment": reference["environment"],
        "candidate_environment": candidate["environment"], "heads": heads,
        "position_authority": False, "px4_external_vision": "disabled",
        "field_ready": False,
        "limitations": ["Not training or optimizer equivalence",
                        "Not a held-out accuracy benchmark",
                        "Does not qualify untested devices or inference providers",
                        "Does not audit dependency security or model/data licenses"],
    }


def read_report(path: Path) -> tuple[dict, str]:
    with path.open("rb") as stream:
        payload = stream.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("Reference report exceeds 1 MiB")
    return json.loads(payload), hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; comparison evidence is immutable")
    try:
        reference, reference_hash = read_report(args.reference)
        candidate, candidate_hash = read_report(args.candidate)
        result = compare(reference, candidate)
        result["reference_report_sha256"] = reference_hash
        result["candidate_report_sha256"] = candidate_hash
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "scope": result["scope"]}))
    if result["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
