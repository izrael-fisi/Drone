# Integration interface reference

This is the language-neutral contract for integrating the scaffold. Field names match the Python reference in `src/vision_nav/types.py`; if a different language is used, generate an equivalent IDL/schema and preserve the semantics.

## Coordinate and timestamp rules

| Item | Contract |
| --- | --- |
| Map frame | ENU, metres, fixed origin stored in the map release |
| PX4 output frame | Offline bridge record is NED; wire frames require a pinned firmware adapter, not yet implemented |
| Body frame | Publication conversion requires explicit FRD; `body_T_camera` must include it |
| Quaternion | internal `xyzw`, unit norm; bridge rotates the world frame and emits `wxyz`, not just storage reordering |
| Time | monotonically increasing seconds at the runtime boundary; source clock domain and synchronization are logged |
| Covariance | 6×6 pose covariance in the declared frame, finite, positive semi-definite, with documented ordering |
| Image coordinates | pixel `(u,v)`, origin top-left; record image resize/crop/rectification transform |

## `FramePacket`

| Field | Producer responsibility | Validation |
| --- | --- | --- |
| `sequence` | strictly increasing per camera stream | no duplicates/reordering without explicit reset |
| `timestamp_s` | hardware capture time converted to monotonic seconds | positive and newer than last accepted frame |
| `image` | calibrated/rectified image with known colour layout | image dimensions match `CameraModel` |
| `camera` | fx/fy/cx/cy plus width/height | calibration ID exists; values finite and plausible |
| `imu` | qualified, separately synchronized raw inertial data for nominal VIO | optional for camera diagnostics/constrained camera-only experiments, not for metric VIO |

## `VisualOdometry`

This original port is not sufficient for the qualified metric smoother or the
new native VIO adapter. See [metric factor replay](metric-pose-smoother.md) and
[native local-state replay](openvins-research-replay.md) for their explicit clock,
frame, provenance and covariance contracts. Native posterior VIO poses must not
be turned into independent relative factors by simple differencing.

| Field | Meaning |
| --- | --- |
| `relative_pose` | transform from previous accepted local keyframe/body pose to current pose, in metres and internal orientation convention |
| `covariance` | uncertainty of this relative observation—not a global pose covariance |
| `tracked_features` | post-outlier count; used as one quality diagnostic |
| `quality` | calibrated [0,1] health score; never sufficient alone to publish global pose |

## Candidate, match, and geometry messages

The opt-in `vision_nav_openvins_pose_window_v1` research interface preserves
within-snapshot clone correlations. See the [joint VIO contract](vio-joint-pose-contract.md)
for timestamp ownership, tangent conversion and audit. It does not supersede
the prohibition on feeding correlated posterior poses as independent factors.

The opt-in v2 window adds bounded native passive anchors with explicit
normal/passive membership and schedule or owned live-control provenance. It retains the same tangent
and correlation semantics; it does not make posterior differences independent.
See [native passive anchors](native-passive-anchors.md) and
[live frame-ticket controls](live-native-anchors.md). The default live stream
still uses v1. The separate [automatic query-owner option](learned-query-anchors.md)
uses four v2 slots, acquires before inference, and binds completion to the original
coordinator query. Merely enabling the low-level controls does not enable that policy.

