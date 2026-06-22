# Desktop App

The desktop app in `desktop-app/` is a Tauri + React operator tool adapted from
the Macula desktop workflow for this Drone GNSS-denied navigation repo.

It is used for:

- guided module setup over local Wi-Fi/SSH from the Devices page
- selecting or importing satellite map regions
- importing your own map/image files
- building and uploading a Drone `mission_bundle`
- configuring the low-compute classical feature path or high-compute neural path
- running module validation, camera view, and short runtime checks over SSH
- enabling MAVLink output for accepted vision measurements when ready

## Module Setup

The Devices page contains the customer-facing `Module setup` flow for a
Raspberry Pi runtime computer on the same Wi-Fi network as the desktop app.

The flow is:

1. Connect the desktop computer and Raspberry Pi to the same Wi-Fi network.
2. Open Devices and use `Local Wi-Fi Discovery` to scan saved hostnames,
   Raspberry Pi mDNS names, and local SSH neighbors.
3. Add or select the discovered module, then expand it and choose
   `Module setup`.
4. Add or confirm the module hostname or IP, username, and SSH authentication.
5. Run `Test Wi-Fi SSH` and save the module as the active device.
6. Open the expanded device menu to adjust runtime paths, MAVLink, and flight
   controller settings only after connection exists.
7. Run `Install Module` to sync runtime files and execute the module bootstrap.
8. Run setup and vision checks from the app:
   - Wi-Fi SSH identity
   - project file check
   - module dependency bootstrap
   - system verification
   - camera view test
   - camera health
   - time sync
   - MAVLink endpoint access
   - optional Micro XRCE-DDS Agent readiness for PX4 ROS 2 paths
   - calibration image capture
   - synthetic vision smoke test
   - deployed runtime bundle validation
   - field evidence template creation for required real-world replay cases
   - field replay-case registration for terrain evidence gates
   - bench-report support bundle creation and desktop download
9. Save a local setup report from the collected checks when you need an audit
   trail for a bench run or customer install.

The project sync command intentionally excludes desktop-only and generated
folders such as `.git`, `desktop-app`, `node_modules`, `target`, `data`, `logs`,
and `map_bundles`. Bootstrap uses the existing `scripts/pi/bootstrap_pi5.sh`
script on the module and may require a reboot afterward.

The setup report is exported as JSON and excludes SSH passwords, key
passphrases, and sudo passwords. It includes device connection metadata,
runtime paths, step status, command output, camera-preview path, and the most
recent downloaded support-bundle summaries. It also includes a bounded
diagnostic snapshot for the newest downloaded support bundles: bench-readiness
checks, log summaries, frame timelines, extractable artifact inventories,
image-artifact metadata without base64 payloads, replay-gate reports, PX4 and
ArduPilot parameter reports, PX4 receiver evidence, field-evidence reports,
feature-method benchmark reports, and threshold-tuning reports. Discovery
results are saved in the
desktop app so recent local-network candidates remain visible even after a Pi
reboots or temporarily drops offline. Discovery also shows active desktop
IPv4 interface/subnet hints, lets the operator select the adapter that should
be on the Pi network, and provides a copyable mDNS/SSH/firewall checklist. The
selected adapter and checklist are included in the setup report, which helps
diagnose whether the Pi and desktop are on the same local network after a
failed bench install.
When autonomy-readiness reports are available, the setup report also includes a
compact final-audit snapshot with the latest status, handoff path, evidence
package path, goal-completion flag, plan source snapshot, proof-item
passed/total counts, bounded proof items, completion blockers, external blocker
details, the first next actions, the referenced workflow report/validation/log
archive paths, and the referenced field collection plan summary when it is
available locally.
When autonomy evidence workflow reports are available, the setup report also
includes the latest workflow status, pass/fail/skip summary, marker count,
workflow-log archive path, validation report path, validation status, issue
count, bounded validation issues, and validation-check details such as missing
final-proof artifact markers. The validation summary also preserves the next
required workflow step and command hint, so exported setup reports show where a
partial evidence run should resume.
The readiness wrappers also create
`autonomy_readiness_report.evidence.zip`, a support-review package with the JSON
report, Markdown handoff, package manifest, a plan source snapshot, a bounded
goal-proof summary, and any small referenced evidence artifacts available on the
local machine.

## Vision Pipeline

The default mode is `classical`.

```text
satellite region
  -> mission_bundle/ortho/map.png
  -> ORB or AKAZE feature index
  -> Raspberry Pi 5 runtime
```

The optional `neural` mode keeps SuperPoint + LightGlue metadata and region files
inside the bundle for higher-compute devices. The Raspberry Pi-safe classical
feature index is still built as a fallback.

