# Autonomy And Ground Control Implementation Plan

This plan executes the recommendations from
[Autonomy And Ground Control Research](autonomy-ground-control-research.md)
without changing the project scope. The target remains a low-cost
GNSS-denied terrain-navigation module plus desktop setup software.

## Delivery Tracks

### Track 1: External Position Output

Goal: make every autopilot output path share one tested conversion layer.

Status:

- In progress: `vision_nav.external_position` defines local ENU input,
  local NED/FRD output, MAVLink covariance ordering, yaw conversion, and
  payload shapes for `VISION_POSITION_ESTIMATE` and `ODOMETRY`.
- In progress: `mavlink_bridge.py` now uses the shared conversion layer for
  existing `VISION_POSITION_ESTIMATE` sends.
- In progress: `send_odometry_match_result()` and
  `VISION_NAV_MAVLINK_MESSAGE=odometry` are available for bench testing the
  richer PX4 external-vision path.
- In progress: `vision-nav-send-mavlink-log` now supports selectable
  `vision_position_estimate` or `odometry` output, rate limiting, repeated log
  sends, and skip-reason summaries for sender-side PX4 SITL smoke tests.
- In progress: `scripts/dev/px4_sitl_external_vision_smoke.sh` generates a
  synthetic accepted/rejected external-vision log and sends it to a PX4 SITL UDP
  endpoint so the operator can verify `vehicle_visual_odometry` reception.
- Done: the PX4 SITL smoke script writes an evidence-session folder with the
  synthetic sender log, session manifest, receiver-capture instructions, stable
  capture filenames, and a dry-run mode used by local preflight.
- Done: `vision-nav-evaluate-px4-sitl-session` and
  `scripts/dev/evaluate_px4_sitl_session.sh` evaluate an evidence-session
  folder directly, write `receiver_evidence.json`, and fail cleanly until the
  PX4 listener capture exists.
- Done: `vision-nav-evaluate-px4-sitl-evidence` and
  `scripts/dev/evaluate_px4_sitl_receiver_evidence.sh` convert captured PX4
  `listener vehicle_visual_odometry` / `mavlink status` output into pass/fail
  receiver evidence for SITL bench runs.
- Done: `scripts/dev/run_px4_sitl_external_vision_capture.sh` provides a
  tmux-based bench harness that can start PX4 SITL, send the synthetic
  external-vision stream, capture PX4 shell receiver output, and run the session
  evaluator when a local PX4 checkout is available.
- Done: the PX4 SITL smoke script, automated capture harness, and session
  evaluator emit stable `__VISION_NAV_PX4_SITL_SESSION__` /
  `__VISION_NAV_PX4_SITL_REPORT__` markers so receiver proof artifacts can be
  copied into support bundles and final readiness audits without path guessing.
- Done: support bundles can package PX4 SITL receiver captures and the generated
  receiver-evidence report so bench verification can be reviewed later from the
  desktop app.
- Done: support bundles can ingest a full PX4 SITL evidence-session folder via
  `--px4-sitl-session` / `VISION_NAV_PX4_SITL_SESSION`, copy it under
  `extras/px4_sitl_session/`, and publish the parsed receiver report under
  `summaries/px4_sitl_evidence/`.
- Done: `vision-nav-check-px4-params` and `scripts/pi/check_px4_params.sh`
  evaluate exported PX4 parameter files for external-vision bench readiness
  without modifying the flight controller.
- Done: `vision-nav-bench-readiness` evaluates support-bundle ZIPs/manifests as
  a single bench-readiness gate across terrain bundle health, runtime logs,
  replay gates, PX4 receiver evidence, and PX4 parameter checks.
- Done: support-bundle creation now writes `summaries/bench_readiness.json` and
  embeds the same status in `support_manifest.json` so downloaded bench reports
  carry their own readiness result.
- Done: the bench-readiness gate now counts optional ArduPilot ExternalNav
  parameter reports when present, without making ArduPilot mandatory for the
  PX4-first bench path.
- Done: PX4 external-vision bench guidance is documented in
  [PX4 External Vision Bench Guide](px4-external-vision-bench.md).
