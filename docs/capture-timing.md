# Camera And PX4 Capture Timing

Implemented September 5, 2026 for training/replay evidence. This is not a
flight synchronization release and does not enable external-vision output.

## What Changes In New Captures

The Picamera2 recorder retains libcamera's `SensorTimestamp` and labels it
`linux_boottime_ns`, with reference `first_row_exposure_start`. It samples
Linux BOOTTIME inside two MONOTONIC reads, before and after obtaining the
image. These brackets measure the relationship between the two host clocks.
They are not an estimate based on when a JPEG finished writing.

The color path also now writes Picamera2 RGB888 arrays directly as OpenCV BGR
bytes. The old path reversed red and blue. Existing photographs are not
modified automatically; inspect old captures before combining them with new
training data. The array is copied before releasing the camera request.

On a real MAVLink connection, the training recorder sends a TIMESYNC diagnostic
request approximately once per second, in addition to the existing before/after
capture snapshots. Responses share the existing telemetry reader with ATTITUDE
and IMU messages; no second serial reader is opened. This does not request
message rates, change parameters, arm the vehicle, or publish navigation.

Responses retain source system/component identity and destination extensions
where available. An echo must match this recorder's request; explicitly
other-addressed replies are ignored. Broadcast legacy replies remain evidence,
but observations from multiple responding sources invalidate the clock fit.

## Output

New capture `manifest.json` files include:

```text
camera metadata per frame
  sensor_clock_domain
  sensor_timestamp_reference
  host_clock_samples

mavlink.timesync.during_capture
  exchanges, interval_s, capacity_reached

mavlink.exposure_alignment
  clock_fit
  frames
  paired_frame_count
  status
  vio_ready: false
  flight_ready: false
```

The offline clock fit estimates PX4-minus-host offset and linear drift. It uses
RTT-weighted least squares and retains an evidence digest, residuals, drift,
source identity, fit interval and rejection reasons. It requires at least six
usable exchanges spanning one second. Defaults reject RTT above 10 ms, gaps
above 30 seconds, and drift above 200 ppm. Clock resets, duplicate/reordered
observations, mixed sources and inconsistent offset residuals reject the fit.
These thresholds are initial engineering policies, not measured Pi/Pixhawk
performance claims.

Conversions never extrapolate beyond the observed clock interval. Their timing
envelope includes RTT, fit residual/slack, host-clock bracket width and distance
from a timing observation under the configured drift allowance. This envelope
is not a calibrated statistical covariance or a proven worst-case bound on
sensor delay. Conversion is withheld above the default 5 ms envelope.

Exposure timestamps are compared with same-source ATTITUDE native boot times.
The output references the nearest recorded sample, with explicit delta and
millisecond timestamp quantization allowance. Delta plus clock envelope must
remain below 25 ms. No Euler interpolation is invented. Duplicate/reordered
attitude or camera timestamps, unknown clock domains, stale/future sensor
timestamps, and host suspend/clock steps produce explicit rejected entries.

The capture's existing `attitude_alignment` and `frame_attitude_alignment`
receipt-time evidence remain unchanged for compatibility. New DatasetRelease
creation and training-index preparation now default to exposure/native-attitude
pairing. Existing indices/labels are not modified in place. GNSS ground-truth
timing is not calibrated by this change. Older recordings without source
identity/host brackets are reported incomplete; those measurements cannot be
reconstructed from file dates.

## Run A Timing Audit

After a short prop-off capture with the existing training recorder:

```bash
python -m vision_nav.capture_timing --session /data/captures/prop-off-session
```

Or, after updating the editable installation:

```bash
vision-nav-audit-capture-timing --session /data/captures/prop-off-session
```

The command validates session checksums, recomputes timing from raw evidence,
and prints JSON. It does not trust the stored summary or modify the session.
Exit code 0 means every frame has a pairing under this policy; exit code 2
means missing/invalid evidence or an incomplete timing result. A successful
audit is not a VIO or flight-readiness approval. Keep exported audit files
outside immutable capture folders to avoid changing their checksum inventory.

## Before Main Data Collection

1. Record the actual Pi OS, Picamera2/libcamera versions, camera/sensor mode,
   PX4 firmware version, and MAVLink source IDs.