The Vision Pipeline page stores the default pipeline, feature method, feature
count, match thresholds, and neural weight paths used by new mission bundle
builds. Devices and Mission Planner show or consume these values, but the Vision
Pipeline page is the only editable configuration surface for matching defaults.

## Map Sources

The Maps page can create runtime map sources in three ways:

- draw an area and download satellite tiles
- import an existing folder containing `satellite.png` and `metadata.json`
- upload a map/image file and convert it into the same normalized folder shape

Uploaded map files are converted to:

```text
uploaded_map_source/
  satellite.png
  metadata.json
```

Supported upload formats include PNG, JPEG/JPG, TIFF/GeoTIFF image files, BMP,
WebP, and GIF. GeoTIFF uploads automatically read standard embedded
georeferencing when the source CRS is one of:

- EPSG:4326 or another geographic lon/lat CRS stored in GeoTIFF keys
- EPSG:3857 Web Mercator
- WGS84 UTM, EPSG:32601 through EPSG:32660 or EPSG:32701 through EPSG:32760

For those GeoTIFFs, the app derives the runtime origin latitude/longitude,
origin pixel, GSD, rotation, CRS label, and georeference confidence. Manual
origin/GSD fields remain available and override embedded metadata. Non-GeoTIFF
images still need manual origin latitude, longitude, and GSD.

The normalized `metadata.json` includes `georef_source`,
`georef_confidence`, and `georef_crs`. These fields are copied into the mission
bundle so the Pi runtime can combine map georeference quality with visual match
quality when it estimates measurement covariance.

Terrain bundles also declare optional barometer support. The app does not
require that telemetry, but the runtime can use PX4 MAVLink altitude/pressure
messages to fill relative vertical fields and vertical covariance.

## Local Setup

```bash
cd desktop-app
npm ci
npm run build
```

To run or package the Tauri shell, install Rust/Cargo first:

```bash
cd desktop-app
npm run tauri dev
```

The bundle builder command uses the local Drone repo path selected in the app.
If needed, set `DRONE_DESKTOP_PYTHON` to the Python interpreter that has this
repo's dependencies installed.

## Mission Planner

The Mission Planner tab is the ground-control style workspace. The user selects
a flight area/map source and the interactive planner map displays that saved
source's local `satellite.png` mosaic.

The planner is organized into four operator layers:

- `Mission`: takeoff, waypoint, and land items.
- `GeoFence`: an optional polygon safety boundary.
- `Rally`: optional emergency rally points.
- `Vision Map`: localization checkpoints used to reason about GNSS-denied
  feature-map coverage.

Mission Planner opens without auto-selecting a saved map source, so large local
mosaics do not block the first tab render. The saved `satellite.png` mosaic is
loaded only after the user selects a map source. The stats panel reports mission
item count, distance, estimated time, map area, and readiness checks for map
quality, mission path, fence shape, and MAVLink endpoint.

The plan editor also tracks mission state during the app session. It marks the
plan as invalid when required inputs are missing, not built before a bundle has
been created, stale when the map/mission/output settings change after a build,
not uploaded when the current bundle exists only locally, and uploaded when the
current plan fingerprint has been sent to the active Raspberry Pi. Local-only
devices show a bundle-ready state instead of upload status. Build/upload
fingerprints and timestamps are saved locally, so the Mission Planner can show
the previous bundle state after the app restarts.

Mission plans can be imported from the app's JSON format or QGroundControl-style
`.plan` files. Export writes a `.plan` file with QGC mission, geofence, rally,
and `visionNavigation` metadata for this project. The Mission state panel also
shows whether the active imported/exported plan file has unsaved local changes.

Mission Planner also includes a GNSS-denied readiness block. The operator can
record that satellite-source assumptions are disabled, set map-position and
home resets from the selected mission item, set or derive heading, and mark
estimator health. The planner shows each subcheck and treats the combined
GNSS-denied prep state as a bundle-readiness gate, so build/upload stays
disabled until satellite, map reset, home reset, heading, and estimator status
are complete. Those values and per-check statuses are exported in the app
mission JSON and in the QGroundControl `.plan` file under
`visionNavigation.gnss_denied`.

Support bundles parse that exported mission JSON and include a
`gnss_denied_plan` bench-readiness check. The support-bundle list shows this as
`gnss prep`, so the operator can confirm the uploaded plan still carries the
GNSS-denied prep state before relying on PX4 receiver or field evidence.

