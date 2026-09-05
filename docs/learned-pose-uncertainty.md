# Learned Pose Uncertainty: Conditional Research Path

The learned local verifier now preserves the exact original-match indices used
by its PnP refinement. A separate research command propagates explicitly supplied
image, map-point and camera-intrinsic errors into right-camera pose covariance.
It never fills the active runtime's publication covariance or enables PX4 output.

## What Is Implemented

- `neural/shadow_geometry.py` retains float64 metric coordinates and reports
  `inlier_match_indices` in the original match-pair order, including after score
  filtering and RANSAC. It names the unweighted AP3P/RANSAC/RefineLM solver.
- `estimation/reprojection_uncertainty.py` consumes one accepted PnP attempt
  from `ReplayLocalVerifier`, plus a separately declared joint error model.
- Full covariance retains correlations across points, between image and map
  coordinates, and with the shared camera intrinsics. No error magnitude is
  inferred from descriptor confidence, RANSAC scores or residual RMS.
- Rank/conditioning and local stationarity checks withhold unusable results.
- Analytic derivatives are checked against numerical right-SE(3) perturbations.
  A synthetic nonlinear Monte Carlo test performs 1,600 actual OpenCV PnP refits
  with pixel, point, shared map-translation and shared focal-length noise.

OpenCV's PnP refinement minimizes reprojection error. The covariance must match
that estimator; merely reporting the inverse weighted information would describe
a different fit when the existing fit is unweighted.
[OpenCV PnP and refinement](https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html).

## Mathematical Contract

The nominal pose is `T_map_camera`. Perturbations multiply on its right as
`T * Exp(xi)` with `xi = [rx, ry, rz, tx, ty, tz]`, radians then metres in camera
coordinates. Pixels are rectified and expressed in the exact resized image
coordinate system used by the matcher. K is zero-skew with known image geometry.

For N original matches, the complete input-error vector is ordered:

```text
[u0,v0, ..., uN-1,vN-1,
 X0,Y0,Z0, ..., XN-1,YN-1,ZN-1,
 fx,fy,cx,cy]
```

The declared covariance is `(5N+4) x (5N+4)`. Spatial coordinates are ENU metres;
all image/intrinsic entries use model-input pixels. It must be finite, symmetric
and positive semidefinite. Zero variance in some input dimensions is permitted
only as an explicit conditional research assumption. Resulting pose covariance
must remain positive definite. No diagonal jitter or guessed noise is added.

Let `r = project(T, P, K) - observed_pixels`, `H = dr/dxi` and `B = dr/dinput`.
For a fixed correct inlier set and small errors near the unweighted optimum:

```text
S = -pinv(H) B
P_pose = S P_input S^T
```

The implementation uses a column-scaled full-rank SVD rather than forming and
inverting the normal equations. It does not change the original pose. Its
normalized condition limit and stationarity tolerance are numerical checks, not
calibrated navigation-integrity thresholds. This is a Gauss-Newton approximation;
it omits higher-order residual Hessian terms and selection uncertainty.

Map translation shared by every landmark remains a shared pose error. It does
not shrink with the number of inliers. Conversely, appropriately correlated
pixel and principal-point shifts can cancel. Both behaviors have analytic tests.

## Request And Command

Create a JSON request with:

| Field | Required content |
| --- | --- |
| `schema_version` | `vision_nav_pnp_uncertainty_request_v1` |
| `attempt` | One complete accepted surface-backed replay attempt, not a homography result |
| `context` | `timestamp_ns`, `map_release_id`, `model_release_id`, `calibration_id`, `clock_id`, `uncertainty_model_id` |
| `uncertainty.attempt_sha256` | Canonical JSON SHA-256 of the exact attempt, using `payload_digest` |
| `uncertainty.joint_measurement_covariance` | Full covariance in the order above, for all original matches |
| `uncertainty.scope` | `synthetic_contract_test` or `unvalidated_research_model` |
| `uncertainty.noise_model_description` | Source and meaning of the assumed errors; no empty label |

