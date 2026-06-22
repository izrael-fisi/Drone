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
- Done: Pi runtime wrappers and the MAVLink log sender default to the preferred
  `ODOMETRY` path while keeping `VISION_POSITION_ESTIMATE` as an explicit
  compatibility override.
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
- Done: the final readiness audit accepts standalone PX4 receiver-evidence
  reports through `--px4-sitl-report` / `VISION_NAV_PX4_SITL_REPORT`, so
  already evaluated receiver proof can be reused without rebuilding the support
  bundle.
- Done: Module Setup lists downloaded standalone PX4 receiver-evidence JSON
  reports from `~/DroneTransfer/from-pi/px4-sitl-evidence/` with sample counts,
  latest sample age, MAVLink version, and report issues.
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
- Done: PX4 SITL receiver evidence now computes observed receive rate from
  `vehicle_visual_odometry` listener timestamps, compares it with the smoke
  session `rate_hz`, and surfaces the rate in desktop receiver/support-bundle
  views, bench-readiness details, and final autonomy-readiness details so proof
  covers timing as well as sample presence.
- Done: bench-readiness and final autonomy-readiness now require PX4 receiver
  evidence to prove the MAVLink `ODOMETRY` path. `VISION_POSITION_ESTIMATE`
  captures can still be reviewed as compatibility debug evidence, but they do
  not satisfy the preferred PX4 product-interface gate.

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
- PX4 bench/final readiness fails if receiver evidence only covers the
  compatibility `VISION_POSITION_ESTIMATE` path.
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
- Done: repo-local launch files under `ros2/launch/` start live terrain
  runtime publishing or replay publishing with repeatable arguments.
- Done: `terrain_nav_live.launch.py` exposes optional PX4 SITL/direct MAVLink
  arguments including endpoint, `ODOMETRY` message selection, source IDs, and
  external-position health thresholds, while keeping ROS-only launch behavior
  as the default when no endpoint is set.
- Done: Pi diagnostics and Module Setup now include an optional Micro
  XRCE-DDS Agent check for PX4 uXRCE-DDS/ROS 2 bridge readiness.
- In progress: `vision-nav-ros2-replay-log --export-rosbag-jsonl` writes a
  dependency-free topic replay artifact with ROS message types, topics,
  timestamps, and payloads for offline field-log review before native rosbag2 is
  required.
- In progress: the same JSONL export can include bounded
  `sensor_msgs/msg/CompressedImage` camera-frame topic records from runtime
  `frame_path` entries, with relative paths resolved from the log directory.
- Done: `vision-nav-validate-rosbag-export` validates JSONL, MCAP, and native
  rosbag2 export metadata, sidecars, topic counts, message counts, and storage
  presence without requiring ROS 2.
- Done: ROS replay export validation now fails closed unless the export includes
  non-empty `/vision_nav/odometry` and `/diagnostics` topics, matching the final
  autonomy-readiness proof requirement instead of only checking file structure.
- Done: `scripts/pi/run_rosbag_export_validation.sh` wraps the default Pi
  terrain log export and validation into one command, writes the stable
  `rosbag-jsonl-validation.json` readiness artifact, and emits
  `__VISION_NAV_ROSBAG_EXPORT_VALIDATION__=...` for support/download workflows.
- Done: support bundles ingest ROS bag export validation reports under
  `extras/rosbag_export_validations/`, publish parsed summaries under
  `summaries/rosbag_export_validations/`, and surface their status in desktop
  support-bundle diagnostics and bench-readiness checks when provided.
- Done: `vision-nav-ros2-replay-log --export-mcap` writes an optional
  JSON-encoded MCAP archive with odometry, diagnostics, and bounded compressed
  camera-frame topics when the `mcap` Python package is installed.
- Done: `vision-nav-ros2-replay-log --export-rosbag2` writes a native rosbag2
  directory with serialized ROS messages when run in a sourced ROS 2 Python
  environment with `rosbag2_py` and message packages available.
- Done: `vision-nav-review-rosbag2-cli` creates a native rosbag2 workstation
  review artifact by combining strict export validation with captured
  `ros2 bag info` output, degrading cleanly when the ROS 2 CLI is unavailable.