Mission Planner also records terrain planning constraints before bundle build.
The operator can confirm the offline map-cache path, set minimum AGL, maximum
terrain relief, minimum AGL-to-GSD ratio, and maximum route-segment length. The
same metadata is exported in the app mission JSON and in the QGroundControl
`.plan` file under `visionNavigation.terrain_planning`. The planner also
generates deterministic route-segment records with split coordinates,
cumulative distance, longest segment length, and split reason so long
terrain-aware routes can be reviewed in the bundle without changing the
underlying flight-controller mission items. After a bundle build, the app
compares terrain limits with `bundle_health.json` terrain-profile values.

The mission bundle action builds the selected map source, writes the desktop
mission JSON to `mission/mission_plan.json`, writes the QGC-style file to
`mission/qgc.plan`, records both in `manifest.json`, and uploads the bundle to
the runtime compute module. Feature extraction settings are read from the saved
Vision Pipeline defaults. It also builds the terrain tile index, STAC-style
manifest, `bundle_health.json`, and terrain runtime config. The Mission Planner
bundle result shows map health, tile count, feature count, and GSD before the
operator validates or runs the bundle on the Pi. It also shows a coarse Pi
runtime-cost estimate from tile count and feature density, plus checksum status,
covered file count, map source provenance, georeference source, CRS, and
georeference confidence. A compact map-quality heatmap previews feature density
per tile so low-texture areas are visible before the Pi uses the bundle. If
optional DEM/DSM elevation rasters are present in the selected bundle, the
result also shows whether elevation sanity checks are ready. When the bundle
contains a mission plan and sampleable DEM/DSM raster, it shows terrain-profile
status, estimated minimum AGL, terrain relief, and a compact terrain/flight
profile preview. By default this overwrites the active bundle at:

```text
/home/<pi-user>/drone-data/map_bundles/mission_bundle
```

That path is what `./scripts/pi/run_terrain_nav_loop.sh` loads through
`VISION_NAV_BUNDLE`, so the map selected in the desktop app becomes the active
map used for feature comparison on the Raspberry Pi.

The Maps page can attach optional DEM and DSM GeoTIFFs to a saved map source.
Those files are copied into the map folder under `elevation/`, referenced from
`metadata.json`, and carried into the next terrain mission bundle.
When GDAL Python bindings are available on the machine building the bundle, the
same health report also includes stricter TIFF/GeoTIFF driver, projection,
geotransform, overview, block-layout, and COG-readiness checks.

It then runs the existing Pi scripts:

```bash
./scripts/pi/validate_terrain_bundle.sh
./scripts/pi/run_terrain_nav_loop.sh
```

Module Setup can fetch the latest Pi-side runtime snapshot with
`./scripts/pi/read_runtime_status.sh`. The Runtime Status card shows the active
map, last match status/reason, confidence, estimator health, external-position
health, frame sequence, and accepted/rejected counts, then downloads the raw
`runtime_status.json` to `~/DroneTransfer/from-pi/runtime-status/` for the
setup report.
Module Setup can also run `Field Log Capture`, a bounded 30-frame
`scripts/pi/run_terrain_nav_loop.sh` pass against the selected mission bundle.
It uses the configured MAVLink endpoint when present, downloads
`terrain_matches.jsonl` to `~/DroneTransfer/from-pi/terrain-match/`, and
downloads the companion `runtime_status.json` for support review. That synced
log can feed Field Evidence registration, ROS Bag Validation, Native rosbag2
Review, feature-method benchmarking, and threshold tuning. Evidence workflow
checks parse existing synced logs before accepting them: the JSONL must be
nonempty and include accepted, rejected, or degraded match statuses. If the log
is valid but `runtime_status.json` is missing, the capture evidence is reported
as degraded until the runtime snapshot is refreshed.