The following assumption fields under `uncertainty` are mandatory and exact:

```json
{
  "measurement_order": "all_query_uv_then_all_map_xyz_then_fx_fy_cx_cy",
  "correspondences": "fixed_correct_inliers_conditional",
  "map_systematics": "included_in_joint_measurement_covariance",
  "rectification_error": "included_in_joint_measurement_covariance",
  "timing_error": "not_modeled",
  "camera_extrinsic_error": "not_modeled"
}
```

This interface deliberately does not provide a default noise matrix. The existing
DSM diagonal priors do not establish across-point independence or camera error,
so they cannot alone construct this request. A higher-dimensional calibration
model may first propagate into rectified pixels/K, retaining their cross terms.

Use the research/estimation environment after installing the project:

```bash
vision-nav-assess-pnp-uncertainty --request request.json --output new-result.json
# Equivalent without reinstalling console entrypoints:
python -m vision_nav.estimation.reprojection_uncertainty \
  --request request.json --output new-result.json
```

The CLI bounds requests to 16 MiB and 6-128 original matches. It never silently
subsamples inliers or overwrites an output. Exit 0 means a conditional research
calculation, not flight acceptance; exit 2 means withheld numerical evidence.
Malformed contracts raise an error without producing a result.

The output includes request/implementation hashes, the nominal camera pose,
conditional covariance, input sensitivity S and diagnostic conditions. It always
reports `NO_GLOBAL_FIX`, `position_authority=false`, `covariance_calibrated=false`,
`publication_covariance=null`, `vio_cross_covariance=null` and PX4 disabled.

## Fusion Boundary And Remaining Work

Do not feed independently generated per-frame covariances into the estimator as
independent fixes. With validated shared input models, a later adapter can use
`S_i C_input_i_input_j S_j^T` for cross-fix covariance and propagate VIO/input
cross-covariance consistently. Tangent, residual sign and timestamp conventions
must all match the estimator's contract.

Shared map error is already included in this calculation. Adding the same map
error again as the correlated-window solver's common map floor would double
count it. The [joint replay adapter](joint-pnp-vio-alignment.md) now connects the
two paths only with an explicit complete cross-frame/VIO input model and a zero
additional map floor. It recalculates each PnP result and rejects inconsistent
ownership. It does not infer those input correlations from model scores.

This calculation conditions on the correct map, correct inliers and local
linearization. It does not model wrong matches, multiple PnP solutions,
unobservable terrain, timing errors, camera mount/extrinsic uncertainty or VIO
correlation. A single Gaussian cannot describe geographically distinct aliases.
Covariance calibration still requires independent held-out aircraft sequences,
an audited reference, map/calibration error measurement and empirical coverage.
Until then, active learned candidates retain `pose_covariance=null`.

## Verification

```bash
python -m pytest -q tests/test_reprojection_uncertainty.py
```

The tests cover isotropic analytic covariance, shared map error, image/intrinsic
cross terms, nonlinear Monte Carlo, Jacobians, deterministic results, inlier
identity after filtering, invalid contracts, degenerate/nonstationary geometry
and immutable CLI output. They validate implementation algebra under synthetic
assumptions, not an aircraft error model or trained-model accuracy.

September 5 verification: 21 uncertainty tests; 136 tests in the combined native
VIO/estimator suite (including those 21); 109 learned-pipeline regression tests
with the prepared reference-model artifacts; and 104 existing unit-runner checks
in the map-enabled environment. Suites overlap and are not an additive count.
The learned suite retains 170 upstream/export warnings. No Pi or flight test was
performed for this change.

Recorded synthetic example: `research/outputs/pnp-uncertainty-synthetic-20260905-r1/`.
It contains the complete request and outputs from both NumPy 1 and NumPy 2
environments. Covariance agreed at rtol 1e-10 / atol 1e-12. This example has
16 synthetic matches, 0.2 pixel independent error and a common ENU map-translation
covariance of diag(0.4, 0.5, 0.6) m2. The common error is retained. These values
are test assumptions, not measured sensor specifications or runtime defaults.