- Done: runtime logs include `external_position_health` snapshots with message
  type, send rate, latency, covariance warnings, and skip reasons.
- Done: `ODOMETRY` output includes reset-counter tracking for estimator reset
  epochs, map changes, and backward timestamps.

Next tasks:

1. Run PX4 SITL receiver verification and save the evaluator report proving
   that EKF2/uORB receives the selected message path at the expected rate.
2. Use the automated capture harness to collect repeatable receiver artifacts
   from the local PX4 environment, then include those artifacts in a support
   bundle.

Acceptance checks:

- Unit tests prove ENU to NED axis mapping, yaw conversion, and covariance
  placement.
- Existing MAVLink pose output continues to pass.
- Rejected or incomplete terrain matches are not sent.

### Track 2: ROS 2 Companion Runtime

Goal: make ROS 2 the modular runtime spine while keeping the direct Python CLI
and MAVLink path for simple Pi deployments.

Status:

- In progress: `vision-nav-ros2-replay-log` converts accepted runtime log
  records into ROS-compatible `nav_msgs/Odometry` dictionaries and
  `diagnostic_msgs`-style health records.
- In progress: the same command can publish with `rclpy` when ROS 2 packages are
  installed and sourced.
- In progress: `vision-nav-run-terrain-loop --ros2-publish` can publish live
  odometry and diagnostics during camera/matcher runtime.
- In progress: repo-local launch files under `ros2/launch/` start live terrain
  runtime publishing or replay publishing with repeatable arguments.
- Done: Pi diagnostics and Module Setup now include an optional Micro
  XRCE-DDS Agent check for PX4 uXRCE-DDS/ROS 2 bridge readiness.
- In progress: `vision-nav-ros2-replay-log --export-rosbag-jsonl` writes a
  dependency-free topic replay artifact with ROS message types, topics,
  timestamps, and payloads for offline field-log review before native rosbag2 is
  required.
- In progress: the same JSONL export can include bounded
  `sensor_msgs/msg/CompressedImage` camera-frame topic records from runtime
  `frame_path` entries, with relative paths resolved from the log directory.
- Done: `ros2/drone_vision_nav/` provides a thin `ament_python` package wrapper
  with package metadata, installed launch profiles, and `terrain_nav_live` /
  `terrain_nav_replay` console scripts for colcon-based ROS 2 workstations.

Tasks:

1. Add PX4 SITL launch profile arguments once SITL receiver verification is
   available.
2. Add native rosbag2/MCAP conversion after a ROS 2 workstation workflow is
   available.

Acceptance checks:

- ROS 2 topics can replay a saved frame log on the desktop.
- PX4 SITL can receive external-position output through ROS 2 or direct MAVLink.
- The direct Python `vision-nav-run-terrain-loop` command remains usable.

### Track 3: Terrain Map Bundle Pipeline

Goal: make map preparation reliable enough for customer field use.

Status:

- In progress: `vision_nav.geospatial_health` reports map georeference,
  CRS/GSD, raster metadata, lightweight COG/GeoTIFF readiness, STAC asset
  validity, tile-index readiness, feature counts, feature-density quality,
  estimated Pi runtime cost, local bounds, and blocking issues.
- In progress: `vision-nav-build-terrain-bundle` writes `bundle_health.json`
  and returns the same health summary to the desktop app after bundle build.
- In progress: `vision-nav-validate-bundle` and
  `vision-nav-validate-terrain-bundle` include geospatial health checks for
  terrain bundles.
- In progress: Mission Planner shows bundle map health, tile count, feature
  count, and GSD after a bundle build.
- In progress: `bundle_health.json` now includes checksum status and source
  provenance while excluding generated health reports from checksum coverage to
  avoid self-referential mismatches.
- In progress: Mission Planner bundle results display checksum status, covered
  file count, map source, source filename/name, georeference source, CRS, and
  georeference confidence.
- In progress: terrain bundles now discover optional `elevation/dem.tif` and
  `elevation/dsm.tif` assets, declare them in manifest/STAC/runtime config, and
  report elevation-readiness in bundle health and desktop/support summaries.
