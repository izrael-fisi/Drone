"""Group redundant metric tile poses without fusing correlated evidence."""

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class CandidateGroupingPolicy:
    max_position_separation_m: float = 1.0
    max_rotation_separation_deg: float = 3.0
    min_support_overlap_fraction: float = .05

    def validate(self):
        values = asdict(self)
        if any(type(v) not in (int, float) or not np.isfinite(v) or v <= 0 for v in values.values()):
            raise ValueError("Candidate grouping thresholds must be finite and positive")
        if self.max_rotation_separation_deg > 180 or self.min_support_overlap_fraction > 1:
            raise ValueError("Invalid rotation or overlap threshold")


def landmark_bounds(points: Any):
    """Conservative ENU XY support box, not a claimed orthophoto footprint."""
    if points is None:
        return None
    points = np.asarray(points, dtype=float)
    if points.ndim == 3 and points.shape[0] == 1:
        points = points[0]
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 6 or not np.isfinite(points).all():
        return None
    low, high = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
    return [*low.tolist(), *high.tolist()] if np.all(high > low) else None


def _metric_pose(item):
    geometry = item["geometry"]
    if "camera_position_enu_m" not in geometry:
        return None
    p = np.asarray(geometry["camera_position_enu_m"], dtype=float)
    r = np.asarray(geometry.get("camera_rotation_enu_to_optical"), dtype=float)
    if (p.shape != (3,) or r.shape != (3, 3) or not np.isfinite(p).all() or not np.isfinite(r).all()
            or not np.allclose(r.T @ r, np.eye(3), atol=1e-5) or not np.isclose(np.linalg.det(r), 1, atol=1e-5)):
        raise ValueError("Accepted metric camera candidate has invalid pose")
    return p, r


def _bounds(value):
    if value is None:
        return None
    bounds = np.asarray(value, dtype=float)
    if bounds.shape != (4,) or not np.isfinite(bounds).all() or np.any(bounds[2:] <= bounds[:2]):
        raise ValueError("Invalid candidate landmark support bounds")
    return bounds


def select_candidate_groups(attempts: list[dict], *, complete: bool, policy=CandidateGroupingPolicy()):
    """Input must belong to one frame and one validated map/model/ENU release.

    Fixed thresholds are diagnostic association bounds, not calibrated pose
    confidence. No scores, inlier counts or covariances are summed across tiles.
    """
    policy.validate()
    ids = [a["tile_id"] for a in attempts]
    if len(ids) > 24 or any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Candidate selection requires at most 24 distinct tile IDs")
    accepted = sorted((a for a in attempts if a.get("geometry", {}).get("status") == "accepted"), key=lambda a: a["tile_id"])
    poses, boxes, errors = {}, {}, []
    for a in accepted:
        try:
            poses[a["tile_id"]] = _metric_pose(a)
            boxes[a["tile_id"]] = _bounds(a.get("landmark_support_bounds_enu_m"))
        except (TypeError, ValueError) as exc:
            errors.append({"tile_id": a["tile_id"], "reason": str(exc)})
    adjacency = {a["tile_id"]: set() for a in accepted}
    pairs = []
    for i, a in enumerate(accepted):
        for b in accepted[i+1:]:
            aid, bid = a["tile_id"], b["tile_id"]
            pa, pb, ba, bb = poses.get(aid), poses.get(bid), boxes.get(aid), boxes.get(bid)
            pair = {"tile_ids": [aid, bid], "consistent": False, "reason": "metric_pose_and_support_required"}
            if pa is not None and pb is not None and ba is not None and bb is not None:
                distance = float(np.linalg.norm(pa[0]-pb[0]))
                angle = float(np.degrees(np.arccos(np.clip((np.trace(pa[1] @ pb[1].T)-1)/2, -1., 1.))))
                intersection = float(np.prod(np.maximum(0., np.minimum(ba[2:], bb[2:])-np.maximum(ba[:2], bb[:2]))))
                overlap = intersection / min(float(np.prod(ba[2:]-ba[:2])), float(np.prod(bb[2:]-bb[:2])))
                consistent = (distance <= policy.max_position_separation_m and angle <= policy.max_rotation_separation_deg
                              and overlap >= policy.min_support_overlap_fraction)
                pair.update(position_separation_m=distance, rotation_separation_deg=angle,
                            support_bbox_overlap_fraction=overlap, consistent=consistent,
                            reason="consistent_metric_overlap" if consistent else "different_pose_or_disjoint_support")
                if consistent:
                    adjacency[aid].add(bid)
                    adjacency[bid].add(aid)
            pairs.append(pair)
    groups, remaining, nonclique = [], set(adjacency), False
    while remaining:
        frontier, component = [min(remaining)], set()
        while frontier:
            node = frontier.pop()
            if node not in component:
                component.add(node)
                frontier.extend(adjacency[node]-component)
        remaining -= component
        # A-B and B-C agreement cannot hide A-C disagreement by chaining.
        clique = all(component-{node} <= adjacency[node] for node in component)
        nonclique |= not clique
        groups.extend([sorted(component)] if clique else [[node] for node in sorted(component)])
    by_id = {a["tile_id"]: a for a in accepted}
    summaries = []
    for group in groups:
        # Choose one original observation deterministically. Never average poses.
        representative = min(group, key=lambda key: (-by_id[key]["geometry"].get("inlier_count", 0),
                                  by_id[key]["geometry"].get("reprojection_rmse_px", float("inf")), key))
        summaries.append({"tile_ids": group, "representative_tile_id": representative,
                          "pose_fused": False, "confidence": None, "pose_covariance": None})
    if not complete or errors:
        reason = "candidate_verification_incomplete"
    elif len(groups) > 1:
        reason = "ambiguous_multiple_geometric_candidates"
    elif not groups:
        reason = "no_geometric_candidate"
    else:
        reason = "unique_geometry_within_retrieved_set"
    unique = reason == "unique_geometry_within_retrieved_set"
    return {"schema_version": "vision_nav_candidate_groups_v1",
            "algorithm": "pairwise_clique_or_withhold",
            "correlation_policy": "select_one_original_observation_no_evidence_sum",
            "status": "unique" if unique else "withheld", "reason": reason,
            "accepted_tile_ids": [a["tile_id"] for a in accepted], "groups": summaries,
            "selected_tile_id": summaries[0]["representative_tile_id"] if unique else None,
            "pairwise_evidence": pairs, "nonclique_overlap": nonclique, "validation_errors": errors,
            "complete": complete and not errors, "policy": asdict(policy),
            "policy_calibrated": False, "confidence": None, "position_authority": False,
            "covariance": None, "px4_external_vision": "disabled"}
