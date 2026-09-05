# Learned PnP Estimator Binding

This research adapter connects the live learned result format to conditional
PnP uncertainty and the delayed joint-window estimator. It does not turn a
successful neural match into an aircraft position measurement. It is an API
for the owning runtime/replay service. The live CLI can now opt into binding
audits through `--alignment-config`; conditional alignment requires explicit
Python uncertainty providers. The CLI loads them in an
[owned estimator process](research-alignment-process.md), never in the sensor
owner. See [live alignment](live-research-alignment.md).

## Inputs And Ownership

`vision_nav.estimation.learned_pnp_binding.prepare` accepts:

- The complete `vision_nav_live_learned_sensor_session_v1` result row.
- The original immutable `LiveFrameTicket` for its final camera frame.
- The owning inference session's pipeline provenance.
- Explicit model release, sensor clock and uncertainty model IDs.

New live rows contain a canonical `provenance_sha256`. The model child computes
it over its pipeline provenance; the parent's appended `execution` metadata is
not included. The digest binds a row to the configured pipeline, not to a signed
or authenticated remote sender. Only owner-validated process/replay output may
enter this API. Existing rows without the digest are rejected, not upgraded by
guessing their provenance.

The adapter checks native epoch, sequence, exposure timestamp, pixel hash, PNG
hash and temporal anchor identity against the retained ticket. It recomputes
the raw pixel hash and rejects writable pixels. No camera or serial connection
is opened, and no second image capture is requested.

## Candidate Selection

All retrieved candidates must have model and local-verification records in the
same order. The adapter reruns the existing candidate-grouping policy and
requires an unchanged, complete, unique geometric group. It chooses that group's
one original representative, never averages overlapping tiles, sums confidence
or discards competing locations based on a better inlier count.

This is uniqueness within the retrieved set, not proof of global uniqueness or
temporal consistency. The broader hypothesis/integrity gates remain required.
Surface-backed PnP is mandatory here; homography-only evidence is withheld.
Map/calibration/model/image IDs are checked against owner provenance.

Local-verification attempts now retain `matched_query_feature_indices` and
`matched_map_feature_indices`, preserving the exact correspondence ordering
after map-surface filtering. These indices are scoped to that extraction and
tile/model configuration; they are not persistent cross-frame track IDs.

The conditional uncertainty implementation accepts 6..128 paired matches. The
adapter preserves the complete selected attempt and its original inlier indices;
it never silently truncates, refits or relabels an oversized fit. The learned
verifier now implements an explicit [bounded PnP policy](bounded-learned-pnp.md):
first require full-set consensus, then perform a new spatially distributed
24-pair maximum fit and recheck its wider support. The adapter verifies the
selection trace and exact final-fit inputs; noise must describe that final fit.

## Explicit Uncertainty

Successful preparation returns `awaiting_joint_noise` and
`request_without_uncertainty`. This is not a usable covariance or fix.
`bind_uncertainty(draft, uncertainty)` requires an exact-attempt-bound noise
object using the existing conditional PnP contract, then runs the real
linearization/stationarity/covariance checks.

No covariance is inferred from neural scores, feature counts or map variance
diagonals. Shared map, pixel and intrinsic correlations must be supplied in the
joint model. Timing and extrinsic uncertainty remain explicitly unmodeled by
this conditional research tool, and its covariance is not field-calibrated.
Only `conditional_pnp_request` exposes a request for `complete_query`; failures
have `request=null` and preserve a reason. The request context carries the exact
learned-row, pipeline and candidate-selection digests.

```python
from vision_nav.estimation.learned_pnp_binding import prepare, bind_uncertainty

draft = prepare(
    learned_row,
    frame_ticket=window["tickets"][-1],
    provenance=inference.provenance,
    model_release_id=alignment_config["model_release_id"],
    clock_id=alignment_config["clock_id"],
    uncertainty_model_id=alignment_config["uncertainty_model_id"],
)
if draft["status"] == "awaiting_joint_noise":
    bound = bind_uncertainty(draft, explicit_attempt_noise)
    if bound["status"] == "conditional_pnp_request":
        request = bound["request"]
        context = request["context"]
        query = coordinator.begin_query(context["timestamp_ns"], context["frame_sha256"])
        if query["status"] == "dispatched":
            coordinator.complete_query(query["ticket"], request)
```

The owner must first feed valid native snapshots, invalidate on sensor/worker
failure and avoid duplicate submissions. A later `refresh_current` requires
another explicit joint noise model spanning all retained VIO states and map
observations. Per-frame conditional covariance does not supply those cross
terms. See [joint alignment](joint-pnp-vio-alignment.md).

## Remaining Work

- Field-derived pixel/map/calibration/timing uncertainty and VIO cross terms.
- Persistent correspondence identity and temporal multi-location rejection.
- Pi qualification of the isolated estimator deadlines and workload.
- Pi workload/retention qualification and real X500 geographic holdouts.

Every output remains `NO_GLOBAL_FIX`, `position_authority=false`,
`covariance_calibrated=false` and PX4 disabled. No map, model or vehicle
configuration is promoted by creating a binding.

## Verification

September 5, 2026: **170 estimator/binding/PX4 tests passed**, including an
end-to-end synthetic learned-format input through conditional PnP and current
joint-window alignment. **63 model-process/live/correspondence tests passed**
with actual ONNX models and native OpenVINS enabled. Three optional direct
PyTorch checkpoint tests were skipped because `VISION_NAV_LOCAL_REFERENCE_CONFIG`
was not supplied; the separate ONNX integration tests did execute. The main
unit runner passed **104 checks**. All commands exited normally.

These checks establish contracts and execution, not field accuracy. In
particular, the bound PnP geometry/noise tests use synthetic observations; the
actual-model/native integration checks verify provenance and correspondence
identity transport, not a qualified learned-to-global aircraft estimate.
