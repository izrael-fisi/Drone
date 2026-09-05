"""Offline IMU-frame trajectory metrics; ground truth never enters navigation."""

from __future__ import annotations

import argparse
import copy
import csv
import importlib.metadata
import json
from pathlib import Path

import numpy as np

from .openvins_replay import REVISION, digest


def pose_matrix(value):
    pose = np.asarray(value, dtype=float)
    if (pose.shape != (4, 4) or not np.isfinite(pose).all()
            or not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-8)
            or not np.allclose(pose[:3, :3].T @ pose[:3, :3], np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(pose[:3, :3]), 1., atol=1e-6)):
        raise ValueError("Pose must be a finite proper SE3 transform")
    return pose


def ground_truth_csv(path):
    """TUM VI calibrated export: native ns, IMU position, Hamilton wxyz."""
    from scipy.spatial.transform import Rotation

    times, poses = [], []
    with Path(path).open() as stream:
        for row in csv.reader(line for line in stream if line.strip() and not line.startswith("#")):
            if len(row) != 8:
                raise ValueError("Expected eight-column calibrated IMU ground-truth CSV")
            ns = int(row[0])
            values = np.array([float(x) for x in row[1:]])
            if (ns <= 0 or (times and ns <= times[-1]) or not np.isfinite(values).all()
                    or not np.isclose(np.linalg.norm(values[3:]), 1., atol=1e-3)):
                raise ValueError("Invalid ground-truth timestamp, value or quaternion")
            pose = np.eye(4)
            pose[:3, 3] = values[:3]
            pose[:3, :3] = Rotation.from_quat(values[[4, 5, 6, 3]]).as_matrix()
            times.append(ns)
            poses.append(pose)
    if len(times) < 3:
        raise ValueError("Insufficient ground truth")
    return np.array(times, dtype=np.int64), np.array(poses)


def associate(times, gt_times, gt_poses, max_gap_ns):
    from scipy.spatial.transform import Rotation, Slerp

    indices, reference = [], []
    for index, ns in enumerate(times):
        right = int(np.searchsorted(gt_times, ns))
        if right < len(gt_times) and gt_times[right] == ns:
            pose = gt_poses[right]
        elif right == 0 or right == len(gt_times) or gt_times[right] - gt_times[right-1] > max_gap_ns:
            continue
        else:
            a, b = gt_poses[right-1:right+1]
            fraction = float(ns-gt_times[right-1]) / float(gt_times[right]-gt_times[right-1])
            pose = np.eye(4)
            pose[:3, 3] = (1-fraction)*a[:3, 3] + fraction*b[:3, 3]
            pose[:3, :3] = Slerp([0., 1.], Rotation.from_matrix([a[:3, :3], b[:3, :3]]))([fraction]).as_matrix()[0]
        indices.append(index)
        reference.append(pose)
    return np.array(indices, dtype=int), reference


def trajectory_metrics(reference, estimated, seconds):
    from evo.core import filters, metrics, trajectory, units
    from evo.core.geometry import GeometryException

    ref = trajectory.PoseTrajectory3D(poses_se3=reference, timestamps=seconds)
    est = trajectory.PoseTrajectory3D(poses_se3=estimated, timestamps=seconds)

    def errors(candidate):
        result = {}
        for key, relation in (("position_m", metrics.PoseRelation.translation_part),
                              ("orientation_deg", metrics.PoseRelation.rotation_angle_deg)):
            metric = metrics.APE(relation)
            metric.process_data((ref, candidate))
            result[key] = metric.get_all_statistics()
        return result

    initial = copy.deepcopy(est)
    initial.transform(reference[0] @ np.linalg.inv(estimated[0]))
    result = {"initial_pose_alignment": errors(initial), "scale_correction": False}
    fitted = copy.deepcopy(est)
    try:
        r, t, scale = fitted.align(ref, correct_scale=False)
        result["full_trajectory_se3_alignment"] = errors(fitted)
        result["alignment"] = {"rotation": r.tolist(), "translation_m": t.tolist(), "scale": float(scale)}
    except GeometryException as exc:
        result["full_trajectory_se3_alignment"] = None
        result["alignment_error"] = str(exc)
    # Distance-based relative error does not need gauge or scale alignment.
    rpe = metrics.RPE(metrics.PoseRelation.translation_part, delta=1., delta_unit=units.Unit.meters,
                      all_pairs=True, pairs_from_reference=True, rel_delta_tol=.1)
    boundaries = [0, *(np.flatnonzero(np.diff(seconds) > .25)+1).tolist(), len(seconds)]
    relative_errors = []
    for start, end in zip(boundaries, boundaries[1:]):
        if end-start < 2:
            continue
        try:
            rpe.process_data((trajectory.PosePath3D(poses_se3=reference[start:end]),
                              trajectory.PosePath3D(poses_se3=estimated[start:end])))
            relative_errors.append(rpe.error.copy())
        except (metrics.MetricsException, filters.FilterException):
            continue
    result["rpe_max_associated_gap_s"] = .25
    if relative_errors:
        rpe.error = np.concatenate(relative_errors)
        result["rpe_reference_1m_translation_m"] = {**rpe.get_all_statistics(), "pairs": len(rpe.error)}
    else:
        result["rpe_reference_1m_translation_m"] = None
        result["rpe_error"] = "No one-metre pairs inside continuous associated segments"
    result["associated_reference_distance_m"] = float(ref.path_length)
    result["associated_estimated_distance_m"] = float(est.path_length)
    return result


