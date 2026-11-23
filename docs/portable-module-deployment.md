# Portable Navigation Module Deployment

## Product Boundary

The product is a companion flight-computer and camera module, not an X500- or
X650-specific navigation implementation. Holybro X500 V2 and X650 V2 are the
current development airframes. Their configuration files declare test-platform
identity, not verified hardware specifications, firmware, calibration or approval.
No mass, motor layout, GPS model or flight-controller type is inferred from a name.

```text
installation: airframe + module mounting + controller firmware/transport
             camera/IMU/body calibration + clock/noise evidence
                               |
module: recorded/synchronized sensors -> local VIO + learned map evidence
                               |
          shadow results + replay evidence + ground-control diagnostics
                               |
future qualified flight-controller adapter (currently no vision publication)
```

Change installation data when moving a module. Do not fork the estimator by
airframe. Keep compute/provider, camera ingress, inertial source and
flight-controller wire protocol separate. The existing internal ENU/FRD/time/
covariance contracts still apply. A new mechanical mount requires measured
extrinsics and vibration/timing checks; it cannot inherit another mount's approval.

## Implemented In This Pass

- `vision-nav-module inspect/run` validates a bounded, versioned deployment
  configuration, resolves inputs inside one read-only root and dispatches only
  allowlisted existing jobs. It never executes a configured shell command.
- `vision-nav-module schema` exports JSON Schema for configuration tooling in
  other languages, without opening any devices or reading installation files.
- Generic, X500 V2 and X650 V2 installation examples use the same learned profile.
  They default to diagnostics and disabled flight-controller transport.
- Portable CPU container with ONNX Runtime, geometry/geospatial dependencies and
  MAVLink tooling; separate opt-in native research image builds the pinned
  ROS-free OpenVINS adapter and runs its CTest suite during the image build.
- Real native sensor smoke/replay, combined learned/native recorded replay,
  and finite PX4 telemetry bench dispatch. No fake neural backend is supplied.
- New run directories contain `launch.json` with normalized configuration hash,
  declared installation, package/platform identity and exact command. Existing
  job reports retain their own checksums, timing and acceptance decisions.
- Ground-station static web image serves the existing React app with SPA routing.
  The old development-server target remains available.

This is a **deployment and interoperability foundation**, not a promoted live
navigation service. `launch.json` is launch provenance, not runtime liveness,
successful completion or release compatibility. Job exit status and result
reports are authoritative for completion. All outputs remain shadow-only.

## Supported Jobs

| Job | Image target | Behavior |
| --- | --- | --- |
| `diagnostics` | `runtime` | Validate configuration and report installed packages; no sensor access/model execution |
| `native_smoke` | `research-native` | Render deterministic image/IMU fixture, run real native VIO, require complete 160-frame replay |
| `vio_replay` | `research-native` | Process a sealed capture through existing bounded native stream owner |
| `learned_replay` | `research-native` | Existing learned camera-window replay with retrieval, local geometry and concurrent native VIO |
| `telemetry_bench` | `runtime` | Existing finite PX4 MAVLink evidence capture; explicit prop-off attestation required |

These are finite jobs: do not configure automatic restart for recordings or
bench commands. Container termination goes to the job owner via exec and Compose
init. An interrupted job is incomplete evidence; retained launch files are not
silently overwritten on restart. There is no new persistent HTTP control server.

## Quick Start: Linux, Mac Or Windows Linux Containers

