# Navigation Foundations Implementation

Status: September 5, 2026. Implements the first part of the
[architecture reevaluation](architecture-reevaluation-2026-09-05.md).
This is shadow/research tooling, not a flight localization release.

## Implemented

- [Continuous research alignment](continuous-research-alignment.md) now updates
  retained-window conditional poses independently of learned-frame arrivals.
  It serializes work through one owned estimator process, records original
  map-fix age and complete replay inputs, and withholds on stale snapshots,
  expired anchors or source failure. It does not propagate past anchor retention.

- [Bounded learned PnP](bounded-learned-pnp.md) now connects large match sets to
  the limited joint estimator using a traceable full-consensus/24-pair-refit
  policy. Original evidence, deterministic selection and wider-support checks
  are retained; explicit conditional noise and shadow-only gates remain.

- Optional [joint VIO pose-window export](vio-joint-pose-contract.md) now retains
  native clone cross-covariances, converts them into declared right-local SE(3)
  coordinates and audits relative uncertainty without assuming independence.
  All 2,689 TUM room1 windows passed the numerical/structural audit with the
  original trajectory unchanged. Correlated map fusion remains separate work.

- A [ROS-free OpenVINS research adapter](openvins-research-replay.md) now runs
  native monocular visual-inertial estimation from image/IMU replay. A rendered
  sensor fixture exercises actual initialization/tracking, with input hashes,
  state/covariance evidence and fault tests. It is GPL research evaluation, not
  a default Pi dependency or real-flight result; correlated output is not yet
  converted into independent metric-smoother factors.

- The same frontend now replays the real calibrated TUM VI room1 sequence with
  explicit 16-bit conversion, fisheye calibration and mask handling. Independent
  IMU-frame evaluation uses no scale/time-offset fitting and reports gaps and
  initialization separately. Two runs produced identical state evidence;
  full-trajectory rigid-aligned position RMSE was 0.0806 m on the associated
  portion. This is handheld local-VIO evidence, not UAV map localization or Pi
  qualification. See the replay document for initial-alignment error and limits.

- A [real metric pose smoother](metric-pose-smoother.md) now provides GTSAM SE(3)
  fixed-lag replay for qualified local odometry and delayed learned 3D map fixes.
  It preserves map/local cross-covariance, transforms camera lever arms, rejects
  repeated evidence and retains a declared common map-error floor. This is not
  a raw VIO frontend or validated covariance model; every result stays shadow-only.
  The original heuristic factor-graph demo now always withholds position.

- Learned map compilation optionally lifts XFeat pixels onto a georeferenced DSM.
  It reverses the model-input resize at pixel centres, applies the original
  raster affine transform, transforms horizontal CRS, samples valid surface
  heights, then converts WGS84 ellipsoidal coordinates to local ENU.
- Invalid surface samples remove aligned keypoints/descriptors; there is no
  zero-height or classical-feature fallback. Tiles with fewer than six valid
  landmarks fail compilation. The source COG, exported tile images and DSM
  are checked against their recorded hashes.
- The feature cache stores `map_points_enu_m` and
  `map_point_variance_enu_m2`. Variances are declared map-error priors, not
  a calibrated camera-pose covariance. Raster masks must exclude unreliable
  or unobserved surfaces before compilation.
- A checksummed landmark provenance record binds map/model IDs, ENU origin,
  datum, accuracy assumptions, source/license and rejected-feature counts.
- Deep asset validation rejects `pnp_3d` without that provenance, compatible
  origin, matching landmark/variance arrays and sufficient spatial support.
  The learned asset promotion command runs deep validation before writing
  the final manifest. Image-only builds no longer default to advertising PnP.
- Shadow matching verifies several retrieved tiles independently. Every attempt
  has correspondence/geometry evidence and latency. Multiple accepted locations,
  missing assets/provider failures, and budget-truncated searches prevent a
  unique candidate. Duplicate tile IDs do not consume additional matcher calls.
- Missing or ambiguous frames break consecutive candidate support. Position
  history scatter is no longer exposed as estimated pose covariance, and the
  last position is no longer replaced by a lagging trajectory average.
