# Pi Capture To Local VIO Replay

This offline converter connects `vision_nav_training_capture_v1` sessions to the
native, ROS-free OpenVINS research runner. It uses actual recorded images and
selected accelerometer/gyro samples, not GPS or PX4 attitude as a replacement
for motion estimation. It does not run a vehicle service, change PX4 settings or
publish position. Learned map corrections remain a separate, unfinished live
integration.

## Required Inputs

- A stopped, checksummed Pi training-capture session with original camera
  exposure metadata, pre/during/post TIMESYNC observations and continuous
  `HIGHRES_IMU` records from one explicitly identified PX4 sender and sensor.
- Measured global-shutter camera intrinsics, camera-to-selected-IMU transform,
  fixed camera/IMU delay and IMU noise calibration for this exact assembly.
- A sensor-profile directory containing the two native calibration YAML files,
  qualification evidence and `sensor_profile.json`, described below.
- The clean pinned OpenVINS source and built research binary from
  [the native replay guide](openvins-research-replay.md).

This first converter does not support rolling-shutter approximation, implicit
sensor selection, mixed IMUs, gravity-subtracted acceleration or arbitrary raw
sensor axes. A manual-focus lens does not determine whether the sensor is global
or rolling shutter. Confirm the actual camera model. Do not change the profile's
shutter declaration to bypass a rejection.

The camera-to-IMU transform is not automatically the camera-to-airframe transform.
Here the selected HIGHRES_IMU stream must be confirmed as sender-corrected body
FRD. The calibration must reference those same axes. Physical IMU axes, a gimbal
frame and a loosely mounted camera cannot be silently substituted.

