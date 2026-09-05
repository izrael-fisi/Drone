# Bounded Learned PnP Fits

The live/replay learned verifier and compiled-feature shadow path now use an
explicit bounded metric fitting policy. This is research estimator integration,
not a new learned model, a field benchmark or flight-control authorization.
Planar consistency checks are unchanged.

## Policy

`vision_nav.neural.bounded_pnp` records `vision_nav_bounded_pnp_fit_v1`:

1. Run existing scored AP3P RANSAC and unweighted LM on the full correspondence
   set. Its inlier count, ratio, image coverage, reprojection and cheirality
   gates must pass before any reduction. Failed full fits stay failed.
2. If more than 24 pairs were supplied, distribute the original inliers across
   a 4x4 image grid. Select one point per occupied cell per round, preferring
   higher match scores and then stable query/map feature IDs. Retain at most
   24 pairs. Preserve their original source order in the final arrays.
3. Run a new robust PnP and unweighted LM on those pairs. Reapply all existing
   geometry thresholds. A configured minimum above 24 is explicitly unavailable,
   not silently lowered.
4. Project the full original consensus through the reduced-fit pose. Reject
   if its RMSE exceeds the existing reprojection threshold or any original
   inlier is behind the camera.

The selection is deterministic for a fixed original consensus. This is not a
claim of bitwise RANSAC determinism across OpenCV builds or platforms. The
selected count is an engineering bound, not a measured Pi optimum. Source sets
above 2048 pairs are rejected explicitly.

## Trace And Estimator Boundary

`geometry.fit_selection` retains the policy, every original matched pixel/3D
point/feature identity/score, camera intrinsics, thresholds, source digest,
original consensus, selected source indices and the wider-consensus residual.
The attempt's matched arrays and inlier indices describe the **new final fit**.
Do not reuse an uncertainty object or covariance from the original fit.

Binding and conditional uncertainty assessment verify the source digest,
deterministic selection, original gates, source feature bounds, exact selected
arrays and reprojection at the original and final poses. They do not truncate
an oversized request or refit a pose inside the estimator. Unchanged small or
legacy attempts without a selection record retain the existing 6..128 input
contract. They can still exceed a multi-fix matrix budget when combined.

The dense input model uses five dimensions per correspondence (image uv and
map xyz), plus four intrinsics. Eight 24-pair attempts use
`8 * (5 * 24 + 4) = 992` dimensions, within the existing 1024 limit. This does
**not** guarantee the separate JSON trace size, memory, retention or runtime
deadline budgets. Complete cross-observation/VIO noise is still mandatory;
there is no automatic independent-noise approximation.

The owned model and estimator processes include the fitting/validation source
files in their provenance checks. A changed source requires a new session.

## Uncertainty And Qualification

Selection uncertainty, correspondence mistakes and unsearched alternate
locations are not modeled. Match scores order candidates; they do not become
measurement variances. Conditional covariance still requires explicit joint
pixel/map/intrinsics noise and passes the existing stationarity and numerical
checks. Measured timing, extrinsics, shared map errors and VIO correlations are
unfinished. Every estimate remains `NO_GLOBAL_FIX`, `position_authority=false`,
`covariance_calibrated=false`, with no publication covariance and PX4 disabled.

`tests/test_bounded_pnp.py` covers full-set rejection, spatial selection,
bounded refitting, wider-consensus disagreement, malformed/tampered traces and
synthetic binding/uncertainty. The opt-in actual XFeat/LighterGlue test in
`tests/test_surface_mask.py` exercises the complete masked-map fit and
conditional uncertainty calculation. Its imagery comes from checksum-bound
ONNX parity fixtures; map elevations, intrinsics and pixel noise are synthetic.
This is real model execution, not real-flight accuracy or calibrated confidence.

## Verification Record

September 5, 2026, on the development Mac:

- 245 focused estimator, binding, VIO and PX4-boundary tests passed using the
  Python 3.11 estimator environment and native OpenVINS binary.
- 132 broader model/native/correspondence/map tests passed using the Python
  3.13 ONNX environment. Three optional direct-PyTorch checkpoint tests were
  skipped because no `VISION_NAV_LOCAL_REFERENCE_CONFIG` was supplied. Actual
  MegaLoc and XFeat/LighterGlue ONNX and native-process tests did run.
- After adding the explicit prebuilt-map-path regression, all 13 candidate
  verification tests passed. This overlaps the broader suite above.
- All 104 main project checks passed. No desktop files changed in this pass;
  desktop build and Rust checks were not rerun.

The actual-model masked fixture retained 398 original matches and fit 24 pairs;
both geometry and conditional synthetic-noise assessment passed. Complete
inputs and assessment are in the generated, uncommitted
`research/outputs/bounded-pnp-actual-uncertainty-20260905-r1/` evidence directory.
Broader logs/fixtures are under `bounded-pnp-estimator-tests-20260905-r1` and
`bounded-pnp-model-tests-20260905-r1`. These counts do not establish field
qualification. Dependency deprecation warnings remain in the map/ONNX tools.
