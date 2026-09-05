# Target Vision-First Architecture

For the transfer snapshot, requirements, implementation boundaries and remaining
work, start with the [technical handoff](technical-handoff-2026-09-05.md).

> **Maturity note (2026-08-30):** This document is the target architecture and
> contract, not a claim that every adapter is already flight-ready. See
> [Architecture Reassessment](architecture-reassessment-2026-08.md) for the
> implemented-versus-planned matrix and the approved migration sequence.

> **September 5 revision:** The [reevaluation](architecture-reevaluation-2026-09-05.md)
> revises the target toward a map-conditioned learned student with continuous
> local visual-inertial odometry and asynchronous global map corrections.
> [Implementation status](navigation-foundations-implementation.md) distinguishes
> the landed geometry/candidate/capture foundations from the remaining estimator,
> training and publication work. The CNN/GRU baseline is not a reproduced BEV model.
> A [GTSAM metric smoother](metric-pose-smoother.md) is implemented for qualified
> factor replay. It is not yet a live VIO frontend; all outputs remain shadow-only.

> **Current implementation:** A separate native OpenVINS live frontend and
> opt-in automatic delayed-query pose retention now exist. The pose-only replay
> smoother is not that frontend. See the [latest source-backed assessment](current-navigation-assessment-2026-09-05.md#latest-decision-after-automatic-query-ownership)
> for the implemented-versus-target matrix. Continuous-current global estimation,
> measured sensor uncertainty, sustained Pi execution and aircraft transport
> remain unqualified. FAISS, recovery-only retrieval cadence and a qualified
> temporal global hypothesis bank below are targets, not claims about the live
> research runner.

> **Latest review:** See [live availability and training priorities](current-navigation-assessment-2026-09-05.md#latest-review-live-availability-and-training-priorities).
> The current map-conditioned student uses CNN encoders, spatial cross-attention
> and weighted temporal pooling; the earlier CNN/GRU description above is
> historical. Earlier native 20 Hz observations produced no current global
> updates; later transaction optimizations produced intermittent operator-visible
> updates, still with substantial gaps. See the [latest execution evidence](handoff-verification-2026-09-05.md#child-transaction-and-extended-stream-checks).
> Keep continuous-current estimation,
> physical calibration, geographic holdout accuracy and full Pi load as explicit
> release gates, separate from passing contract tests.

## Coordinate, clock, and calibration contract

- The map world frame is East-North-Up (ENU), metres. Tiles are orthorectified and carry an immutable map version, epoch, CRS/ENU anchor, resolution and acquisition metadata.
- Camera timestamps use a monotonic hardware clock. All transforms are recorded with the calibration version; no anonymous “current calibration” values are permitted.
- `body_T_camera` is calibrated offline. The offline PX4-boundary conversion
  rotates ENU pose and covariance into NED with explicit body/tangent semantics.
  Actual MAVLink frames require a firmware-pinned adapter; transport remains
  unimplemented. See [PX4 integration](px4-integration.md).
- Calibrated camera plus synchronized inertial measurements is the nominal navigation path. PX4 attitude is context, not a translation estimator or a substitute for qualified raw IMU. Camera-only remains a constrained experiment: scale must come from a known-height/ground-plane constraint, stereo/multi-camera geometry, or a globally registered 3D map. Monocular VO alone must not be published as metric global navigation.

## Offline map build

1. Validate imagery orthorectification/DEM alignment, cloud masks, temporal metadata and licensing; reject tiles below the GSD required by the target flight altitude.
2. Produce a multi-resolution tile pyramid and a seam-overlapped tiling grid. Preserve a parent/child ID so a coarse candidate constrains fine search.
3. Compute frozen MegaLoc descriptors; build a bounded, release-bound FAISS index for recovery.
4. Cache XFeat sparse features, learned correspondence metadata, optional 3D ENU landmarks, semantic/topic auxiliary embeddings, and elevation/visibility rasters. RoMa/VGGT/MASt3R-derived assets are cloud-only provenance or teacher inputs. Publish only after a complete map-version manifest validates checksums.
5. Evaluate every map epoch against held-out seasonal/altitude/viewpoint flights. Use map change detection to reduce confidence rather than silently treating stale tiles as truth.

## Onboard data flow

| Rate | Stage | Output / rejection rule |
| --- | --- | --- |
| camera rate | exposure/blur/clock/calibration checks | accepted frame or explicit rejection |
| continuous local frontend | calibrated visual-inertial odometry | local motion, velocity/bias and uncertainty; unresolved scale or sensor timing prevents metric use |
| keyframe cadence | distilled temporal BEV/bearing student -> active-tile window | temporal context and pose prior |
| recovery cadence and on loss | MegaLoc coarse retrieval -> Top K | bounded global recovery candidates |
| candidate | XFeat + LighterGlue -> USAC/MAGSAC | discard below inlier/ratio/RMSE gates |
| survivor | learned correspondence plus metric geometry refit | spatially distributed, certainty-weighted inliers |
| backend | multi-hypothesis + robust graph update | globally referenced pose and covariance |

Video2BEV is an offline 3DGS-rendered-BEV retrieval/matching teacher reference,
not a released short-window temporal network. Distilling reconstructed-view
evidence into our onboard temporal student is a separate, unqualified experiment.
See the [pinned source/input audit](video2bev-reproduction.md). MASt3R-SLAM/VGGT and RoMa
remain cloud-only teacher/map-production tools; they are not Pi runtime stages.
TopicGeo-style semantic/topic features are a second retrieval cue, not a
position measurement.

## Confidence and ambiguity

`HypothesisBank` maintains Top-N tile hypotheses in log space. A global update needs sufficient USAC/MAGSAC inliers, ratio, and reprojection residual; agreement of global, sparse/dense, and optional semantic evidence; spatial distribution of inliers (add a grid-coverage gate in the model binding); and persistent support across frames, high posterior, and separation from runner-up.

When no hypothesis clears the gate, retain VO only as a local factor, declare `RECOVERING`, and withhold PX4 external-vision output. `NO_GLOBAL_FIX` is not an error condition to hide: it is a normal, observable state for the vehicle safety policy.

## Factor graph

The target includes a qualified local VIO frontend with velocity/bias states
and a map/local alignment estimator. The implemented replay backend uses
`X_k = local_T_body`, `A = local_T_map`, a local gauge prior, metric relative
motion factors and delayed map-body `Between(A, X_k)` factors. Raw local motion
is retained separately from retrospectively optimized states. See the
[smoother contract and limitations](metric-pose-smoother.md); the raw frontend
and its calibrated noise model are not implemented by that backend.

Global pose/reprojection factors require robust losses and a qualified pose
covariance, including shared map/calibration/timing uncertainty. Loop closures
and multi-location ambiguity need their own acceptance policies. A global visual
update must never hard-reset a mature local trajectory or reuse a cached fix as
new evidence. Future raw-IMU integration needs `ImuPreintegration(X,V,B)` and bias
states; the pose-only replay backend does not provide these. Camera-only startup
and recovery must remain explicitly scale-qualified rather than bypassing them.