Calibration and accurate relative timestamps matter especially during motion.
[OpenVINS sensor calibration](https://docs.openvins.com/gs-calibration.html)
describes these dependencies. A successful TIMESYNC fit alone does not measure
camera exposure delay or prove that the selected firmware's IMU message clock
matches its TIMESYNC clock. Record that firmware-specific check in the evidence.
The [MAVLink HIGHRES_IMU contract](https://mavlink.io/en/messages/common.html#HIGHRES_IMU)
includes sensor identity and updated-field flags; the converter requires fresh
values for all six accelerometer/gyro axes in each selected sample.

## Sensor Profile

The directory must contain `kalibr_imucam_chain.yaml`, `kalibr_imu_chain.yaml`,
qualification evidence files, and `sensor_profile.json`. Use this structure for
the JSON, replacing nulls with measured/reviewed values and real file hashes:

```json
{
  "schema_version": "vision_nav_openvins_sensor_profile_v1",
  "profile_id": null,
  "calibration_status": null,
  "camera_calibration_id": null,
  "firmware_revision": null,
  "qualification_notes": null,
  "camera_shutter": "global",
  "camera_timestamp_reference": "first_row_exposure_start",
  "imu_frame": "body_frd",
  "imu_measurement": "specific_force_including_gravity",
  "imu_intrinsics_policy": "sender_corrected_no_second_correction",
  "timesync_clock_matches_imu_boot_clock": null,
  "source_system": null,
  "source_component": null,
  "imu_sensor_id": null,
  "message_type": "HIGHRES_IMU",
  "gravity_m_s2": null,
  "qualification_evidence": ["qualification-report.json"],
  "checksums": {
    "kalibr_imucam_chain.yaml": null,
    "kalibr_imu_chain.yaml": null,
    "qualification-report.json": null
  }
}
```

`calibration_status` must be `measured`, with the same camera calibration ID as
the capture. The firmware-clock declaration must be true only after checking
that relation. Evidence should cover the mount, focus, exposure settings, image
mode, noise measurements, transform and timing calibration, and firmware/IMU
selection. Checksums bind the supplied evidence but do not independently prove
the measurements or approve flight use. There is no automatically generated
"measured" profile in the repository.

Camera YAML must contain only `cam0`, with a pinhole `radtan` or `equidistant`
model, four intrinsics, four distortion coefficients, matching resolution and a
rigid `T_imu_cam` that maps camera points into the selected IMU frame. Use only
that transform convention. `timeshift_cam_imu` is required explicitly, including
when measured zero. Its convention is `t_imu = t_camera + timeshift_cam_imu`.
The supported fixed-delay magnitude is at most 100 ms, matching the native runner.
There is no automatic distortion truncation, resizing or transform inversion.

IMU YAML contains only `imu0`, the four measured positive noise/random-walk
values and positive update rate. The sender-corrected path requires explicit
identity `Tw`, `Ta`, `R_IMUtoACC`, `R_IMUtoGYRO`, `T_i_b`, zero `Tg`, zero
`time_offset` and model `kalibr` or `calibrated`. This prevents applying a second
intrinsic/frame correction to an already corrected stream. A different raw
sensor model needs a separately reviewed adapter.

## Prepare And Run

On a development machine with the project Python environment:

```bash
python -m vision_nav.vio.prepare_capture \
  --checkout research/vendor/open_vins \
  --session /data/captures/x500-session \
  --sensor-profile /data/calibration/x500-vio-profile \
  --output research/outputs/x500-vio-input-001

python -m vision_nav.vio.openvins_replay run \
  --binary research/outputs/openvins-native-build/vision-nav-openvins \
  --capture research/outputs/x500-vio-input-001 \
  --output research/outputs/x500-vio-replay-001 \
  --pose-window
```

After an editable install, `vision-nav-prepare-vio-capture` is equivalent to the
first module command. Both output directories must be new. Conversion output
must be outside the input capture/profile directories. Source files stay intact.
If conversion fails after creating its directory, that unsealed directory is
incomplete evidence; inspect it and choose a new output for a retry.

## Timing And Data Rules

The converter maps first-row exposure time through the existing host-clock
brackets and bounded offline PX4 clock fit. It never substitutes capture-call
midpoint, host receipt or UTC. A host clock step, ambiguous sender or conversion
outside the observed clock interval rejects preparation. IMU boot microseconds
become integer nanoseconds without fitted resampling or gravity subtraction.

Every camera event is delivered after sufficient IMU look-ahead, including the
clock-conversion uncertainty and the fixed delay. The native runner applies that
delay once; it is not added to the exported camera timestamp as well. No sensor
events are interpolated, fabricated or repaired. Selected IMU gaps over 50 ms,
camera gaps over 250 ms and nonmonotonic timestamps reject the session. These
are replay bounds, not a validated aircraft operating envelope. A one-frame-per-
second training capture is therefore not suitable for this VIO path.

Inputs are limited to 2..20,000 frames, 512 MiB telemetry, 64 KiB telemetry lines
and two million selected IMU samples. Samples must bracket the entire selected
camera interval. Nonselected telemetry is counted and excluded; GPS records and
the raw telemetry file are not copied into estimator input. The output includes
unchanged selected image bytes, SI events, native calibration, timing diagnostics,
supplied qualification evidence and a sealed input manifest with source hashes.

Estimator initialization/tracking settings initially use the pinned mono KLT
research configuration, not tuned X500 defaults. Native calibration optimization
remains disabled. The converter does not prove that capture cadence, motion
excitation, blur, noise parameters or the configured initialization thresholds
will produce a usable trajectory. Inspect failures and local-state coverage.

## Verification Status

Controlled tests render images and corresponding inertial motion, package them
in the actual Pi capture schema, convert their camera/PX4 clocks and run the
actual native estimator with correlated clone export. Rejection tests cover
missing/raw-versus-attitude inputs, stream gaps/order, partial updates, clock
failure, calibration mismatch, rolling shutter, changed evidence, sparse camera
frames and a prohibited second IMU correction. Fixed-delay tests verify that it
is preserved for one native application.

These are software/synthetic-sensor tests, not a calibrated X500 replay or Pi
benchmark. Every native output remains `NO_GLOBAL_FIX`, has no position
authority and disables PX4 external vision. The next hardware dependency is a
real qualified capture plus its sensor profile; the next software integration
is live bounded sensor delivery and delayed learned-map fusion.