- In progress: Maps can attach optional DEM/DSM GeoTIFFs to saved map sources
  so Mission Planner bundle builds carry elevation assets into runtime bundles.
- In progress: `bundle_health.json` now includes a mission terrain profile when
  DEM/DSM assets can be sampled, with terrain relief, estimated minimum AGL, and
  AGL-to-map-GSD warnings shown in Mission Planner bundle results.
- In progress: terrain matching now reports hierarchical tile retrieval
  metadata and uses prior-local radius search when a pose prior exists, or
  spatially distributed coarse candidates at startup with no prior.
- In progress: terrain tile descriptors now include a compact grayscale global
  descriptor, and runtime matching reranks coarse/prior candidates by visual
  descriptor distance before local ORB/AKAZE homography.
- In progress: `bundle_health.json` now includes feature-density heatmap cells
  for terrain tiles, and Mission Planner renders a compact low/fair/good/dense
  map-quality heatmap after bundle build.
- In progress: raster health now records optional GDAL-backed TIFF/GeoTIFF
  validation when Python GDAL bindings are available, including driver,
  projection, geotransform, block layout, overview count, and COG readiness.
- In progress: terrain profile health now emits bounded preview points, and
  Mission Planner renders a compact terrain/flight profile preview after bundle
  build.
- Done: downloaded support-bundle browsing shows parsed checksum status, map
  source provenance, georeference confidence, and replay-gate state.
- In progress: `vision-nav-benchmark-retrieval` benchmarks the current
  lightweight grayscale global descriptor on replay logs, reporting top-k
  recall and mean rank while marking the optional neural retrieval backend as
  unavailable until neural descriptors are generated.

Tasks:

1. Validate the full terrain-bundle pipeline on real field replay logs and
   promote recurring failure modes into Track 5 replay gates.

Acceptance checks:

- Invalid georeference blocks terrain bundle validation.
- The desktop app shows map health before the Pi uses the bundle.
- A wrong-map replay produces rejected matches, not low-covariance outputs.

### Track 4: Desktop Setup And Mission UX

Goal: make the customer workflow guided, diagnosable, and hard to misuse.

Status:

- In progress: Mission Planner now tracks a session-local plan fingerprint and
  shows invalid, not built, stale bundle, not uploaded, uploaded, or
  bundle-ready state after build/upload actions.
- In progress: plan-state checks include mission/map readiness, selected map
  source, output bundle path, remote bundle path, QGC plan content, and desktop
  mission JSON content.
- Done: Mission Planner persists build/upload fingerprints and timestamps
  across app restarts and shows whether the active imported/exported `.plan`
  file has unsaved local changes.
- In progress: Mission Planner now records GNSS-denied readiness actions in the
  mission bundle metadata: satellite-source disabled state, map-position reset,
  heading reset, home reset, and estimator-health status.
- In progress: Module Setup chains Wi-Fi SSH identity, repo sync/install,
  runtime verification, camera preview/health, time sync, MAVLink endpoint
  validation, optional Micro XRCE-DDS Agent readiness, calibration image
  capture, synthetic smoke testing, and deployed bundle validation from the app.
- In progress: Module Setup now has per-check run actions, a bench-report
  action that validates the deployed terrain bundle and downloads the Pi support
  bundle, plus a local JSON setup-report export for install/bench audit trails.
- In progress: Module Setup can register the latest Pi terrain log as a field
  evidence case with expected behavior, condition tags, notes, replace control,
  and strict full-gate control, then leave the generated report for support
  bundle auto-ingest.
- In progress: Mission Planner now hands an uploaded Pi mission bundle directly
  to the matching Module Setup tab for one-click bench-report creation.
- In progress: Devices and Module Setup now provide local Wi-Fi discovery for
  saved Pi hosts, common Raspberry Pi mDNS names, and local SSH neighbors, with
  recent discoveries persisted in desktop storage.
- Done: Discovery now shows active desktop IPv4 interface/subnet hints, supports
  adapter selection, summarizes mDNS/SSH failure modes, and provides a copyable
  mDNS/SSH/firewall checklist that is also written into setup reports.
- In progress: Mission Planner bundle results show imported DEM/DSM terrain
  profile, estimated minimum AGL, AGL/GSD warnings, and map-quality heatmaps.