The Runtime And MAVLink panel can also create a support bundle on the connected
Raspberry Pi. Support bundles are written under
`~/DroneTransfer/outgoing/support-bundles/` on the Pi, then downloaded to
`~/DroneTransfer/from-pi/support-bundles/` on the desktop. They include active
map metadata, bundle health, runtime logs, generated summaries, app/git state,
the configured MAVLink endpoint, optional replay-gate reports, optional PX4
SITL receiver evidence and parameter checks, optional ArduPilot parameter
checks, optional feature-method benchmarks, optional field-evidence gates, and
optional threshold-tuning reports, and an automatic bench-readiness summary. The
panel lists recent downloaded support bundle ZIPs with parsed bench-readiness
status, bundle health, checksum status, map source provenance, georeference
confidence, replay-gate status, GNSS-denied mission-prep status, PX4 evidence
status, PX4 parameter status, ArduPilot parameter status, feature-method
benchmark status, field-evidence status, and threshold-tuning status so the
operator can confirm what was captured without manually opening the archive.
Feature-method benchmark reports
from `$HOME/DroneTransfer/outgoing/feature-method-bench`, field-evidence reports
from `$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json`, and
threshold-tuning reports from
`$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json` are also
packaged automatically when present. The list can
reveal a ZIP in the local file manager, copy the full path for support notes,
show a compact detail view, extract safe diagnostic artifacts, or delete stale
ZIP files after a bench session. The detail view reads the ZIP archive directly
and shows support metadata,
git/app state, log status counts, accepted-rate summaries, bench-readiness
checks, replay-gate case results, PX4 receiver sample counts, MAVLink
version/link hints, PX4 external-vision parameter readiness, ArduPilot
ExternalNav parameter readiness, feature-method benchmark recommendations,
field-evidence case coverage, per-condition coverage status, threshold-tuning
margins, compact per-record previews from bundled runtime/replay JSONL logs, and
bounded frame timelines that show accepted-rate progression, dominant segment
status, external-position health counts, sequence range, and average
confidence/inlier/reprojection metrics across the log. When a terrain runtime
`runtime_status.json` snapshot is present beside the log, support bundles copy
it as `logs/<log-name>.runtime_status.json` and summarize the active map, output
path, estimator health, external-position state, and last accepted/rejected
reason in `support_manifest.json`. Bench readiness also evaluates that snapshot:
missing runtime status degrades the report, while missing active-map or
last-match state fails the runtime-status check. It also
previews a bounded set of small image artifacts from camera, debug, replay,
smoke, or extra-file paths while skipping full map, orthophoto, and tile
assets. Full artifacts such as runtime logs, replay-gate reports, PX4 receiver
reports, parameter reports, field-evidence reports, threshold-tuning reports,
bench-readiness reports, and lightweight bundle metadata can be extracted into a
`*-artifacts/` folder next to the downloaded ZIP and revealed in the file
manager. Full maps, orthophotos, tile pyramids, descriptors, elevation assets,
GeoTIFFs, SQLite indexes, and oversized files are intentionally excluded from
the extraction action.

Desktop-created support bundles automatically pass conventional Pi evidence
locations into `scripts/pi/create_support_bundle.sh`:
`$HOME/px4-sitl-evidence`, `$HOME/px4.params`, `$HOME/ardupilot.params`,
`$HOME/DroneTransfer/outgoing/feature-method-bench`, and
`$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json`.
Missing files are ignored by the Pi wrapper; present files are packaged and
counted in the bench-readiness report.

Module Setup uses the same support-bundle path for its `Bench Report` action,
after validating the deployed terrain bundle at the configured runtime bundle
path.

Module Setup can create a ready-to-fill field evidence template before the
first field run. The app runs `scripts/pi/create_field_evidence_template.sh`
over SSH, writes the starter manifest under the Pi replay-cases transfer
folder, downloads it to `~/DroneTransfer/from-pi/replay-cases/`, and shows the
local path in Latest Output. The template contains one placeholder
`dataset_type=field` case for each required autonomy-readiness condition. The
Field Evidence Templates list indexes downloaded starter manifests after app
restart with site name, case count, placeholder count, and required condition
tags. On the Pi, the same action seeds the active `field_manifest.json` if it
does not already exist; later field-case registration replaces matching
template placeholders by condition tag. Module Setup downloads both the starter
template and the active manifest when the Pi wrapper emits
`__VISION_NAV_FIELD_TEMPLATE__=...` and `__VISION_NAV_FIELD_MANIFEST__=...`.
The template list separates remaining placeholder conditions from conditions
that already have registered logs.
The `Create Plan` action runs `scripts/pi/create_field_collection_plan.sh` and
turns that active manifest into a JSON and Markdown field checklist with one
registration command per required condition. Module Setup downloads both files,
shows their paths in Latest Output, and indexes downloaded plans after restart
in the Field Collection Plans list. The Evidence Workflow wrapper also runs the
checklist step, downloads the emitted plan artifacts, and records the emitted
plan markers in the workflow report. Support bundles copy the JSON checklist
and sibling Markdown file when present, and the final autonomy evidence package
includes the plan/checklist paths recorded by the readiness audit.
In the Field Collection Plans list, each pending condition has a `Load` action
that fills the Field Evidence Case form with the plan case name, condition,
expected behavior, notes, site, and any non-placeholder capture metadata. This
keeps the generated checklist and the registration form aligned while still
letting the operator complete site-specific metadata before registering the log.
The plan and readiness cards also promote the next pending condition with its
capture/register command buttons, and the plan list can load that next condition
directly into the Field Evidence Case form.
The local-only `Load Next Field Condition` setup action performs the same load
from the newest downloaded plan, so the operator can move from `Create Plan` to
metadata entry without scrolling through the plan list. Loading a condition only
prepares the form; proof is created after the field case is captured and
registered.
When the all-in-one Evidence Workflow runs without an explicit field case, it
also auto-loads the plan's next pending condition, captures into that
condition-specific output folder, and uses the matching terrain log path for
registration once metadata is complete.
Each placeholder condition in the template and plan also carries a
`capture_metadata` object plus a capture checklist. The metadata covers
operator/date, location label, flight altitude, speed, lighting, weather,
terrain texture, map-age or seasonal notes, camera focus/exposure notes,
IMU/PX4 state, safety notes, and freeform notes. Generated registration
commands pass the filled JSON through `VISION_NAV_FIELD_CAPTURE_METADATA`, so
registered field replay cases keep that context for support review. The
field-evidence and threshold-tuning proof gates require that metadata to be
filled for every real field case; placeholder `TODO` values or missing numeric
altitude/speed context keep the final readiness proof failed. Operators using
the Pi terminal instead of the desktop form can run
`scripts/pi/update_field_capture_metadata.sh` to patch the active manifest for a
condition and regenerate the collection plan before running the Evidence
Workflow again. When the Evidence Workflow skips registration for incomplete
metadata, its report markers include the condition-specific metadata update
command for support review, and Module Setup shows a `metadata` copy chip on the
downloaded Evidence Workflow report card.