- MAVLink capture retains HIGHRES_IMU and SCALED_IMU/2/3 acceleration/angular-rate
  measurements, native timestamps, sensor/source identity and available update
  flags. Scaled values convert to SI; unverified sender axes remain labeled.
  Capture manifests report per-stream cadence, incomplete values and clock steps.
- Picamera2 now records bracketed BOOTTIME/MONOTONIC samples and preserves BGR
  pixels without an extra channel swap. The sole MAVLink reader collects
  periodic TIMESYNC alongside telemetry. An offline drift/uncertainty-gated
  exposure-to-native-attitude audit complements the legacy receipt-time pairing;
  it does not claim calibrated sensor synchronization or replace labels.
- New DatasetRelease and index creation use exposure/native-attitude timing by
  default. Windows carry audit references; portable cross-view datasets check
  image/timestamp/attitude identity and retain audits through training/export.
  Old receipt-time data needs explicit legacy research options. Existing labels
  remain immutable; sensor delay and GNSS label timing are still uncalibrated.

## Build Surface-Backed Learned Assets

Install the map-production extras in your development/training environment:

```bash
python -m pip install -e '.[map-production]'
```

Prepare a DSM and aligned orthophoto with documented accuracy. v1 accepts only
metre-valued WGS84 ellipsoidal surface heights, and the layout origin altitude
must use the same datum. Convert orthometric data using an audited geoid
transformation upstream; changing a metadata label does not convert heights.
Ground-only DTM and unknown units/datum are rejected by this first sampler.

The subsequent [v2 ground-support path](ground-surface-masks.md) explicitly
permits DTM only inside a reviewed, checksum-bound support mask. Existing v1
DSM behavior remains unchanged. Unknown or excluded pixels are removed before
height lifting; no ground support is inferred from missing building annotations.

Fill in `research/elevation-source.example.json` as a separate source record.
Set the raster hash, real source/provenance and measured/provider-supported
horizontal and vertical standard deviations. Null accuracy fields intentionally
fail validation. Paths in that record are relative to the record's folder.

The audited model's local-keypoint output must declare
`coordinate_space: input_pixel_centers`, with the model-input size in its
preprocessing contract. Confirm that convention against the actual exporter;
do not label normalized or source-image coordinates as input-pixel centres.

```bash
python -m vision_nav.neural.map_tile_layout \
  --reference-map /data/reference/reference_map.json \
  --output-dir /data/staging/area-v2 --tile-size-px 512

python -m vision_nav.neural.map_asset_compiler \
  --tile-layout /data/staging/area-v2/tile_layout.json \
  --map-release-id area@2 --model-release /models/model_release.json \
  --elevation-spec /data/elevation-source.json
```

Use the existing `vision_nav.neural.map_compiler` promotion command with its
normal asset arguments plus
`--landmark-provenance /data/staging/area-v2/index/landmark_provenance.json`
and the same `--geodetic-origin-wgs84 LAT LON ELLIPSOID_HEIGHT`. It defaults
to `pnp_3d` only when this provenance is supplied. Then run:

```bash
python -m vision_nav.neural.map_asset_validation --map-release /data/staging/area-v2/map_release.json
```

The lower-level manifest creation/validation functions are not a substitute
for the deep asset audit. A structurally valid manifest is not proof of usable
metric geometry. Failed staging builds must be inspected and rebuilt in a new
staging directory; existing immutable assets are not overwritten.

## Candidate Search

The shadow runtime and benchmark accept `--max-candidate-verifications` (1-24,
default 4) independently from `--retrieval-top-k` (default 12). This caps matcher
and geometry calls per frame. A truncated search reports incomplete evidence;
to evaluate a complete four-candidate set use both arguments with value 4.
These are count bounds, not measured deadline guarantees.

One geometrically accepted tile among the verified retrieval set is still not
global position authority. Overlapping tiles remain ambiguous when their poses
disagree. [Shared metric candidate grouping](learned-candidate-grouping.md) now
combines pose-consistent candidates with overlapping landmark-support boxes for
diagnostic selection, preserving all original observations without summing
confidence. Motion-conditioned temporal comparison and qualified covariance
remain pending. No PX4 output is enabled.

## Capture Changes

The existing training recorder picks up inertial messages when the Pixhawk link
already supplies them. It does not request new message rates, modify PX4
parameters, or open a second serial reader. Check the manifest at
`mavlink.inertial_capture` after a short prop-off recording.