- Done: `scripts/dev/run_rosbag2_cli_review.sh` wraps the sourced workstation
  native export plus `ros2 bag info` review into one fail-closed operator
  command, with a dry-run mode covered by local preflight.
- Done: support bundles can ingest native rosbag2 CLI review artifacts under
  `extras/rosbag2_cli_reviews/`, publish parsed summaries under
  `summaries/rosbag2_cli_reviews/`, and include the optional review in bench
  readiness when provided.
- Done: final autonomy-readiness audits accept standalone native rosbag2 CLI
  review reports or support-bundle summaries as a strict proof item, failing
  closed until `ros2 bag info` succeeds against the native export.
- Done: `ros2/drone_vision_nav/` provides a thin `ament_python` package wrapper
  with package metadata, installed launch profiles, and `terrain_nav_live` /
  `terrain_nav_replay` console scripts for colcon-based ROS 2 workstations.

Tasks:

1. Run `scripts/dev/run_rosbag2_cli_review.sh` on a sourced ROS 2 workstation
   against real terrain logs, then save the resulting native export and review
   artifact.
2. Run the ROS 2 live launch profile with `mavlink_endpoint:=udp:14550` during
   PX4 SITL receiver verification and include the generated receiver artifacts
   in the final support bundle.

Acceptance checks:

- ROS 2 topics can replay a saved frame log on the desktop.
- ROS replay exports have a passed validation report with odometry and
  diagnostics topics, plus a passing native rosbag2 CLI review when native
  rosbag2 export is part of the evidence path, before they count toward final
  readiness.
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
- Done: `vision-nav-benchmark-retrieval` benchmarks both the lightweight
  grayscale global descriptor and optional precomputed neural retrieval
  descriptors on replay logs, reporting top-k recall, mean rank, and clean
  unavailable/degraded status when descriptor sidecars or query descriptors are
  absent.

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
- Done: Mission Planner now records GNSS-denied readiness actions in the mission
  bundle metadata, exports per-check status under `visionNavigation.gnss_denied`,
  renders the satellite/map/home/heading/estimator checklist, and blocks bundle
  build/upload until that GNSS-denied prep checklist is complete.
- Done: support bundles now parse the bundled Mission Planner JSON, summarize
  its GNSS-denied readiness block, and count `gnss_denied_plan` in bench
  readiness so stale or incomplete mission prep fails the bench report.
- Done: final autonomy-readiness reports expand failed `gnss_denied_plan`
  bench subchecks into a Mission Planner-specific next action, so operators know
  to complete GNSS-denied prep, rebuild/upload the bundle, and recreate the
  bench report.
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
- Done: the terrain runtime writes `runtime_status.json` beside
  `terrain_matches.jsonl` with active map, output path, estimator health,
  external-position health, latest frame, last match status/reason, and status
  counts; support bundles copy and summarize that snapshot for desktop review.
- Done: Module Setup can fetch the latest Pi-side `runtime_status.json` over
  SSH, download the snapshot, and show active map, last match, estimator health,
  external-position health, frame sequence, and accepted/rejected counts before
  the operator creates a full bench report.

Tasks:

1. Tune route splitting defaults after field replay data confirms the preferred
   segmentation rule.
2. Wire GNSS-denied reset actions to autopilot checklist validation after live
   PX4 receiver evidence is available.

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
- Done: support-bundle details now aggregate bundled runtime/replay JSONL logs
  into bounded frame timelines that show accepted-rate progression, dominant
  segment status, status counts, external-position health counts, sequence
  range, and confidence/inlier/reprojection averages without loading the full
  log into the WebView.
- In progress: support-bundle details now include bounded previews for small
  camera/debug/replay image artifacts inside downloaded ZIPs while skipping
  full map, orthophoto, tile, descriptor, and elevation assets.
- Done: support-bundle details now list safe extractable diagnostics and can
  extract full runtime logs, replay-gate reports, PX4 receiver reports,
  parameter reports, field-evidence reports, threshold-tuning reports,
  bench-readiness reports, and lightweight bundle metadata into a local
  `*-artifacts/` folder while rejecting full maps, tile pyramids, descriptors,
  elevation assets, GeoTIFFs, SQLite indexes, path traversal, and oversized
  entries.
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
- Done: bench-readiness now evaluates bundled `runtime_status.json` snapshots
  as evidence of active map, output/log path, estimator health, latest match
  status/reason, external-position health, and accepted/rejected counts. Missing
  snapshots degrade the gate; missing active-map or last-match state fails it.