Module Setup can also register the latest Pi terrain runtime log as a field
evidence case after capture. The operator selects expected behavior, condition
tags, capture metadata, notes, and whether to replace an existing case. The app runs
`scripts/pi/register_field_replay_case.sh` over SSH, which updates the Pi-side
field replay manifest and writes the field-evidence report that the next
support bundle will include automatically. The same action downloads
`field_evidence_report.json` to `~/DroneTransfer/from-pi/replay-cases/` and the
Field Evidence Coverage list shows which required real-world conditions are
covered or still missing.
The Field Evidence Case form sends `VISION_NAV_FIELD_CAPTURE_METADATA` during
registration. The Evidence Workflow only includes the optional registration
step when that metadata is complete, so it does not create field cases that are
known to fail the proof gate.
The form draft is also saved in local desktop app storage, so operators can
switch pages or restart the app without losing the field-capture context they
need for the next proof registration.

Module Setup can run `Threshold Tuning` after enough field cases are registered.
The action runs `scripts/pi/run_threshold_tuning_report.sh` over SSH, writes the
threshold report under the Pi replay-cases folder, and downloads it to
`~/DroneTransfer/from-pi/replay-cases/` on the desktop. The Threshold Tuning
Reports list shows downloaded JSON reports with coverage status, replay status,
field-case count, and the main acceptance-rate margins.

Module Setup can run the local-only `PX4 SITL Receiver Capture` action on the
desktop/PX4 workstation. The action runs
`scripts/dev/run_px4_sitl_external_vision_capture.sh`, stores the session under
`~/DroneTransfer/from-pi/px4-sitl-evidence/`, refreshes the PX4 Capture
Prerequisites list, and refreshes the PX4 Receiver Evidence list. The local
readiness re-audit also scans that folder for `receiver_evidence.json`. If the
local workstation is missing PX4 or `tmux`, the action still prepares the
evidence-session folder, synthetic sender log, and manual capture README, then
exits failed so the operator can fix the prerequisite without losing the capture
instructions. The same folder includes
`px4_sitl_capture_prereqs.json` and the
`__VISION_NAV_PX4_SITL_PREREQS__=...` marker with the missing prerequisite
checks. Module Setup lists those checks, copyable next actions, and copyable
fix commands separately from receiver proof. Those fix commands now include the
dry-run-first `scripts/dev/setup_px4_sitl_prereqs.sh` helper, which can install
`tmux` with `--apply` and clone PX4 only when `--clone-px4` is also provided.
The local-only `PX4 Prereq Setup` action runs that helper in dry-run mode from
the app before receiver capture, so the operator can review the exact install
and clone commands without modifying the workstation.
Support bundles ingest that
prerequisite report under `px4_sitl_prereqs` and show it as a separate
`px4 prereqs` status, while PX4 receiver proof still requires
`receiver_evidence.json`. Autonomy-readiness reports, evidence packages, and
handoffs preserve the same fix commands so the operator can continue from a
downloaded report without reopening the raw prerequisite JSON. Downloaded
autonomy-readiness cards expose those setup fixes as a separate
`prereq fixes` command-copy group, distinct from proof-producing immediate and
blocked follow-up commands. Downloaded support-bundle details also expose the
same PX4 prerequisite fix commands when the bundle contains
`px4_sitl_prereqs.fix_commands`.