- In progress: Mission Planner now exposes terrain planning constraints,
  offline cache state, and route segmentation metadata, then compares the
  configured terrain limits against bundle terrain-profile health after build.
- In progress: Mission Planner now exports deterministic terrain route-segment
  records with split coordinates, cumulative distances, longest segment length,
  and split reasons in the app mission JSON and QGC `visionNavigation`
  metadata.

Tasks:

1. Tune route splitting defaults after field replay data confirms the preferred
   segmentation rule.
2. Wire GNSS-denied readiness actions to live runtime telemetry and autopilot
   checklist validation.

Acceptance checks:

- A new operator can get from fresh Pi to bench report without shell commands
  except the initial OS flash.
- Runtime output clearly shows active map, active output path, estimator health,
  and last accepted/rejected match reason.

### Track 5: Validation And Product Risk Controls

Goal: prove the estimator rejects bad information before field use.

Status:

- In progress: `vision_nav.support_bundle` creates a zip package containing
  runtime metadata, git/app version state, bundle manifest/config/health,
  selected logs, generated log summaries, optional autopilot metadata, and
  optional full map assets.
- In progress: `scripts/pi/create_support_bundle.sh` packages the default Pi
  terrain/runtime/replay logs into `~/DroneTransfer/outgoing/support-bundles/`.
- In progress: Devices and Mission Planner runtime controls expose one-click
  support-bundle creation and desktop download for connected Raspberry Pi
  modules.
- In progress: Devices and Mission Planner list the most recent downloaded
  support-bundle ZIPs under `~/DroneTransfer/from-pi/support-bundles/` with
  parsed bundle health, checksum status, map provenance, georeference
  confidence, and replay-gate status.
- In progress: `vision_nav.replay_gates` evaluates replay/runtime logs for
  `good_map`, `degraded`, and `wrong_map` expected behavior. Wrong-map cases
  fail if any map match is accepted by default.
- In progress: replay gates now require accepted good-map records to include
  confidence, inliers, reprojection error, scale confidence, covariance, and
  motion-consistency checks; degraded accepted weak matches must inflate
  covariance.
- In progress: support bundles include replay-gate reports when a replay-case
  manifest is provided.
- In progress: the desktop support-bundle list now supports compact details,
  reveal in file manager, copy path, and stale ZIP deletion for downloaded bench
  artifacts.
- In progress: support-bundle details now read downloaded ZIP archives directly
  and show metadata, bundle health, log summaries, accepted rates, and
  replay-gate case issues without manually extracting the archive.
- In progress: support-bundle details now include compact per-record previews
  from bundled runtime/replay JSONL logs so accepted/rejected match reasons,
  confidence, tile IDs, and external-position state are visible in the app.
- In progress: support-bundle details now include bounded previews for small
  camera/debug/replay image artifacts inside downloaded ZIPs while skipping
  full map, orthophoto, tile, descriptor, and elevation assets.
- In progress: support-bundle details now include PX4 SITL receiver evidence
  status, sample counts, latest sample age, local position, MAVLink version, UDP
  link hint, and report issues when receiver captures are provided.
- In progress: support-bundle details now include PX4 external-vision parameter
  readiness status, EKF2 external-vision control value, height reference, GNSS
  control, covariance-source mode, delay, and report issues when a parameter
  export is provided.
- Done: support bundles can be evaluated by `vision-nav-bench-readiness` to
  produce one pass/degraded/fail bench artifact status instead of relying on
  separate manual inspections.
- In progress: downloaded support-bundle details now show the embedded
  bench-readiness status and per-check messages in the desktop app.
- In progress: `data/replay_cases/` defines the replay case registry shape for
  good texture, degraded, and wrong-map datasets.
- Done: `vision-nav-evaluate-replay-manifest` evaluates replay-case manifests
  outside support-bundle creation and writes per-case gate reports.
- Done: `data/replay_cases/synthetic_smoke/` provides deterministic local
  smoke coverage for good-map, degraded low-texture, and wrong-map rejection
  behavior; `local_preflight.sh` evaluates this suite.