def evaluate(replay: Path, ground_truth: Path, max_gap_ns=20_000_000):
    if not 0 < max_gap_ns <= 100_000_000:
        raise ValueError("Ground-truth interpolation bound must be at most 100 ms")
    report = json.loads((replay / "report.json").read_text())
    states_path = replay / "local_states.jsonl"
    if (report.get("source_revision") != REVISION or report.get("position_authority") is not False
            or report.get("states_sha256") != digest(states_path)):
        raise ValueError("Replay provenance or state checksum mismatch")
    all_times, times, poses = [], [], []
    with states_path.open() as stream:
        for line in stream:
            row = json.loads(line)
            ns = row["timestamp_ns"]
            if (type(ns) is not int or ns <= 0 or (all_times and ns <= all_times[-1])
                    or row.get("source_revision") != REVISION or row.get("position_authority") is not False
                    or row.get("navigation_mode") != "NO_GLOBAL_FIX"
                    or row.get("px4_external_vision") != "disabled"):
                raise ValueError("Invalid or non-shadow replay record")
            all_times.append(ns)
            if row["local_state"] == "LOCAL_ESTIMATE":
                times.append(ns)
                poses.append(pose_matrix(row["local_T_imu"]))
            elif row["local_state"] != "INITIALIZING_OR_NO_UPDATE":
                raise ValueError("Unknown local state")
    gt_times, gt_poses = ground_truth_csv(ground_truth)
    indices, reference = associate(times, gt_times, gt_poses, max_gap_ns)
    result = {"schema_version": "vision_nav_vio_evaluation_v1", "scope": "local_imu_odometry_not_global_uav_accuracy",
              "position_authority": False, "px4_external_vision": "disabled",
              "replay_complete": report.get("complete") is True,
              "ground_truth_used_by_frontend": False, "ground_truth_frame": "calibrated_imu",
              "timestamp_policy": "native_ns_no_offset_fit_linear_position_slerp_orientation",
              "max_ground_truth_interpolation_gap_ns": max_gap_ns,
              "camera_frames": len(all_times), "local_estimate_frames": len(times),
              "associated_frames": len(indices), "unassociated_local_frames": len(times)-len(indices),
              "local_update_fraction": len(times)/len(all_times) if all_times else 0.,
              "first_local_update_s": (times[0]-all_times[0])/1e9 if times else None,
              "max_local_update_gap_s": float(np.max(np.diff(times))/1e9) if len(times)>1 else None,
              "recorded_camera_duration_s": (all_times[-1]-all_times[0])/1e9 if all_times else 0.,
              "replay_report_sha256": digest(replay / "report.json"), "states_sha256": digest(states_path),
              "ground_truth_sha256": digest(ground_truth), "evaluator_sha256": digest(__file__),
              "evo_version": importlib.metadata.version("evo"), "metrics": None}
    if len(indices) >= 3:
        selected_times = np.asarray(times, dtype=np.int64)[indices]
        result["max_associated_pose_gap_s"] = float(np.max(np.diff(selected_times))/1e9)
        result["metrics"] = trajectory_metrics(reference, [poses[i] for i in indices],
                                                 (selected_times-selected_times[0])/1e9)
    else:
        result["evaluation_error"] = "Fewer than three associated local estimates"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", required=True, type=Path)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = evaluate(args.replay, args.ground_truth)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["metrics"] is not None and result["replay_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