The native worker's `vision_nav_native_batch_timings_v1` diagnostic is a bounded
256-sample host-service ring, not a sensor or pose contract. Snapshot copying
occurs outside its acquisition lock; original receipt time precedes adapter
acknowledgement and publication delays. Applied anchor-control changes and
terminal failure invalidate in-progress copies. See
[snapshot handoff](native-vio-stream-worker.md#snapshot-handoff-and-timing).

Native anchor submission distinguishes orderly closed input with
`AnchorInputClosed`, a `RuntimeError` subclass issued before queue mutation.
The query owner withholds unsent retains and requires confirmed epoch exit for
unsent releases of owned anchors. Other failures and receipt timeouts are not
downgraded. See [query lifecycle](learned-query-anchors.md#bounds-and-failures).

`RetrievalCandidate` is a coarse map lookup, not a pose. `CorrespondenceSet` contains paired UAV/tile pixels plus matcher certainty. `GeometricEstimate.valid` requires the geometry adapter's declared model-specific checks.

Use this model-selection policy:

| Available map evidence | Preferred geometry | Preconditions |
| --- | --- | --- |
| 3D ENU landmarks/DEM-backed map points | PnP + robust sampling | calibrated intrinsics; 3D point accuracy/visibility recorded |
| locally planar orthorectified ground and constrained viewing geometry | homography + robust sampling | validated planarity, attitude/altitude envelope and pixel-to-map conversion |
| two overlapping camera views for local motion | essential/relative pose | sufficient parallax; scale remains explicitly unresolved if monocular |

For each result record model type, inlier mask/count/ratio, spatial coverage, residual definition/RMSE, covariance calculation, map version and reason for rejection.

`vision_nav_elevation_source_v2` adds a checksum-bound ground-support mask and
permits DTM only within explicitly supported regions. v1 remains DSM-only.
The [surface-mask contract](ground-surface-masks.md) preserves feature/descriptor
indices through filtering and records excluded/unknown versus invalid-height
counts. No validity mask is inferred from the absence of building annotations.

The [conditional learned-pose uncertainty tool](learned-pose-uncertainty.md)
now propagates an explicitly supplied joint pixel/map/intrinsic error model for
an accepted PnP attempt. It preserves exact inlier identities and produces
research diagnostics only. It does not fill `GeometricEstimate` publication
covariance or infer cross-fix/VIO independence.

The delayed coordinator's `refresh_current` research API can re-solve the
original map evidence against a newer joint VIO snapshot. Success means current
at the final snapshot check only; it is not a `NavEstimate` or flight-authorized
output. It requires a new snapshot-bound joint uncertainty model, keeps original
sensor times and withholds on staleness or retry exhaustion. See
[joint alignment](joint-pnp-vio-alignment.md#refresh-against-the-latest-snapshot).

The correlated solver's `nuisance_elimination` metadata describes elimination
of unused pose residuals after full native/joint-covariance validation; it does
not imply native clone eviction or new independence. `covariance_solver` is
`whitened_jacobian_qr`, replacing an observed numerically inaccurate full-window
covariance path. Original snapshot/fix identities remain unchanged. See
[solver reduction and parity](joint-pnp-vio-alignment.md#smaller-correlated-solve).

For one map fix the default optimized solve now uses
`solution_method=single_fix_closed_form` and
`covariance_solver=analytic_joint_se3_pushforward`; it propagates retained
anchor/current/map correlations without an iterative graph. Multiple fixes and
the full offline reference keep the graph path. This does not make a single
fix a consistency test or change the current-snapshot gate. See
[direct propagation](joint-pnp-vio-alignment.md#direct-single-fix-propagation).

The [learned PnP binding](learned-pnp-estimator-binding.md) connects owner-bound
live rows and immutable native frame tickets to that conditional research input.
It rechecks the full candidate set, keeps one original metric observation and
requires explicit attempt noise. It does not synthesize VIO cross-covariance.
An opt-in [live research observer](live-research-alignment.md) connects it to
native snapshots; the CLI audits bindings without noise by default. An explicit
provider can run in an [owned estimator process](research-alignment-process.md)
through either CLI or Python, with terminal startup/operation deadlines and
request-bound JSON replies. Neither path enables navigation authority.
With `--alignment-refresh-interval-s`, the owned process can refresh conditional
estimates between learned results while their anchors remain retained. The
learned trace records queued/superseded draft IDs; a separate checksummed
`alignment-trace.jsonl` retains numerical inputs, snapshot estimates, original
map-evidence timestamps/ages and withheld decisions. See
[continuous research alignment](continuous-research-alignment.md).
Continuous status now separates a 64 KiB conditional pose summary from its
larger replay write. `latest_snapshot_evidence` links the current sample to
`event_sequence` with state `pending` or `written`; pending is not durable
evidence or publication authority. Existing snapshot/time/fix/health gates are
rechecked by the status accessor, and any journal failure withholds output.
No new telemetry or PX4 wire protocol is implied by this internal status field.
Old live rows without provenance digests are not
eligible for this binding.

Learned shadow paths now expose `vision_nav_candidate_groups_v1` for per-frame
diagnostic association of overlapping metric tile poses. It includes every
accepted tile, pairwise pose/support checks, representative IDs, completeness
and policy metadata. It never fuses poses or converts duplicate evidence into
confidence. See [candidate grouping](learned-candidate-grouping.md); this is not
the temporal hypothesis posterior or the `NavEstimate` publication contract.

## `NavEstimate` and publication

`NavEstimate` is a graph-optimized estimate with `confidence`, `mode`, active tile and diagnostics. It may be used internally in any mode. PX4 publishing is allowed only when all of the following are true:

```text
mode == TRACKING
AND pose/covariance finite
AND age <= configured maximum
AND covariance trace <= configured maximum
AND calibration/map/model versions are compatible
AND the PX4 bridge reports transport health
```

The offline bridge additionally requires declared `body_frame="FRD"`,
`clock_domain="host_monotonic"` and
`covariance_convention="position_then_left_rotation_world"`. The latter is
additive world position followed by a world-frame left rotation perturbation,
not the right-SE(3) convention of research estimator interfaces. Missing fields
remain `None` for legacy producers and prevent conversion. Version IDs on the
estimate must match the supplied compatibility report; timestamps must not be
in the future, and upstream publication vetoes remain binding. Covariance must
be symmetric PSD, not merely have nonnegative diagonal entries.

These checks do not enable transmission: transport is still unimplemented, and
learned outputs remain shadow-only. See [PX4 boundary](px4-integration.md) for
the distinction between the full offline tangent covariance and MAVLink wire
packing, coordinate frames and unsolved clock/reset policy.

Changing this predicate requires a safety review and fault-injection test.

## Configuration compatibility gate

At startup and upon map/model switch, fail startup (or stay `INITIALIZING`) unless:

```text
camera calibration ID is compatible with camera stream
map CRS/origin and tile version are known
global descriptor dimension equals the index feature dimension
sparse/dense feature model versions match map cache metadata
geometry map-point frame equals internal ENU map frame
PX4 output convention/version is explicitly configured
```

Log the complete compatibility decision in one structured startup event.