Module Setup can run `ROS Bag Validation` after a terrain runtime/replay log
exists. The action runs `scripts/pi/run_rosbag_export_validation.sh` over SSH,
exports the default terrain log into the dependency-free ROS bag JSONL review
format, validates the export, downloads `rosbag-jsonl-validation.json` to
`~/DroneTransfer/from-pi/terrain-match/`, downloads the source
`terrain_matches.jsonl`, and refreshes the ROS Bag Validation list used by the
final readiness audit. After that log is synced, the local-only `Native rosbag2
Review` action runs `scripts/dev/run_rosbag2_cli_review.sh` from the desktop
repo against the downloaded log and writes
`~/DroneTransfer/from-pi/terrain-match/rosbag2-cli-review.json` for the next
local readiness re-audit. The evidence workflow treats that review as proof
only when the review status, export validation, native format, and
`ros2 bag info` result all pass; degraded or failed review JSON remains
downloadable for diagnostics.

For Pi-side support sessions where a single command is easier than clicking each
step, `scripts/pi/run_autonomy_evidence_workflow.sh` attempts the same ordered
path: field template, field collection checklist, optional field-case
registration, feature benchmark, threshold tuning, ROS bag JSONL export
validation, PX4 ODOMETRY receiver proof check, support bundle, and final
readiness audit. The PX4 check records passed evidence when
`VISION_NAV_PX4_SITL_REPORT` points to an evaluated receiver report. A
`VISION_NAV_PX4_SITL_SESSION` path is preserved for diagnostics, but the step
stays skipped until `receiver_evidence.json` exists. It writes
`autonomy_evidence_workflow.json` with per-step status, log paths, output tails,
an accompanying compressed workflow-log archive, and emitted markers, while
still failing honestly in the final readiness report until real PX4 and field
evidence exist. The final audit step mirrors the generated readiness report's
status, even when the wrapper exits successfully to preserve artifacts. The
archive preserves the full step outputs under `logs/*.log` for support review.
The wrapper also writes a validation JSON beside the workflow report to prove
the report/archive pair is internally consistent.
The Module Setup `Evidence Workflow` action runs that wrapper over SSH, uses the
current Field Evidence Case form values for optional case registration,
downloads the workflow JSON, log archive, and validation JSON, and also
downloads any support bundle, field-evidence report, feature-method benchmark,
threshold-tuning report, readiness report, handoff, evidence package,
field-collection plan/checklist, ROS bag validation report, or PX4 receiver
report marker emitted by the wrapper.
Downloaded workflow reports are indexed after app restart in the Evidence
Workflow Reports list with pass/fail/skip counts, per-step status, emitted
artifact markers, and copy/reveal controls. Artifact marker chips copy the
emitted logs, validation report, support, field, feature, threshold, readiness,
handoff, package, field-plan, ROS bag validation, or PX4 path for support
notes; the `all` chip copies the complete artifact path bundle. When the
downloaded artifact exists in the standard transfer folders, those chips copy
the local desktop path instead of only the Pi-side marker path. When the
validation JSON exists beside the workflow report, the card summarizes
validation status, workflow status, issue count, the next required workflow
step with a copyable command hint, failed/degraded validation checks, missing
final-proof markers, and the first validation issue.
For offline support review, run
`vision-nav-validate-evidence-workflow --report <autonomy_evidence_workflow.json>`
against a downloaded workflow report. The validator confirms the required
ordered steps are present and that the workflow-log archive contains a
`logs/*.log` member for every recorded step. When the generated readiness
report is available locally, it also checks that the final audit step status
matches `autonomy_readiness_report.json`. It also reports missing final-proof
artifact markers for support bundle, PX4 receiver, field evidence, feature
benchmark, threshold tuning, ROS bag validation, native rosbag2 review, final
audit, handoff, and evidence package outputs. It exits nonzero only when the
workflow report/archive pair is structurally failed; a `degraded`
validation can still mean the artifact is usable but field or PX4 proof is
incomplete.

Module Setup can also run `Feature Benchmark` against the latest field replay
log and active runtime bundle. The action runs
`scripts/pi/run_feature_method_benchmark.sh` over SSH, writes the report under
the Pi feature-method benchmark folder, downloads it to
`~/DroneTransfer/from-pi/feature-method-bench/`, and shows the recommended
method plus per-method accepted rates.