- In progress: downloaded support-bundle details now show the embedded
  bench-readiness status and per-check messages in the desktop app.
- Done: Module Setup saved reports now include a bounded diagnostic snapshot
  for the newest downloaded support bundles, including bench-readiness checks,
  log summaries, frame timelines, extractable artifact inventories, image
  artifact metadata without base64 payloads, replay-gate reports, PX4/ArduPilot
  parameter reports, PX4 receiver evidence, field-evidence reports,
  feature-method benchmark reports, and threshold-tuning reports.
- Done: Module Setup saved reports now include a compact final
  autonomy-readiness snapshot with latest status, handoff path,
  evidence-package summary, workflow artifact references, goal-completion flag,
  external blockers, and next actions.
- Done: Module Setup saved reports now include a compact autonomy evidence
  workflow snapshot with the latest workflow status, artifact marker count,
  workflow-log archive path, validation report path, validation status, and
  bounded validation issues.
- Done: `data/replay_cases/` defines the replay case registry shape for good
  texture, degraded, and wrong-map datasets, including
  `replay_case_manifest.schema.json` plus schema checks in the standalone
  manifest evaluator, coverage audit, and support-bundle replay-gate packager.
- Done: `vision-nav-evaluate-replay-manifest --schema-only` validates replay
  manifest shape without requiring referenced logs to exist, which helps build
  field datasets incrementally while still failing malformed evidence manifests.
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
- Done: `vision-nav-create-field-evidence-template` writes a ready-to-fill
  field replay manifest covering every required autonomy condition from the
  same condition list used by the final readiness audit, so field datasets can
  be staged and schema-checked before logs are captured.
- Done: `vision-nav-create-field-collection-plan` renders the active field
  manifest into a JSON/Markdown operator checklist with per-condition status and
  exact Pi registration commands for each required real-world case.
- Done: `scripts/pi/create_field_evidence_template.sh` wraps the template
  generator on the Pi, writes the starter manifest into the transfer folder,
  and emits a stable marker for desktop/support collection.
- Done: `scripts/pi/create_field_collection_plan.sh` wraps the checklist
  generator on the Pi, writes `field_collection_plan.json` and
  `field_collection_plan.md` into the replay-cases transfer folder, and emits
  stable markers for workflow/support collection.
- Done: the Pi template wrapper can seed the active field manifest, and
  replay-case registration replaces matching template placeholders by condition
  tag when real field logs are collected.
- Done: Module Setup downloads both the field evidence starter template and the
  active seeded manifest, then lists downloaded template-shaped manifests after
  restart for offline review.
- Done: Module Setup can run `Create Plan` to generate and download the
  field-collection JSON/Markdown checklist, lists downloaded plans after
  restart, and includes collection-plan paths in saved setup reports.
- Done: downloaded field evidence template summaries now separate remaining
  placeholder conditions from conditions that already have registered real logs.
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
- Done: support bundles ingest field collection plans, copy the JSON and
  Markdown checklist under `extras/field_collection_plans/`, publish parsed
  plan JSON under `summaries/field_collection_plans/`, and preserve the
  intended field-coverage plan for support review.
- Done: downloaded support-bundle details show bundled field collection plan
  status, registered-vs-required counts, per-condition collection state, and
  extractable JSON/Markdown checklist artifacts.
- Done: autonomy readiness audits record field collection plan/checklist paths
  when present so `autonomy_readiness_report.evidence.zip` includes the
  operator plan beside the final handoff and machine-readable report.
- Done: downloaded autonomy-readiness report cards parse the referenced field
  collection plan when it is available locally, showing registered-vs-required
  counts and pending placeholder/missing collection conditions.
- Done: autonomy-readiness report cards fall back to the downloaded sibling
  `field_collection_plan.json` when a Pi-generated report still references the
  Pi-side absolute path.