Requires Docker Engine/Desktop with Compose v2 or newer. Native Pi uses 64-bit
Linux ARM; Intel/AMD workstations use Linux AMD64 and Apple Silicon uses ARM64
containers. Host camera/USB behavior does not
become portable through emulation. Cross-platform image builds are supported by
the Dockerfile structure but require actual architecture-specific verification.
[Docker multi-platform guidance](https://docs.docker.com/build/building/multi-platform/).

### Installed Development Mac

The development Mac now has Docker CLI, Compose, Buildx and a dedicated Colima
VM named `drone-dev` (4 CPUs, 6 GiB RAM, 50 GiB virtual disk). It uses Apple's VZ
virtualization and mounts only this project writable. The default Docker context
was not changed and login auto-start was not enabled. No aircraft was connected.

Use the explicit context on this Mac; prepend `docker --context colima-drone-dev`
instead of plain `docker` in the examples below. To start the configured VM:

```bash
colima start drone-dev --activate=false
docker --context colima-drone-dev version
python3 scripts/dev/verify_module_containers.py --context colima-drone-dev
```

To release its memory after development, run `colima stop drone-dev`.
For a fresh Mac, install `colima docker docker-compose docker-buildx` with
Homebrew and follow the [Colima setup](https://github.com/abiosoft/colima) and
[Compose plugin discovery instructions](https://formulae.brew.sh/formula/docker-compose).
Do not overwrite an existing Docker configuration. The current Mac's plugin
directory is `/opt/homebrew/lib/docker/cli-plugins`.

From the repository root on Linux/macOS:

```bash
mkdir -p data/module-inputs logs/module
export SOURCE_REVISION=$(git rev-parse HEAD)
export MODULE_UID=$(id -u)
export MODULE_GID=$(id -g)
docker compose -f docker/module/compose.yaml build module
docker compose -f docker/module/compose.yaml run --rm module inspect
docker compose -f docker/module/compose.yaml run --rm module run --run-id diagnostics-001
```

On Windows PowerShell, with Docker Desktop using Linux containers:

```powershell
New-Item -ItemType Directory -Force data/module-inputs, logs/module
$env:SOURCE_REVISION = git rev-parse HEAD
docker compose -f docker/module/compose.yaml build module
docker compose -f docker/module/compose.yaml run --rm module inspect
docker compose -f docker/module/compose.yaml run --rm module run --run-id diagnostics-001
```

The defaults bind `data/module-inputs` read-only and `logs/module` writable.
The config is read-only. The host directories must exist; Compose will not
silently create directories for misspelled paths. On Linux, the runtime UID/GID
must have write access to the output directory. Inputs/models/captures/secrets
are never baked into images. Use a fresh run ID each time.

## Real Native Integration Smoke

No drone, map or trained weights are needed for this synthetic integration test.
The native image includes GPL OpenVINS source/license and our native adapter;
review distribution obligations before shipping it commercially.

```bash
docker compose -f docker/module/compose.yaml build research
export MODULE_CONFIG=../../config/module/native-smoke.json
docker compose -f docker/module/compose.yaml run --rm research run --run-id native-001
```

PowerShell selection: `$env:MODULE_CONFIG = "../../config/module/native-smoke.json"`.
Relative Compose mount paths resolve from `docker/module`, not the terminal's
current directory; absolute local paths also work. Outputs are under
`logs/module/native-001/result/`, including `vio/report.json` and exact sensor
inputs. This tests software execution, not location accuracy or Pi latency.

## Captured Data And Learned Replay

Use `config/module/vio-replay.json` or `learned-replay.json` as the template.
Place the corresponding capture/export/map/config files under `data/module-inputs`.
Copy the selected installation metadata into a local configuration, stored as
`config/module/<name>.local.json` (ignored by Git).

`learned_replay` requires a training session, student ONNX export, reference map,
tile layout, measured camera calibration, MegaLoc export/index, local XFeat/
LighterGlue verification config and sealed VIO capture. Nested artifact manifests
must also be portable and checksum-valid. Simply mounting a former Mac path or
renaming files does not repair model/map provenance; use the existing validated
export and capture-preparation workflows. Do not rewrite checksums to hide errors.

Set `MODULE_CONFIG` to the local config and run the `research` service as above.
The child validates the full existing release/capture chain; launcher validation
only checks job structure, path containment and native capabilities. The
installation ID is an annotation, not proof that an imported capture came from
that aircraft. Field capture binding remains in the capture's own metadata.

## Flight-Controller Interoperability

The declared interface is MAVLink 2 with separately selected serial or UDP input.
The PX4 evidence adapter is the only implemented bench policy here. ArduPilot
may be recorded as installation metadata, but launching its bench path is
rejected until a dedicated adapter and receiver evidence exist. Generic protocol
compatibility is not autopilot/firmware qualification. No NMEA requirement exists.

For a **native Linux prop-off serial bench**, first verify the controller path,
baud and device group. Use `serial-bench.json` as a local template and replace the
installation identities with your measured hardware inventory. The baud in the
example is not automatically correct for either development aircraft.

```bash
export MODULE_CONFIG=../../config/module/serial-bench.json
export FC_DEVICE=/dev/serial/by-id/REPLACE_WITH_VERIFIED_DEVICE
export FC_GID=$(stat -c %g "$FC_DEVICE")
docker compose -f docker/module/compose.yaml -f docker/module/serial-bench.yaml \
  run --rm module run --run-id bench-001 --propellers-removed
```

Only the selected device is mapped to `/dev/flight-controller`; the process is
non-root, gets the device group, and has no whole-`/dev`, privileged or host-network
access. Stop other direct serial owners first. This bench **sends bounded
TIMESYNC probes** for timing evidence, but no arming, parameter, stream-rate,
actuator or external-vision commands. It is not strictly receive-only.

For already-routed UDP telemetry, select `transport="udp_listen"`, port 14550,
and use `udp-bench.yaml`. Invoke `run --service-ports --rm module ...` because
Compose one-off jobs otherwise do not publish service ports. Bind only a trusted
local interface using `TELEMETRY_BIND_IP`; the default is loopback. Do not expose
MAVLink or the existing prototype companion API publicly. A forwarded endpoint
is still one logical telemetry owner. No router is automatically installed.
The existing bench checks message presence/cadence, not authenticated controller
identity. Do not mix multiple vehicles on the input: sender-ID/firmware binding
and authenticated routing remain acceptance work before operational use.

## Camera And Inertial Portability

The module currently accepts sealed, ordered recordings for portable container
testing. Live Picamera2/libcamera capture still runs in the existing host sensor
owner with measured camera and IMU profiles. Raspberry Pi's camera software is
coupled to its libcamera stack and sensor support, not a generic pip-only USB
driver. [Raspberry Pi camera documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html).

Do not solve CSI access by giving this image privileged access to the whole host.
The next hardware integration must qualify either a pinned Pi camera image with
an explicit minimal device/driver map, or a bounded host-to-container sensor
adapter preserving exposure time, raw IMU order, synchronization uncertainty,
extrinsics, reset epochs and backpressure. That live adapter is **not implemented
in this pass**. USB/UVC and custom camera/IMU modules need equivalent contracts;
receipt-time video streaming is not a substitute. If inertial data comes from
the flight controller, attitude packets alone are insufficient for VIO.

## Ground-Control App

```bash
docker compose -f docker/module/compose.yaml --profile gcs up -d --build gcs
```

Open `http://localhost:8080`. This is the actual existing frontend in a static
container, not the native Tauri executable or a replacement hardware backend.
Tauri-only SSH/filesystem dialogs still require the native app; existing browser/
companion fallbacks retain their current limitations. No new backend integration
or live-navigation availability is implied by a healthy web server. Existing
desktop development scripts continue to use the `development` target.
Stop it with `docker compose -f docker/module/compose.yaml stop gcs`.
Profiles isolate optional jobs/services. [Compose profiles](https://docs.docker.com/compose/how-tos/profiles/).

## Repeatable Container Verification

From the repository root, with Docker running and Python 3 installed:

```bash
python3 scripts/dev/verify_module_containers.py --build
```

On Windows, use `py -3` instead of `python3`; Docker Desktop must be in Linux
container mode. On the configured Mac add `--context colima-drone-dev`.
Omit `--build` to verify existing images. The verifier compares installed Python
and native adapter source with this checkout and rejects stale images. It does
not modify models, connect to hardware, start training, push images or allocate
cloud resources. Use a fresh directory when supplying `--output`.

Evidence is saved under `logs/module-validation/<run>/verification.json` with
image IDs, architecture, commands, exit codes, source fingerprint and per-step
logs. Actual native replay evidence is in `runs/native/result/`. The temporary
web probe is removed after testing. HTTP checks validate static SPA delivery,
not browser interactions or native Tauri functions.

## Verification And Next Work

This implementation pass: **91 tests passed, no skips** across module deployment,
native replay and the PX4 boundary (12.24 s on the development Mac). The module
test actually launched and completed the 160-frame native smoke. All seven local
preflight stages, frontend build and Rust check passed. Frontend import/chunk
warnings remain. Python 3.11 binary-wheel dependency resolution succeeded for
Linux ARM64 and AMD64; Rasterio was changed to 1.4.4 after 1.4.3 lacked the
required ARM64 wheel. Resolution is not execution or numerical parity.

**Subsequent installation/verification on 2026-09-05:** CPU, native research and
web images built successfully on Linux ARM64 inside Colima. The native build
passed all three CTest executables. Engine-backed checks passed for diagnostics,
duplicate-run rejection, runtime imports, installed source identity, X500/X650
configuration inspection, prop-off attestation rejection, the complete 160-frame
native replay, and HTTP health/SPA routes. The native report retained
`position_authority=false` and disabled PX4 external vision.

The run exposed and fixed a Compose tmpfs flow-list quoting bug: unquoted mount
options became separate entries. A second verifier issue was macOS `.DS_Store`
metadata absent from the installed Python package; source comparison now ignores
that metadata and bytecode but still checks all source bytes. Both have regression
coverage. Rejected-launch checks require the specific structured safety reason,
not merely a failing exit code. The updated focused suite passed **95 tests, no
skips** (13.08 s), including host native execution. All seven local preflight
stages passed again after installation.

Local engine evidence: `logs/module-validation/arm64-20260905-r3/verification.json`.
These generated logs/images are not committed or available merely by cloning.
Image identities recorded in that run:

| Image | ARM64 local image ID |
| --- | --- |
| `drone-vision-module:cpu` | `sha256:0589123cf3066cb1c664f9a8915647778ad88095b774eb3347820acce59a4118` |
| `drone-vision-module:research-native` | `sha256:74cfeb3a2914b822508a60bc221e27767f5fed33993c05d24f48c2234fd9645e` |
| `drone-vision-gcs:web` | `sha256:123c570b71429318139771eba6670e4773fa050a4150844ae4ef1725e2728893` |

Installed tooling: Colima 0.10.3, Docker CLI 29.8.0, Engine 29.5.2, Compose 5.5.1,
Buildx 0.37.0. Images were built from an explicitly labeled dirty working tree
based on `0351dbc939881ee9b9f437e123205a761790a750`; they are not promoted releases.

**Not executed:** Linux AMD64 images, the manual CI workflow, Pi/X500/X650
hardware tests, physical camera/serial connections or trained-model accuracy
tests inside the container. ARM64 virtualization on a Mac is not Pi performance
evidence. No verified multi-architecture or physical sensor deployment is claimed.

Run `python -m pytest -q tests/test_module_deployment.py` in the project
environment (also run `tests/test_container_verification.py`). Set
`VISION_NAV_OPENVINS_BINARY` and provide the pinned source to
exercise the actual native job rather than skipping it. The new manual GitHub
Actions workflow builds both architectures and executes the smoke in-container.
Both architecture jobs must pass before claiming multi-architecture verification;
source-level tests are not a replacement for Docker builds. Direct Python dependencies are pinned, but apt,
base images and transitive Python dependencies are not a fully frozen supply
chain. Record image digests, SBOMs, package lists and license review at promotion.

Remaining gates: camera ingress, matched firmware/sensor metadata on both
airframes, continuous learned-to-native estimation, qualified uncertainty,
geographic holdout training, combined Pi load/thermal/storage testing, signed
release activation and separately qualified flight-controller publication.
Keep the module offline during flight and the desktop outside the control loop.
The older `docker/pi` files are legacy prototypes, not this deployment path.