Module Setup also indexes downloaded final audit reports from
`~/DroneTransfer/from-pi/replay-cases/`. The Autonomy Readiness Reports list
shows the latest JSON reports with pass/degraded/fail counts, support-bundle
bench status, PX4 receiver proof status, field-evidence status,
feature-benchmark status, and threshold-tuning status. Each report can be
revealed in the local file manager or copied by path for support notes. Failed
or degraded reports include next-action rows that point to the matching Module
Setup action or shell command needed to collect the missing evidence. Field and
threshold failures also show the missing condition checklist directly in the
report card. When the support-bundle bench gate is degraded or failed, the same
next-action list includes failed/degraded bench subchecks such as runtime
status, PX4 receiver evidence, replay gates, or parameter exports. The report
card shows each subcheck's status and message so the operator can jump to the
specific setup action that fixes it.
Each final audit report also carries an `evidence_manifest` that the app renders
as a goal-completion proof summary. It shows whether the implementation is ready
to be treated as complete, the final audit's proof-item passed/total count, how
many external proof blockers remain, and the first missing PX4 receiver, field
replay, feature-benchmark, threshold, or support-bundle evidence items.
The JSON report also includes a `proof_runbook` that orders the remaining proof
work into source-plan, bench, field dataset, method/threshold, ROS replay, and
final-audit phases. This keeps downstream support tooling from treating all
missing evidence as parallel work when some gates depend on real field logs
first.
When the support-bundle proof is still missing, the same report card also shows
the strict bench evidence order from the final audit: expected inputs, the
support-bundle command, Module Setup action names, copyable wrapper commands,
and any dependency that must be completed before the bundle is recreated.
The Autonomy Readiness Reports card renders that runbook as phase counts,
per-phase status chips, upstream dependency status, proof checks, and copyable
phase commands, falling back to the evidence package's bounded runbook summary
when the direct report field is unavailable.
The same card renders the report `plan_snapshot` when present, showing research
marker/reference coverage and implementation track/task/done counts without
opening the JSON report.
It also shows audit provenance from the report or evidence package, including
the generated timestamp, repo branch, commit hash, and dirty/clean state, so
support can tell which code revision produced the readiness result.
Module Setup also lists downloaded PX4 receiver-evidence JSON reports from
`~/DroneTransfer/from-pi/px4-sitl-evidence/`, including sample count, latest
sample age, observed receiver rate, MAVLink version, and issue summaries. The
local readiness wrapper consumes the downloaded feature-benchmark JSON and PX4
receiver-evidence JSON directly, so a new benchmark or receiver check can be
audited without rebuilding the support bundle just to duplicate the same report
summary.
Module Setup also exposes `Local Readiness Re-Audit`, which runs
`scripts/dev/run_local_autonomy_readiness_audit.sh` against the already
downloaded `~/DroneTransfer/from-pi/` evidence folders from the desktop app. It
does not require an SSH connection, and it refreshes the same final readiness,
workflow, field, feature, threshold, ROS bag, PX4, and support-bundle report
lists after the local audit finishes. When no downloaded support bundle is
available yet, the local wrapper still writes the failed readiness report,
handoff, and evidence package, and it names `support_bundle_bench_readiness` as
the missing proof gate in terminal output and package metadata.