- Done: local Markdown handoff rendering and evidence-package creation use the
  same downloaded sibling field-plan fallback, so support ZIPs can include the
  JSON/Markdown checklist after Pi reports are copied to the desktop.
- Done: pending field-collection conditions in Module Setup can copy individual
  or batched replay-case registration commands directly from readiness and
  collection-plan cards.
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
- Done: Module Setup lists downloaded standalone threshold-tuning JSON reports
  from `~/DroneTransfer/from-pi/replay-cases/` with coverage status, replay
  status, field-case count, and acceptance-rate margins.
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
  evidence, threshold-tuning proof, and ROS replay export validation proof. It
  intentionally fails until the external PX4, field-log, and replay-validation
  artifacts exist.
- Done: `scripts/dev/handoff_audit.sh` now verifies the Python entrypoints used
  by the autonomy-readiness path, including replay registration, field-evidence
  gates, feature/retrieval benchmarks, threshold tuning, PX4 receiver evidence,
  MAVLink replay send, ROS replay export, and evidence-workflow validation.
- Done: `scripts/dev/local_preflight.sh` uses an isolated per-run temporary
  workspace and preserves it only on failure, so local preflight and handoff
  audits can run concurrently without clobbering each other's evidence logs.
- Done: autonomy-readiness reports include a `plan_snapshot` with source-doc
  marker coverage, research reference/near-term item counts, implementation
  track counts, status-line counts, task counts, and acceptance-check counts so
  the final proof package records which version of the research/plan docs it
  evaluated.
- Done: autonomy-readiness reports include machine-readable `next_actions` for
  failed or degraded proof gates, with the relevant Module Setup action and
  shell command to collect the missing artifact.
- Done: ROS replay validation next actions now point to Module Setup's
  standalone `ROS Bag Validation` action and the
  `scripts/pi/run_rosbag_export_validation.sh` wrapper instead of asking
  operators to hand-run the lower-level export and validator commands.
- Done: Module Setup can copy all shell commands from an autonomy-readiness
  report's next actions in one click, while preserving per-action command copy.
- Done: autonomy-readiness JSON reports include a machine-readable
  `command_bundle` with next-action commands and pending field replay
  registration commands for downstream support tooling, and Module Setup
  consumes that bundle for bulk command copy plus saved setup-report exports.
- Done: field-evidence readiness next actions now point operators to
  `Create Template, then Register`, so missing real-world replay evidence starts
  with the eight-condition starter manifest instead of ad hoc case entry.
- Done: autonomy-readiness reports now preserve failed/degraded
  bench-readiness subchecks and expand them into specific next actions, so a
  missing `runtime_status.json` points to Module Setup > Runtime Status instead
  of only saying to recreate the support bundle.
- Done: Module Setup renders those autonomy-readiness bench subchecks in the
  downloaded report card, including subcheck name, status, and message for
  support-bundle failures such as missing runtime status or PX4 receiver proof.
- Done: autonomy-readiness reports now include a strict
  `evidence_manifest` section with completion blockers, external proof
  blockers, missing field conditions, and failed/degraded bench subchecks so the
  final goal cannot be marked complete from partial evidence.
- Done: autonomy-readiness reports now include a dependency-aware
  `proof_runbook` that orders source-plan, bench, field dataset,
  method/threshold, ROS replay, and final-audit phases, then marks each phase as
  passed, action-required, or blocked by upstream proof.
- Done: Module Setup renders the readiness `evidence_manifest` as a compact
  goal-completion proof summary, including external blocker count and the first
  missing PX4, field, feature-benchmark, threshold, ROS replay validation, or
  support-bundle evidence items.
- Done: field-evidence and threshold-tuning next actions carry the missing
  required condition keys so operators can see which real-world cases still
  need to be collected.
- Done: the final readiness audit accepts standalone PX4 receiver,
  field-evidence, feature-method benchmark, threshold-tuning, ROS replay export
  validation, and native rosbag2 CLI review JSON reports in addition to
  support-bundle summaries, so downloaded evidence can be re-audited without
  repackaging the support bundle.
- Done: the Pi and local autonomy-readiness wrappers pass
  `rosbag-jsonl-validation.json` and `rosbag2-cli-review.json` to the final
  audit when they exist and emit stable markers for support handoff notes.