The `vio_ready` flag remains false. Message availability/cadence does not prove
sensor calibration, exposure synchronization, bias/noise parameters or adequate
bandwidth. Keep the corresponding PX4 ULog. HIGHRES_IMU update masks are retained
so unchanged axes cannot be mistaken for a fresh six-axis sample. RAW_IMU ADC
counts are not silently interpreted as SI measurements.

See [capture timing](capture-timing.md) for the new exposure-clock audit,
rejection rules and prop-off data-collection checks. The recorder sends periodic
TIMESYNC diagnostics but no vehicle commands or parameter changes.

## Remaining Work

The [map-conditioned student workflow](map-conditioned-student.md) now provides
portable data preparation, supervised training, ONNX parity and recorded-tensor
shadow inference. [Raw-camera replay](map-conditioned-replay.md) additionally
connects original captured frames to map tiles using shared training
preprocessing and bounded exposure-aligned windows. An optional
[learned retrieval export/index](learned-retrieval-replay.md) now selects those
tiles automatically without GPS; explicit selection remains a diagnostic mode.
Official MegaLoc export now passes full preprocessing/embedding parity on the
Mac, with opt-in actual-checkpoint index/replay checks. UAV-to-map retrieval
accuracy and Pi feasibility still need measurement. The student has not been distilled
from official teachers or connected to the live retrieval/navigation scheduler.

The same replay can now run an optional
[pinned XFeat/LighterGlue reference](learned-local-verification.md) after student
evidence, with planar consistency or DSM-backed camera-pose candidates. Real
bundled checkpoints were exercised on the upstream sample pair; that is not
UAV-to-map accuracy or a Pi flight release. A
[two-stage ONNX CPU export](learned-local-onnx.md) now runs the detector and
learned matcher without PyTorch, including startup reference/cascade parity,
blank-image rejection and unequal real feature counts. Competing candidates remain
ambiguous and covariance/position authority remain unset.

The [local ONNX benchmark](learned-local-benchmark.md) now measures saved-image
warm/cold feature-cache workloads, separate startup, stage percentiles and host
resource evidence. It exercises real local inference without needing the older
four-role ModelRelease. Desktop timing does not establish Pi performance; the
Pi wrapper requires an idle, prop-off acknowledgement and actual Pi identity.

1. Calibrate sensor delay/readout and raw inertial ingress; bind the offline
   exposure/PX4 clock audit to live scheduling and GNSS label timing. Verify on
   the actual camera/Pixhawk before the main capture campaign.
2. Live binding, teacher distillation and real-data evaluation of the new
   candidate-map-conditioned student; the absolute-ENU CNN/GRU is a fixed-area
   research comparison baseline.
3. Evaluate official Bearing-UAV checkpoints and metric geometry datasets on
   RunPod; pin permissions, source revisions and held-out geography splits.
4. Real-data/hardware qualification of the native visual-inertial frontend and
   correlated integration with the metric smoother; motion-conditioned multi-location hypotheses, tile overlap merging,
   continuity policy and calibrated covariance remain outstanding.
5. Pose refitting and uncertainty propagation using map priors, calibration,
   measured timing and real-image residuals; camera distortion/rectification audit.
6. Pi scheduling/latency/thermal testing and provider parity on that hardware.
7. PX4 frame/orientation/covariance/timestamp corrections, then separately
   reviewed vehicle-integration evidence. Flight publication remains disabled.

## Verification

```bash
python tests/run_unit_tests.py
python -m pytest -q tests/test_neural_pipeline.py tests/test_metric_map_landmarks.py tests/test_candidate_verification.py tests/test_inertial_capture.py tests/test_map_conditioned_student.py tests/test_capture_timing.py
```

The raster tests require Rasterio/PyProj; install the map-production extras to
run rather than skip them. Tests cover generated GeoTIFFs and deterministic
learned-interface doubles, not trained-model accuracy or real flight performance.

Reference behavior: [Rasterio transforms](https://rasterio.readthedocs.io/en/stable/topics/transforms.html),
[masked sampling](https://rasterio.readthedocs.io/en/stable/api/rasterio.sample.html),
and [MAVLink inertial fields](https://mavlink.io/en/messages/common.html#HIGHRES_IMU).
