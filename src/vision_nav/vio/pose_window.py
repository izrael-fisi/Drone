"""Correlated OpenVINS pose-window audit; never independent process factors."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

from vision_nav.estimation.pose_smoother import timestamp, transform
from .openvins_replay import REVISION, digest, load_anchor_schedule

SCHEMA = "vision_nav_openvins_pose_window_v1"
ANCHOR_SCHEMA = "vision_nav_openvins_pose_window_v2"


def _window_times(window):
    times = window["pose_timestamps_ns"]
    schema = window.get("schema_version")
    bound = 36 if schema == ANCHOR_SCHEMA else 32
    if (not isinstance(times, list) or not 1 <= len(times) <= bound
            or any(type(t) is not int or not 0 < t < 2**63 for t in times)
            or any(b <= a for a, b in zip(times, times[1:]))):
        raise ValueError("Invalid clone timestamps or window size")
    if schema == ANCHOR_SCHEMA:
        capacity = window.get("passive_anchor_capacity")
        normal, anchors = window.get("normal_clone_timestamps_ns"), window.get("passive_anchor_timestamps_ns")
        if (type(capacity) is not int or not 1 <= capacity <= 4
                or window.get("retention_policy") != "native_passive_stochastic_clones_explicit_release"
                or not isinstance(normal, list) or not 1 <= len(normal) <= 32
                or not isinstance(anchors, list) or len(anchors) > capacity):
            raise ValueError("Invalid passive anchor metadata")
        for sequence in (normal, anchors):
            if (any(type(t) is not int or t not in times for t in sequence)
                    or any(b <= a for a, b in zip(sequence, sequence[1:]))):
                raise ValueError("Invalid passive anchor membership")
        if sorted(set(normal) | set(anchors)) != times or normal[-1] != times[-1]:
            raise ValueError("Passive anchor union differs from exported pose order")
    elif schema != SCHEMA:
        raise ValueError("Unknown pose window schema")
    return times


def clone_retention_metadata(record, anchor_timestamp_ns):
    """Inspect clone membership only, not covariance validity or fusion eligibility.

    This small diagnostic can run under the worker's snapshot lock. A retained
    timestamp still requires JointPoseWindow validation and measurement gates.
    """
    anchor = timestamp(anchor_timestamp_ns)
    result = {"schema_version": "vision_nav_clone_retention_v1", "anchor_timestamp_ns": anchor,
        "status": "unavailable", "reason": "no_native_record", "anchor_listed": None,
        "posterior_timestamp_ns": None, "oldest_clone_timestamp_ns": None,
        "newest_clone_timestamp_ns": None, "clone_count": None, "clone_span_ms": None,
        "posterior_minus_anchor_ms": None, "covariance_validation": "not_performed",
        "fusion_eligible": False, "position_authority": False, "px4_external_vision": "disabled"}
    if record is None:
        return result
    try:
        if (record.get("schema_version") != "vision_nav_openvins_local_v1"
                or record.get("source_revision") != REVISION
                or record.get("navigation_mode") != "NO_GLOBAL_FIX"
                or record.get("position_authority") is not False
                or record.get("px4_external_vision") != "disabled"):
            raise ValueError("incompatible_native_record")
        posterior = timestamp(record["timestamp_ns"])
        result.update(posterior_timestamp_ns=posterior, posterior_minus_anchor_ms=(posterior-anchor)/1e6)
        if record.get("local_state") != "LOCAL_ESTIMATE":
            result.update(reason="no_local_estimate")
            return result
        window = record.get("pose_window")
        if (not isinstance(window, dict) or window.get("schema_version") not in {SCHEMA, ANCHOR_SCHEMA}
                or window.get("posterior_timestamp_ns") != posterior
                or window.get("position_authority") is not False
                or window.get("map_updates_fused") is not False
                or window.get("contains_cross_covariance") is not True
                or window.get("independent_across_snapshots") is not False):
            raise ValueError("incompatible_clone_metadata")
        times = _window_times(window)
        if times[-1] > posterior:
            raise ValueError("invalid_clone_timestamps")
        listed = anchor in times
        state = ("retained" if listed else "not_yet_processed" if anchor > posterior else
                 "before_retained_window" if anchor < times[0] else "processed_without_clone")
        result.update(status=state, reason=None, anchor_listed=listed, clone_count=len(times),
            oldest_clone_timestamp_ns=times[0], newest_clone_timestamp_ns=times[-1],
            clone_span_ms=(times[-1]-times[0])/1e6)
        if window["schema_version"] == ANCHOR_SCHEMA:
            result["anchor_retention_kind"] = ("passive_stochastic_clone" if anchor in window["passive_anchor_timestamps_ns"]
                                               else "normal_clone" if listed else "not_retained")
    except (AttributeError, KeyError, TypeError, ValueError):
        result.update(status="unavailable", reason="invalid_native_retention_metadata")
    return result


def joint_covariance(value, size):
    cov = np.asarray(value, dtype=float)
    if (cov.shape != (size, size) or not np.isfinite(cov).all()
            or not np.allclose(cov, cov.T, rtol=1e-8, atol=1e-12)):
        raise ValueError("Expected finite symmetric joint covariance of declared size")
    cov = (cov+cov.T)/2
    if np.linalg.eigvalsh(cov).min() < -1e-10:
        raise ValueError("Joint covariance is not positive semidefinite")
    # Round-off symmetry is restored; negative eigenvalues are never clipped.
    return cov.copy()


@dataclass(frozen=True)
class JointPoseWindow:
    posterior_ns: int
    timestamps_ns: tuple[int, ...]
    local_T_imu: tuple[np.ndarray, ...]
    covariance_right_local: np.ndarray

    @classmethod
    def from_record(cls, record):
        if (record.get("schema_version") != "vision_nav_openvins_local_v1"
                or record.get("source_revision") != REVISION
                or record.get("navigation_mode") != "NO_GLOBAL_FIX"
                or record.get("position_authority") is not False
                or record.get("px4_external_vision") != "disabled"
                or record.get("local_state") != "LOCAL_ESTIMATE"):
            raise ValueError("Expected pinned shadow-only OpenVINS local estimate")
        window = record.get("pose_window")
        if (not isinstance(window, dict) or window.get("schema_version") not in {SCHEMA, ANCHOR_SCHEMA}
                or window.get("covariance_convention") != "per_pose_theta_imu_delta_p_local"
                or window.get("linearization") != "current_pose_means_with_native_fej_filter_covariance"
                or window.get("contains_cross_covariance") is not True
                or window.get("independent_across_snapshots") is not False
                or window.get("map_updates_fused") is not False
                or window.get("covariance_calibrated") is not False
                or window.get("position_authority") is not False):
            raise ValueError("Missing or incompatible correlated pose window")
        posterior = timestamp(window["posterior_timestamp_ns"])
        if posterior != timestamp(record["timestamp_ns"]):
            raise ValueError("Pose window posterior does not match record time")
        times = tuple(_window_times(window))
        if times[-1] > posterior:
            raise ValueError("Invalid clone timestamps or window size")
        poses = tuple(transform(p) for p in window["local_T_imu"])
        if len(poses) != len(times):
            raise ValueError("Clone pose/timestamp count mismatch")
        cov = joint_covariance(window["joint_covariance_native"], 6*len(times))
        jacobian = np.eye(len(cov))
        # Native R_I_L uses a negative left rotation error. Transposing yields
        # positive right error for T_L_I; additive local position rotates to I.
        for i, pose in enumerate(poses):
            jacobian[6*i+3:6*i+6, 6*i+3:6*i+6] = pose[:3, :3].T
            pose.setflags(write=False)
        converted = joint_covariance(jacobian @ cov @ jacobian.T, len(cov))
        converted.setflags(write=False)
        return cls(posterior, times, poses, converted)

    def relative_diagnostic(self, first_ns, second_ns):
        """Within-snapshot relative uncertainty, including both cross blocks."""
        import gtsam

        first_ns, second_ns = timestamp(first_ns), timestamp(second_ns)
        if second_ns <= first_ns:
            raise ValueError("Relative diagnostic requires increasing distinct clone times")
        try:
            i, j = self.timestamps_ns.index(first_ns), self.timestamps_ns.index(second_ns)
        except ValueError as exc:
            raise ValueError("Requested clone time is absent; no interpolation allowed") from exc
        h1, h2 = np.empty((6, 6), order="F"), np.empty((6, 6), order="F")
        relative = gtsam.Pose3(self.local_T_imu[i]).between(gtsam.Pose3(self.local_T_imu[j]), h1, h2)
        jacobian = np.zeros((6, len(self.covariance_right_local)))
        jacobian[:, 6*i:6*i+6], jacobian[:, 6*j:6*j+6] = h1, h2
        cov = joint_covariance(jacobian @ self.covariance_right_local @ jacobian.T, 6)
        return {"first_timestamp_ns": first_ns, "second_timestamp_ns": second_ns,
                "posterior_timestamp_ns": self.posterior_ns, "imu_first_T_imu_second": relative.matrix().tolist(),
                "covariance_right_local": cov.tolist(), "tangent_order": "rotation_radians_translation_metres",
                "uncertainty_kind": "correlated_posterior_diagnostic_not_process_noise",
                "independent_factor": False, "covariance_calibrated": False, "position_authority": False}


def audit(replay: Path):
    path = replay / "local_states.jsonl"
    report = json.loads((replay / "report.json").read_text())
    if (report.get("source_revision") != REVISION or report.get("pose_window_requested") is not True
            or report.get("position_authority") is not False or report.get("states_sha256") != digest(path)):
        raise ValueError("Pose-window replay provenance/checksum mismatch")
    anchor_schedule = None
    control_audit = None
    if report.get("anchor_control") is not None:
        from .anchor_control import audit_control_stream
        control = report["anchor_control"]
        if (report.get("passive_anchors") is not None
                or control.get("stream_sha256") != digest(replay/"owned_stream.jsonl")
                or control.get("acknowledgements_sha256") != digest(replay/"anchor_acknowledgements.jsonl")):
            raise ValueError("Anchor control provenance/checksum mismatch")
        control_audit = audit_control_stream(replay/"owned_stream.jsonl", replay/"anchor_acknowledgements.jsonl", path,
            worker_run_id=control["worker_run_id"], capacity=control["capacity"])
    if report.get("passive_anchors") is not None:
        anchor_schedule, _ = load_anchor_schedule(replay / "anchor_schedule.json")
        if (digest(replay / "anchor_schedule.json") != report["passive_anchors"].get("schedule_sha256")
                or anchor_schedule["capacity"] != report["passive_anchors"].get("capacity")):
            raise ValueError("Passive anchor schedule provenance/checksum mismatch")
    action_index, active_anchors = 0, set()
    count, unavailable, missing_local, last_ns = 0, 0, 0, 0
    max_clones, max_span, max_cross = 0, 0., 0.
    min_eigenvalue, max_relative_trace, windows_without_cross = float("inf"), 0., 0
    with path.open() as stream:
        while line := stream.readline(4*1024*1024+1):
            if len(line) > 4*1024*1024:
                raise ValueError("Oversized joint pose evidence row")
            record = json.loads(line)
            ns = timestamp(record["timestamp_ns"])
            if ns <= last_ns:
                raise ValueError("Nonmonotonic replay records")
            last_ns = ns
            if (record.get("schema_version") != "vision_nav_openvins_local_v1"
                    or record.get("position_authority") is not False or record.get("navigation_mode") != "NO_GLOBAL_FIX"
                    or record.get("px4_external_vision") != "disabled" or record.get("source_revision") != REVISION
                    or record.get("local_state") not in {"LOCAL_ESTIMATE", "INITIALIZING_OR_NO_UPDATE"}):
                raise ValueError("Invalid shadow replay record")
            if anchor_schedule is not None:
                expected = []
                actions = anchor_schedule["actions"]
                while action_index < len(actions) and actions[action_index]["at_timestamp_ns"] <= ns:
                    operation = actions[action_index]
                    if operation["at_timestamp_ns"] != ns:
                        raise ValueError("Scheduled anchor camera record missing")
                    expected.append(operation)
                    anchor = operation["anchor_timestamp_ns"]
                    if operation["operation"] == "retain":
                        active_anchors.add(anchor)
                    else:
                        active_anchors.remove(anchor)
                    action_index += 1
                if record.get("passive_anchor_actions") != expected:
                    raise ValueError("Native anchor actions differ from schedule")
                if expected and record["local_state"] != "LOCAL_ESTIMATE":
                    raise ValueError("Anchor action requires an initialized camera update")
            if record.get("pose_window") is None:
                missing_local += int(record["local_state"] == "LOCAL_ESTIMATE")
                unavailable += 1
                continue
            window = JointPoseWindow.from_record(record)
            if anchor_schedule is not None:
                metadata = record["pose_window"]
                if (metadata["schema_version"] != ANCHOR_SCHEMA
                        or metadata["passive_anchor_timestamps_ns"] != sorted(active_anchors)
                        or metadata["passive_anchor_capacity"] != anchor_schedule["capacity"]):
                    raise ValueError("Exported passive anchors differ from applied schedule")
            elif control_audit is None and record["pose_window"]["schema_version"] != SCHEMA:
                raise ValueError("Passive anchor output requires schedule provenance")
            n = len(window.timestamps_ns)
            count += 1
            max_clones = max(max_clones, n)
            max_span = max(max_span, (window.timestamps_ns[-1]-window.timestamps_ns[0])/1e9)
            cov = window.covariance_right_local
            min_eigenvalue = min(min_eigenvalue, float(np.linalg.eigvalsh(cov).min()))
            cross = cov.copy()
            for i in range(n):
                cross[6*i:6*i+6, 6*i:6*i+6] = 0
            norm = float(np.linalg.norm(cross))
            max_cross = max(max_cross, norm)
            if n > 1:
                windows_without_cross += int(norm == 0.)
                relative = window.relative_diagnostic(window.timestamps_ns[0], window.timestamps_ns[-1])
                max_relative_trace = max(max_relative_trace, float(np.trace(relative["covariance_right_local"])))
    if anchor_schedule is not None and report.get("complete") and action_index != len(anchor_schedule["actions"]):
        raise ValueError("Complete replay omitted anchor actions")
    return {"schema_version": "vision_nav_joint_pose_audit_v1", "replay_complete": report.get("complete") is True,
            "passive_anchor_schedule_verified": anchor_schedule is not None,
            "anchor_control_verified": control_audit,
            "passive_anchor_actions_verified": action_index,
            "windows_validated": count, "records_without_window": unavailable,
            "local_estimates_without_window": missing_local, "max_clones": max_clones,
            "max_window_span_s": max_span, "minimum_joint_eigenvalue": min_eigenvalue if count else None,
            "max_cross_block_frobenius_norm": max_cross, "multi_pose_windows_without_cross_covariance": windows_without_cross,
            "max_oldest_to_newest_relative_trace": max_relative_trace if count else None,
            "states_sha256": digest(path), "replay_report_sha256": digest(replay / "report.json"),
            "auditor_sha256": digest(__file__), "position_authority": False, "px4_external_vision": "disabled",
            "covariance_calibrated": False, "independent_process_factor_export": "forbidden",
            "scope": "within_snapshot_correlation_contract_not_joint_map_fusion"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = audit(args.replay)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["replay_complete"] and result["windows_validated"] and not result["local_estimates_without_window"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
