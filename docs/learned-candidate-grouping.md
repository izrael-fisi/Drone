# Learned Candidate Grouping

Status: September 5, 2026. Diagnostic grouping is now shared by the release-bound
Pi shadow probe and the recorded-camera learned replay. No PX4 publication or
operator UI changes are included.

## Problem And Scope

Overlapping tiles can independently produce nearly identical PnP camera poses.
Treating every tile ID as a different location makes a correct seam match appear
ambiguous. Conversely, summing their scores or treating their correlated map
features as independent measurements fabricates confidence.

`neural/candidate_groups.py` groups redundant **metric camera pose candidates**
for one frame and one already validated map/model/ENU release. It is not a
motion estimator, calibrated posterior, map-release compatibility validator or
full temporal multi-hypothesis bank.

## Association Rules

The current diagnostic policy requires all of:

- Accepted PnP geometry with finite camera position and a proper ENU-to-optical
  rotation matrix for both candidates.
- Position separation at most 1 metre and full rotation separation at most
  3 degrees, not merely similar heading.
- Overlap of their ENU XY landmark-support bounding boxes: intersection area
  divided by the smaller box area must be at least 0.05. This is a conservative
  support-box check, not proof that complete image footprints or inliers overlap.

These thresholds are recorded with `policy_calibrated: false`. They are research
association bounds, not validated localization errors or vehicle safety limits.
Homography-only candidates cannot be merged because they do not establish metric
camera poses. Missing support leaves separate candidates rather than guessing
tile adjacency from names. Invalid metric poses/bounds withhold selection.

Every pair within a group must agree. If A agrees with B and B with C but A
disagrees with C, the connected set is kept as separate candidates and marked
`nonclique_overlap`. This prevents chained merging across different locations.
Selection is independent of retrieval iteration order.

One original observation represents a group: most inliers, then lowest
reprojection RMSE, then tile ID as deterministic tie-break. Poses are not
averaged, inlier counts/scores are not summed, and confidence/covariance remain
unset. Every original tile attempt and every pairwise decision remain in the
trace. This does not claim the chosen observation is statistically optimal.

## Integration

- `shadow_inference.py` attaches landmark-support bounds to each PnP attempt
  and uses the shared selector. Multiple consistent overlapping tiles may be
  one candidate group; truly different groups still withhold selection.
- `replay_local_geometry.py` uses the same selector and records its result in
  `local_verification.candidate_selection`. Existing outer reason strings remain
  compatible with prior replay readers.
- An incomplete or budget-truncated search cannot select a unique group even
  if the candidates checked so far agree. Duplicate tile IDs remain deduplicated
  before expensive matching and are rejected at the selector contract.
- Both paths now use `resized_camera_matrix` with OpenCV pixel-center offsets.
  The shadow probe previously scaled focal length/principal point without the
  half-pixel offset; it now matches the recorded-camera path.

`status: unique` means one diagnostic group **within the checked retrieval set**.
It is not a proof of uniqueness over the whole mission area, integrity, temporal
map lock, or position authority. Shadow publication gates remain unchanged.

The existing temporal shadow gate still tracks the representative tile ID;
switching representatives can reset its consecutive count. Cross-frame group
identity, motion-conditioned hypothesis survival, learned no-match likelihood,
qualified pose covariance and correlated VIO/map fusion remain separate work.

## Verification

Tests cover consistent overlap, different positions/orientations, disjoint
support, chain merges, permutation invariance, incomplete searches, planar-only
matches, invalid poses/bounds/policies and duplicate IDs. Integration fixtures
run actual OpenCV PnP from known 3D points through the shadow probe, including
nonuniform image resizing, and check that both tile attempts survive grouping.
Their correspondences are synthetic; they do not measure learned UAV accuracy.

```bash
python -m pytest -q tests/test_candidate_groups.py tests/test_candidate_verification.py
```

The broader learned suite includes real pinned checkpoint/ONNX checks when
`VISION_NAV_LOCAL_REFERENCE_CONFIG` and `VISION_NAV_MEGALOC_EXPORT` are configured.
Those prove model execution/parity, not that the grouping policy is field-calibrated.