2. Keep props off for a short recording. Confirm native ATTITUDE and raw IMU
   availability, continuous timing observations, and no recorder errors.
3. Inspect rejected timing frames, drift, maximum gaps and pairing envelopes.
   Investigate USB congestion or missing metadata rather than widening limits
   just to make a report pass. The periodic recorder caps evidence at 36,000
   exchanges; a capacity warning requires reviewing capture length/coverage.
4. Measure camera-to-IMU timing using an appropriate calibration sequence and
   verify the pinned PX4 firmware uses the expected boot time for TIMESYNC and
   ATTITUDE. Native IMU `boot_or_unknown` timestamps remain unresolved until
   their domain is verified.
5. Account for exposure duration, readout/rolling shutter, timestamp driver
   accuracy, sensor delay, IMU noise/bias, and measured camera-body extrinsics.
   First-row exposure start is not the middle of a full rolling-shutter image.

Still pending: calibrated exposure-midpoint/raw-IMU resampling, live clock-model
refresh in the navigation scheduler, GNSS-reference time calibration, and
real-hardware timing/error benchmarks.

## Training Integration

Completed [single-owner live sensor recordings](live-vio-sensor-session.md#export-into-training-tooling)
can now be exported into the same capture schema with explicit calibration and
map binding. Their primary frame timestamps are converted first-row exposure
times, not capture-call midpoints, so the index rejects legacy timing for them.
The original native per-frame fit remains in the embedded replay; the training
audit may fit recorded periodic exchanges offline and does not overwrite native
events. Absent pre/post snapshots remain absent under the strict training gate.

Prepare a new index from the DatasetRelease; exposure/native-attitude pairing
is the default, not an automatic fallback:

```bash
python -m vision_nav.neural.training_dataset \
  --dataset-release /data/dataset-release.json \
  --output-dir /data/training-index-exposure
```

The index builder verifies that the captured manifest still matches the
DatasetRelease, recomputes the timing audit from source records, and copies the
audit into the index's `timing/` directory. Rejected frames are listed with
reasons. No eligible frames produces `no_eligible_samples` and CLI exit code 2.
Readiness checks use timing envelopes rather than receipt delay for these
samples. This is data-preparation readiness only, never flight approval.

Rebuild labels, splits, map pairs, and temporal windows from that index using
the existing workflow. Windows retain their audit path, digest, frame sequence,
image digest and timing policy. The map-conditioned dataset builder defaults
to requiring this evidence; it checks image identity, timestamp and attitude
agreement, then includes the audited files in its portable package. Its loader
checks model time offsets and roll/pitch against the packaged audit, including
after moving the dataset to another machine. Windows cannot mix timing policies.

Legacy research requires explicit flags at each relevant boundary:

- DatasetRelease creation: `--attitude-timing-policy legacy_host_receipt`.
- Training index: `--timing-policy legacy_host_receipt`.
- Map-conditioned package: `--allow-legacy-timing`.

For the index shell wrapper, set
`VISION_NAV_TRAINING_TIMING_POLICY=legacy_host_receipt` only for those old
research captures. Missing-sensor overrides require legacy mode too. The
dataset, training checkpoint, and ONNX export retain the training timing-policy
list. Do not describe these legacy runs as exposure-aligned. Old cross-view
packages without per-frame timing provenance need rebuilding, even when used
with the explicit legacy option.

GPS references remain labeled `host_receipt_unverified_measurement_time`.
Exposure-aligned attitude does not resolve GNSS sensor delay, antenna lever
arm, altitude datum, or rolling-shutter exposure geometry.

## Verification And Sources

```bash
python -m pytest -q tests/test_capture_timing.py tests/test_inertial_capture.py tests/test_exposure_training.py
python tests/run_unit_tests.py
```

Tests use deterministic clocks, transport doubles and image fixtures. No
physical camera synchronization or inertial accuracy is claimed by these tests.

Protocol: [MAVLink TIMESYNC](https://mavlink.io/en/services/timesync.html).
Timestamp semantics: [libcamera SensorTimestamp](https://docs.libcamera.org/master/internal-api/namespacelibcamera_1_1controls.html).
Pixel ordering: [Picamera2 manual, pixel formats](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).