- Done: Module Setup parses `__VISION_NAV_ROSBAG_EXPORT_VALIDATION__=...`,
  downloads the validation JSON into the terrain-match transfer folder, lists
  downloaded ROS bag validation reports after restart, and shows the ROS bag
  gate in autonomy-readiness report cards and saved setup reports.
- Done: Module Setup parses `__VISION_NAV_ROSBAG2_CLI_REVIEW__=...`, downloads
  the native workstation review artifact into the terrain-match transfer folder,
  and shows the rosbag2 proof gate in autonomy-readiness report cards.
- Done: Module Setup exposes a standalone `ROS Bag Validation` action that runs
  `scripts/pi/run_rosbag_export_validation.sh`, downloads the emitted validation
  report, and refreshes the ROS Bag Validation evidence list without requiring
  the full evidence workflow.
- Done: `scripts/pi/run_autonomy_readiness_audit.sh` runs the same final audit
  on the Pi against the latest support bundle and writes
  `autonomy_readiness_report.json` for transfer or support review.
- Done: `vision-nav-autonomy-handoff` renders
  `autonomy_readiness_report.json` into a Markdown handoff with status, inputs,
  plan source snapshot, checks, all goal proof items, completion blockers,
  external proof blockers, missing field conditions, bench subchecks, and next
  actions.
- Done: the Markdown handoff includes a copy-friendly command bundle for
  next-action shell commands and pending field replay registration commands.
- Done: the Markdown handoff renders the proof runbook so support can follow
  the correct proof collection order from the generated handoff instead of
  opening the raw JSON report.
- Done: `vision-nav-autonomy-evidence-package` creates a support-review ZIP
  containing the strict readiness JSON, Markdown handoff, package manifest, and
  small referenced evidence artifacts that exist locally while listing missing
  or oversized artifacts in the manifest. The package manifest also carries a
  plan source snapshot plus a bounded goal-proof summary and proof-runbook
  summary with proof pass counts, first proof items, completion-blocker count,
  external-blocker count, and ordered phase state.
- Done: the Pi and local autonomy-readiness wrappers emit
  `__VISION_NAV_PX4_SITL_REPORT__=...` when direct receiver proof is available,
  letting Module Setup download the receiver report beside the final audit.
- Done: `scripts/dev/run_local_autonomy_readiness_audit.sh` scans the
  conventional downloaded desktop artifact folders, writes the same strict
  autonomy-readiness report locally, includes the latest downloaded
  feature-method benchmark report, renders a Markdown handoff beside the JSON
  report, and fails closed while preserving artifacts that explain which proof
  items are missing.
- Done: Module Setup exposes `Local Readiness Re-Audit` as a local-only action
  that runs the desktop wrapper against already downloaded `from-pi` evidence,
  then refreshes final readiness, workflow, field, feature, threshold, ROS bag,
  PX4, and support-bundle report lists without requiring SSH.
- Done: Module Setup exposes an `Autonomy Readiness` SSH action after the bench
  report step, so operators can run the strict final audit from the desktop app
  and download the JSON report to `~/DroneTransfer/from-pi/replay-cases/`.
- Done: Module Setup also parses `__VISION_NAV_AUTONOMY_HANDOFF__=...`,
  downloads the generated Markdown handoff beside the JSON report, and shows the
  local path in the Latest Output panel for support review.
- Done: Module Setup parses `__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__=...`,
  downloads the generated evidence ZIP beside the JSON report, includes it in
  saved setup reports, and lists/reveals the sibling package from the Autonomy
  Readiness Reports card after restart, including package manifest counts for
  included, missing, and skipped evidence artifacts, packaged proof pass counts,
  external-blocker counts, and bounded included/missing/skipped artifact labels
  for support triage.
- Done: the Autonomy Readiness SSH action also parses emitted evidence-workflow,
  workflow-log, workflow-validation, field-evidence, feature-benchmark,
  threshold-tuning, and field-collection markers from the readiness wrapper,
  downloads those referenced artifacts, and refreshes the matching report lists
  without requiring the separate Evidence Workflow action first.