- Done: `vision-nav-audit-replay-coverage` audits replay manifests for required
  real field coverage across good texture, low texture, blur, seasonal change,
  lighting change, altitude/scale change, repeated patterns, and wrong-map
  rejection.
- Done: `vision-nav-field-evidence-gate` combines real field coverage,
  manifest-relative log existence, and per-case replay gate evaluation into one
  pass/fail field evidence artifact.
- Done: `vision-nav-register-replay-case` registers copied field, bench, or
  synthetic logs into replay manifests with dataset type, condition tags, and
  stable manifest-relative log paths.
- Done: `vision-nav-benchmark-feature-methods` compares ORB, AKAZE, SIFT, and
  future neural methods on the same replay case, using the same replay gates as
  support bundles and marking neural descriptors unavailable until they are
  generated.
- Done: `scripts/pi/run_feature_method_benchmark.sh` wraps feature-method
  benchmark generation on the Pi, writes a stable JSON report under
  `~/DroneTransfer/outgoing/feature-method-bench/`, and emits a marker for
  desktop download.
- Done: Module Setup exposes a `Feature Benchmark` SSH action that compares
  low-compute methods against the latest field replay log, downloads the JSON
  report, and lists recommended method plus per-method accepted rates.
- Done: support bundles ingest feature-method benchmark report files or output
  directories, copy them into `extras/feature_method_benchmarks/`, publish
  parsed JSON under `summaries/feature_method_benchmarks/`, and count the result
  in bench readiness when present.
- Done: support bundles ingest `vision-nav-field-evidence-gate` reports, copy
  them under `extras/field_evidence/`, publish parsed JSON under
  `summaries/field_evidence/`, and count the result in bench readiness when
  present.
- Done: `scripts/pi/register_field_replay_case.sh` registers Pi terrain
  runtime/replay logs into the outgoing field replay manifest, writes the
  combined field-evidence report, and leaves it at the default path that support
  bundles auto-ingest.
- Done: the desktop Module Setup flow can run the same field-case registration
  over SSH so evidence collection can be driven from the customer app, not only
  from a Pi shell.
- Done: field-case registration emits a stable field-evidence report marker so
  Module Setup downloads `field_evidence_report.json` after each registration
  and shows the real-field coverage checklist from the desktop app.
- Done: downloaded support-bundle details show field-evidence requirement
  status per condition, making missing real-world coverage visible from the app
  without opening JSON reports.
- Done: `vision-nav-tune-replay-thresholds` generates the
  `threshold_tuning_report.json` artifact from a replay manifest, records the
  selected gate config and observed margins, and fails unless the real field
  coverage audit and replay gates both pass.
- Done: `scripts/pi/run_threshold_tuning_report.sh` wraps threshold report
  generation on the Pi and emits a stable report marker for desktop download.
- Done: Module Setup exposes a `Threshold Tuning` SSH action that generates and
  downloads the threshold report after field cases are registered.
- Done: support bundles ingest threshold-tuning reports, copy the raw JSON under
  `extras/threshold_tuning/`, publish parsed reports under
  `summaries/threshold_tuning/`, and let the final autonomy-readiness audit use
  the bundled threshold proof.
- Done: downloaded support-bundle details show threshold-tuning status,
  field-case counts, and accepted-rate margins beside field evidence and method
  benchmark evidence.
- Done: `vision-nav-autonomy-readiness` provides a strict goal-level audit
  across this research document, the implementation plan, support-bundle bench
  readiness, PX4 receiver proof, real field evidence, feature-method benchmark
  evidence, and threshold-tuning proof. It intentionally fails until the
  external PX4 and field-log artifacts exist.
- Done: `scripts/pi/run_autonomy_readiness_audit.sh` runs the same final audit
  on the Pi against the latest support bundle and writes
  `autonomy_readiness_report.json` for transfer or support review.
- Done: `scripts/dev/run_local_autonomy_readiness_audit.sh` scans the
  conventional downloaded desktop artifact folders, writes the same strict
  autonomy-readiness report locally, and fails closed while preserving a report
  that explains which proof artifacts are missing.
