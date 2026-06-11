# GNSS-Denied Vision Navigation Goal

## Goal

Develop a low-cost drone navigation system that can estimate geolocation in GNSS-denied environments using computer vision and pre-installed maps, then output position in an NMEA-compatible format for downstream systems.

The target is not only to fly autonomously, but to produce a useful geolocation stream when GNSS is unavailable, degraded, or intentionally denied.

## Desired Output

The system should eventually produce NMEA-style location messages, such as:

- Estimated latitude
- Estimated longitude
- Estimated altitude or height estimate
- Estimated heading/course
- Estimated speed if available
- Position confidence or quality indicator
- Timestamp

Possible NMEA sentences to emulate or produce:

- `$GPGGA` / `$GNGGA` for fix data
- `$GPRMC` / `$GNRMC` for recommended minimum navigation data
- `$GPVTG` / `$GNVTG` for course and speed

The generated NMEA stream should clearly distinguish estimated vision/map-derived position from true GNSS.

## Candidate Localization Approach

```text
Camera frames
  -> visual feature extraction / object recognition
    -> match against pre-installed map or landmark database
      -> estimate camera/drone pose
        -> fuse with IMU/barometer/optical flow/VIO if available
          -> produce geolocation estimate
            -> convert to NMEA-style output
```

## Map Inputs

Possible pre-installed map sources:

- Orthomosaic imagery
- Satellite imagery tiles
- Known building/landmark database
- Feature map generated before deployment
- Local mission map with georeferenced keypoints
- Floor plan or site map for indoor/special environments

## Low-Cost Sensor Bias

Prefer inexpensive modules first:

- Raspberry Pi camera module or similar low-cost camera
- Raspberry Pi 5 onboard compute
- Raspberry Pi AI HAT+ 2 only if needed
- Pixhawk IMU/barometer data through PX4
- Optional rangefinder for altitude correction

Avoid expensive modules unless benchmarks prove they are necessary:

- High-end GNSS/RTK
- Expensive LiDAR
- Premium stereo depth cameras
- Heavy onboard GPU modules

## Optical Flow Decision

Do not buy an optical flow sensor yet.

Optical flow can help with indoor or GNSS-denied velocity estimation, especially when paired with a downward rangefinder. However, the project direction is more flexible if the first GNSS-denied approach is based on ROS 2 computer vision, visual odometry, and map matching.

Possible later use cases for optical flow:

- Low-cost indoor hold
- Velocity stabilization near ground
- Backup velocity estimate when visual map matching is unavailable

## Simulation Milestones

1. Run PX4 SITL with Gazebo X500.
2. Add simulated camera/depth/vision models.
3. Publish camera frames into ROS 2.
4. Run object recognition on simulated imagery.
5. Create a small georeferenced test map.
6. Estimate drone pose from visual features and map matches.
7. Publish estimated pose as a ROS 2 topic.
8. Convert estimated pose into NMEA-style sentences.
9. Feed estimated pose into mission logic as GNSS-denied navigation input.
10. Validate against simulator ground truth.

## Hardware Milestones

1. Bench-test Raspberry Pi 5 camera capture.
2. Benchmark vision inference on Raspberry Pi 5 CPU/GPU path.
3. Benchmark Raspberry Pi AI HAT+ 2 if needed.
4. Connect Raspberry Pi 5 to Pixhawk 6X.
5. Stream PX4 telemetry into ROS 2.
6. Run perception on recorded datasets before live flight.
7. Test NMEA output on bench before flight.
8. Test localization outdoors with GNSS available as ground truth.
9. Test degraded/GNSS-denied behavior only after safety validation.

## Design Risks

- Vision-map matching may fail in repetitive, low-texture, dark, or changed environments.
- Pre-installed maps can become stale.
- Low-cost cameras may have motion blur or rolling-shutter artifacts.
- Raspberry Pi inference latency may be too high without acceleration.
- NMEA consumers may assume the stream is real GNSS unless metadata or sentence quality fields are handled carefully.

## Safety Rule

Map-derived NMEA should be treated as an estimated navigation source with confidence bounds. It should not silently replace GNSS in safety-critical logic until tested against ground truth and failure cases.