- Done: `scripts/pi/run_autonomy_evidence_workflow.sh` attempts the ordered
  field-template, field-collection checklist, optional field-case registration,
  feature-benchmark, threshold-tuning, ROS bag JSONL export validation,
  support-bundle, and final-readiness sequence while writing a
  machine-readable per-step workflow report with logs and emitted artifact
  markers even when the final gates still fail.
- Done: the evidence workflow uses the standalone
  `scripts/pi/run_rosbag_export_validation.sh` wrapper for its ROS bag
  validation step, so direct operator runs and full workflow runs generate the
  same marker/report shape.
- Done: the evidence workflow writes a compressed workflow-log archive and
  emits `__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__=...`, so full per-step logs can
  be downloaded with the workflow report instead of relying only on bounded
  tails. The archive stores the step outputs under `logs/*.log` for support
  review.
- Done: `vision-nav-validate-evidence-workflow` validates downloaded workflow
  reports offline, confirms required step records, verifies the log archive is
  readable, and checks that every recorded step has a matching `logs/*.log`
  member. The Pi evidence workflow now writes this validation JSON beside the
  workflow report and emits `__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__=...`.
  The validator exits nonzero only for structurally failed report/archive
  validation, while degraded-but-usable workflows can still pass script checks
  when readiness proof is merely incomplete.
- Done: final autonomy-readiness audits now record discovered evidence-workflow
  JSON, workflow-validation JSON, and workflow-log archive paths as non-gating
  inputs. Handoffs show their artifact availability, and evidence ZIPs include
  the files when present and under the artifact size limit.
- Done: downloaded autonomy-readiness report cards now surface those workflow,
  validation, and log archive inputs directly with copy/reveal controls when
  the local downloaded artifacts are available.
- Done: downloaded autonomy-readiness report cards now preserve and render the
  final audit `proof_items` checklist, so operators can see every research-plan
  proof gate's current status instead of only the failed blockers.
- Done: downloaded autonomy-readiness report cards now parse and render the
  `plan_snapshot`, showing research marker/reference coverage and implementation
  track/task/done counts directly in the desktop app.
- Done: saved Module Setup reports now include bounded final-audit proof items,
  proof pass counts, plan snapshots, completion blockers, and external blockers
  so exported support archives preserve both passing and missing research-plan
  evidence.
- Done: Module Setup exposes that wrapper as `Evidence Workflow`, downloads the
  workflow JSON, workflow-log archive, validation JSON, and any support bundle,
  field-evidence report, feature-method benchmark, threshold-tuning report,
  field-collection plan, final readiness, handoff, evidence-package, or PX4
  receiver markers emitted during the sequence.
- Done: Module Setup lists downloaded autonomy evidence workflow JSON reports
  after app restart with pass/fail/skip counts, per-step status, and emitted
  artifact markers, including workflow logs, validation report, support bundle,
  field-evidence, feature-method, threshold-tuning, final readiness, handoff,
  evidence-package, field-plan, ROS bag validation, and PX4 receiver outputs.
  Each marker can be copied individually or as one artifact-path bundle from
  the workflow report card for support notes, preferring the downloaded local
  artifact path when the app can resolve it. When the downloaded validation JSON
  is present, the card also shows validation status, workflow status, issue
  count, and the first validation issue.
- Done: Module Setup detects sibling Markdown handoffs beside downloaded
  autonomy-readiness JSON reports after app restart and exposes copy/reveal
  controls in the Autonomy Readiness Reports list.
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
4. Tune frame-timeline markers after real field datasets exist if additional
   field-specific events need to be highlighted beyond accepted/rejected,
   confidence, inliers, reprojection error, and external-position health.
5. Run `vision-nav-autonomy-readiness` against the final support bundle, PX4
   receiver-evidence report, field evidence report, feature-method benchmark
   report, threshold-tuning report, and ROS replay export validation report
   before calling the autonomy and ground-control implementation goal complete.

Acceptance checks:

- Local smoke tests cover accepted, degraded, and rejected localization cases
  through the synthetic replay manifest. Real field cases remain required before
  threshold tuning is considered complete.
- Support bundles are enough to reproduce a failed bench run offline.
- The autonomy-readiness audit passes only when bench evidence, real field
  evidence, feature-method benchmark evidence, threshold tuning, and ROS replay
  export validation are all present and passing.

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