- Done: Module Setup exposes an `Autonomy Readiness` SSH action after the bench
  report step, so operators can run the strict final audit from the desktop app
  and download the JSON report to `~/DroneTransfer/from-pi/replay-cases/`.
- Done: Module Setup lists downloaded autonomy-readiness JSON reports from
  `~/DroneTransfer/from-pi/replay-cases/` with pass/degraded/fail counts and
  the support-bundle, PX4 receiver, field-evidence, feature-benchmark, and
  threshold-tuning gate statuses.

Tasks:

1. Fill `data/replay_cases/` with real field logs that pass
   `vision-nav-field-evidence-gate` for good texture, low texture, blur,
   seasonal change, lighting change, altitude/scale change, repeated patterns,
   and wrong map. Use `scripts/pi/register_field_replay_case.sh` on the Pi, or
   `vision-nav-register-replay-case` when bringing copied logs into the repo
   dataset folder.
2. Run `vision-nav-benchmark-feature-methods` on real field logs and use the
   generated reports to choose the Pi default and higher-compute fallback.
3. Tune replay-gate thresholds against real field logs for blur, seasonal
   change, altitude/scale change, repeated patterns, and wrong-map cases, then
   save the generated `threshold_tuning_report.json` artifact.
4. Add native replay artifact views for full extracted support-bundle logs and
   frame timelines after real field datasets exist.
5. Run `vision-nav-autonomy-readiness` against the final support bundle, field
   evidence report, and threshold-tuning report before calling the autonomy and
   ground-control implementation goal complete.

Acceptance checks:

- Local smoke tests cover accepted, degraded, and rejected localization cases
  through the synthetic replay manifest. Real field cases remain required before
  threshold tuning is considered complete.
- Support bundles are enough to reproduce a failed bench run offline.
- The autonomy-readiness audit passes only when bench evidence, real field
  evidence, feature-method benchmark evidence, and threshold tuning are all
  present and passing.

### Track 6: ArduPilot Adapter Path

Goal: keep ArduPilot compatibility in view without distracting from the PX4
bench prototype.

Status:

- Done: [ArduPilot ExternalNav Adapter Design](ardupilot-externalnav-adapter.md)
  documents the later adapter contract, preferred `ODOMETRY` path, conservative
  ExternalNav parameter shape, bench sequence, and explicit non-goals.
- Done: `vision-nav-check-ardupilot-params` and
  `scripts/pi/check_ardupilot_params.sh` audit exported ArduPilot parameters for
  ExternalNav bench readiness without modifying the flight controller.
- Done: support bundles can include ArduPilot parameter exports and parsed
  ExternalNav readiness reports under `extras/ardupilot_params/` and
  `summaries/ardupilot_params/`, and the desktop support-bundle detail view can
  display them.
- Done: `vision-nav-bench-readiness` includes ArduPilot parameter status in the
  combined pass/degrade/fail bench artifact when an ArduPilot report is
  bundled; use `--require-ardupilot-params` only for adapter-specific bench
  runs.

Tasks:

1. Wait for repeatable PX4 SITL/bench receiver evidence before enabling an
   ArduPilot runtime send profile.
2. Run ArduPilot SITL with `ODOMETRY` input and save receiver/EKF source-state
   evidence.
3. Add ArduPilot receiver-evidence parsing only after the SITL evidence format
   is known.

Acceptance checks:

- ArduPilot support never becomes the default output path.
- Parameter checks can be run from an exported Mission Planner/MAVProxy file.
- Runtime adapter work remains blocked behind PX4 bench evidence and ArduPilot
  SITL receiver proof.

## Execution Order

1. External-position conversion and MAVLink payloads.
2. PX4 external-vision guidance and SITL smoke path.
3. Desktop setup wizard and runtime health display.
4. COG/STAC/GeoTIFF bundle validation and health report.
5. ROS 2 package wrapper and replay.
6. Hierarchical tile retrieval and map-quality heatmap.
7. ArduPilot adapter after PX4 bench validation. The design and parameter
   readiness checker now exist; runtime output remains intentionally gated.

The first execution item is now represented in code by
`src/vision_nav/external_position.py` and the updated MAVLink bridge tests.