After Mission Planner builds and uploads a bundle to a Raspberry Pi device, the
`Open Bench Report In Module Setup` action opens that device's setup tab with
the uploaded bundle path already handed off. From there, `Create Bench Report`
validates the deployed terrain bundle, creates the support bundle on the Pi, and
downloads it to the desktop. The following `Autonomy Readiness` setup action
runs `scripts/pi/run_autonomy_readiness_audit.sh` over SSH using the latest
Pi-side support bundle when available, then downloads the strict final audit report to
`~/DroneTransfer/from-pi/replay-cases/` on the desktop. When the Pi emits a
Markdown handoff marker, Module Setup downloads that handoff beside the JSON
report. When the Pi emits the evidence-package marker, Module Setup also
downloads `autonomy_readiness_report.evidence.zip` beside the JSON report and
shows both local paths in the Latest Output panel for support review. If the
support bundle is still missing, the Pi wrapper still emits a failed audit,
handoff, and evidence package that name `support_bundle_bench_readiness` as the
missing proof gate. When the
readiness wrapper also emits evidence-workflow report, workflow-log archive,
workflow-validation, field-evidence, feature-benchmark, threshold-tuning, or
field-collection markers, the same action downloads those sibling artifacts and
refreshes the matching report lists.
When the wrapper emits `__VISION_NAV_PX4_SITL_PREREQS__`, Module Setup downloads
the PX4 capture prerequisite JSON beside the receiver report. The final
readiness handoff and evidence ZIP keep that file as a diagnostic artifact, but
it does not satisfy the PX4 receiver-proof gate.
If a field collection plan/checklist exists in the replay-cases folder, the
audit records those paths and the evidence ZIP includes them as referenced
artifacts.
On later app launches, the Autonomy Readiness Reports list detects sibling
`autonomy_readiness_report.md` and
`autonomy_readiness_report.evidence.zip` files beside each JSON report and
exposes copy and reveal controls for both artifacts. When the evidence ZIP has
the expected `manifest.json`, the list also shows included, missing, and
skipped artifact counts plus the first included/missing/skipped artifact labels
so support can tell what proof is present or absent without opening the archive.
Missing entries include not-yet-passed required proof gates, even when no
concrete artifact path has been produced yet. For those proof gates, the card
preserves and displays status, reason, message, source, and the first missing
condition keys when available.
If the package manifest includes a compact workflow-validation summary, the card
also shows the workflow status, issue count, failed/degraded checks, missing
final-proof markers, and non-passing required workflow steps from the package
itself.
The evidence-package command prints the same first missing package artifact
labels to terminal logs, so CLI-only support reviews can identify absent proof
gates without opening the ZIP manifest.
Readiness report cards also provide bulk command copy actions for immediately
runnable next-action shell commands and blocked follow-up commands, while
preserving each row's individual command copy control. The underlying JSON
report includes the same split command bundle for downstream support tooling,
with its compatibility `next_action_commands` list ordered by proof-runbook
dependencies. Module Setup reads that bundle directly so bulk copy still works
when the referenced Pi-side field plan is only available as downloaded report
metadata or the direct report JSON is older than the evidence package. Saved
setup reports also include the latest readiness report's `command_bundle`,
falling back to the command bundle preserved in the evidence ZIP manifest.
If the readiness report points to a local field collection plan, the same card
shows the plan status, registered-vs-required count, pending condition count,
and first pending collection conditions. For Pi-generated reports that still
refer to Pi-side absolute paths, the app falls back to a downloaded sibling
`field_collection_plan.json` beside the readiness report. The local Markdown
handoff renderer and evidence ZIP packager use the same fallback, so support
packages can still include the downloaded JSON/Markdown checklist.
Downloaded support-bundle details also surface bundled field collection plans:
the card shows plan pass/degraded state, registered-vs-required condition
counts, capture-command/runtime-status path coverage, per-condition
placeholder/missing/registered status, and lets support extract the
JSON/Markdown plan artifacts from the ZIP.
Pending field-collection condition pills and command buttons in Module Setup
copy individual or batched generated capture and registration commands when the
plan includes them, which keeps real replay-case collection and registration
out of manual retyping. The first pending condition is shown separately as the
next field-capture target in downloaded plan and readiness cards.
The Markdown handoff mirrors that workflow with a copy-friendly command bundle
for guided workflow, immediate next-action, blocked follow-up, and pending
field replay capture/registration commands. It also
summarizes the research/implementation source-doc snapshot that the final audit
used, including required marker coverage and implementation track/task counts.
It also renders the readiness report's proof runbook so support can see which
phase is passed, waiting on an operator action, or blocked by an upstream proof
artifact.
When downloaded evidence-workflow JSON, workflow-validation JSON, and workflow
log archives are available, the local/Pi readiness audit records them as
non-gating inputs so the handoff can show availability and the evidence ZIP can
carry them with the rest of the review package when they are under the artifact
size limit. The evidence ZIP manifest also includes the plan snapshot, bounded
proof counts, a bounded proof-runbook summary, a preserved command bundle, a
compact workflow-validation summary, and the first proof items, and the Autonomy
Readiness Reports card shows those package proof counts beside
compact workflow, validation, and logs chips for referenced inputs, with
copy/reveal actions when the downloaded local artifact exists.

## MAVLink

MAVLink output is opt-in. When enabled in Mission Planner runtime controls, the
app sets:

```bash
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600
VISION_NAV_MAVLINK_MESSAGE=vision_position_estimate
VISION_NAV_EXTERNAL_POSITION_MIN_RATE_HZ=1.0
VISION_NAV_EXTERNAL_POSITION_MAX_LATENCY_MS=500.0
```

Accepted map matches are sent as MAVLink `VISION_POSITION_ESTIMATE` by default,
with local NED position derived from the repo's local ENU measurement. Set
`VISION_NAV_MAVLINK_MESSAGE=odometry` to bench the richer PX4 external-vision
`ODOMETRY` path. Rejected matches are logged but not sent. Runtime logs include
`external_position_health` snapshots with output status, send rate, latency,
skip reasons, and covariance warnings.

ArduPilot device selection is kept as an adapter-readiness path, not the
default runtime output. PX4 remains the bench target until receiver evidence is
repeatable. The ArduPilot design and parameter audit workflow live in
[ArduPilot ExternalNav Adapter Design](ardupilot-externalnav-adapter.md).

The Devices Control tab mirrors the same runtime actions for a selected Pi:
status, short terrain loop, stop loop, view logs, create support bundle, and
service status. The support-bundle action also downloads the generated zip to
the desktop transfer folder and shows recent downloaded bundles with the same
parsed health summary.
