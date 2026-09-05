# Modern Stack Research: Decision Record

This record separates tools that fit the present project from interesting tools
that need more evidence. It supplements [Architecture Reassessment](architecture-reassessment-2026-08.md).

## Recommendations at a Glance

| Layer | Select now | Evaluate later | Do not make a default yet |
| --- | --- | --- | --- |
| Camera capture | Raspberry Pi OS libcamera + Picamera2 | direct libcamera C++ adapter | USB webcam/OpenCV capture as production source |
| Map raster | COG + GDAL/Rasterio/PyProj + STAC assets | DEM/3D landmark pipeline | loose PNG plus ad-hoc sidecar metadata |
| Map geometry | OpenCV robust PnP or envelope-limited homography | COLMAP/PyCOLMAP landmark compilation | monocular metric pose inferred from unconstrained homography |
| Pi features | XFeat + LighterGlue and robust geometry | AI HAT provider after parity/thermal evidence | ORB/AKAZE/SIFT as active fallback |
| Retrieval | bounded MegaLoc index plus FAISS-compatible metadata | alternate learned retrieval after held-out evaluation | whole-map brute force per frame |
| State estimation | bounded deterministic window with explicit covariance | native GTSAM/iSAM2 service | current deterministic fallback as flight estimator |
| PX4 | pymavlink `ODOMETRY` plus `TIMESYNC` and packet tests | MAVSDK for desktop convenience | custom GPS injection as PX4 default |
| Logs | JSON decision trace, optional MCAP | indexed remote evidence store | ROS bag as active dependency |
| Acceleration | CPU Pi baseline | Hailo AI HAT for proven model | accelerator-led architecture decision |

## Why These Choices

### COG, STAC, and navigation manifests

COG offers tiled/overview-backed access to large raster maps; GDAL provides a
supported COG driver and validation. STAC is a mature way to describe spatial
assets. Neither standard captures the map-to-camera calibration/model/index
compatibility this navigation stack needs, so `MapRelease` remains authoritative
for that information. This avoids the current duplicated `map.png`, loose
features, and partial JSON metadata being treated as equivalent artifacts.

### Desktop reconstruction versus Pi execution

COLMAP and PyCOLMAP are now accessible from Python and have wheels for the
desktop platforms. They belong in map production/replay, where the RTX 3060 can
build 3D landmarks and evaluate datasets. Their memory/GPU and setup profile do
not belong in the Pi flight service. Hierarchical Localization is similarly a
valuable offline benchmark/orchestration reference, rather than something to
copy wholesale into a low-latency Pi process.

### Retrieval and matching

FAISS is the appropriate reference for a versioned dense-vector map index. It
supports multiple speed/accuracy/memory trade-offs and GPU acceleration for map
build/evaluation. Its ARM packaging and Pi memory profile must be benchmarked;
the runtime should persist only a bounded index and Top-K active window. The
active learned local path is XFeat plus LighterGlue. ORB/AKAZE/SIFT remain
frozen offline baselines for replay comparisons, never automatic Pi fallback.

RoMa, Video2BEV, MASt3R-SLAM, VGGT, and semantic/topic retrieval are cloud
teacher or map-production adapters. They increase the risk of latency, opaque
failure modes, or incompatible map features, so the Pi receives only a
distilled, audited ONNX student and release-bound XFeat/MegaLoc assets.

### Estimator

GTSAM provides the mature factor-graph/iSAM2 reference implementation needed
for a later incremental graph. The boundary should remain typed factors and
`NavEstimate`; a native GTSAM layer can then be introduced without changing
the desktop, maps, or PX4 contract. Before that, a bounded and deterministic
estimator makes it possible to establish measurement/covariance semantics and
replay behavior without pretending the current test fallback is navigation.

### Camera and time

Raspberry Pi documents libcamera/rpicam as the supported camera stack and
Picamera2 as its Python API. It supports current Raspberry Pi camera modules,
including IMX296 global shutter and third-party OV9281-class sensors. Use its
metadata and an explicit timestamp conversion adapter. Treat focus, exposure,
blur, rolling/global shutter, crop/resize, and calibration identity as
first-class input quality data.

### Hailo AI HAT

Hailo provides Raspberry Pi 5 examples and Python access for accelerator
inference. This makes it a reasonable *optional* deployment target, not an
immediate navigation dependency. The initial mapping/localization path does not
have a proven Hailo model/export plan. Purchase or integrate it only after a
selected model, preprocessing, numerical precision, latency, heat, and CPU
fallback are demonstrated on the target Pi.

### PX4 transport and logging

PX4's external-position documentation requires deliberate EKF2 fusion,
timestamp delay, sensor offset, and frame configuration. MAVLink `ODOMETRY`
already contains the required pose, velocity, covariance, frames, reset counter
and quality fields, so a small direct adapter is easier to validate than an
extra abstraction. MCAP supports Python, TypeScript, Rust, and C++, making it
well aligned to Pi runtime plus Tauri review without reviving ROS as scaffolding.

## Research Questions That Must Be Answered With Hardware Evidence

1. Exact camera model, lens, shutter behavior, frame rate, fixed exposure and
   focus behavior at each planned altitude/speed.
2. Camera hardware timestamp availability and stable offset/drift against PX4
   boot time measured with `TIMESYNC`/packet capture.
3. Whether the selected map provides reliable 3D landmarks or only an
   operationally bounded plane model, and how metric scale is established.
4. Pi 5 P95/P99 capture, feature, retrieval, geometry, and estimator latency
   under temperature/vibration/storage load.
5. Global-retrieval cache size and recall trade-off for each actual map area.
6. PX4 version and exact external-vision parameter evidence for the Pixhawk
   firmware installed on the Holybro aircraft.
7. False global fix rate on held-out seasons, lighting, map age, repetitive
   terrain, and wrong-map replays.

## Source Notes

All links below are primary project or vendor documentation as of 2026-08-30.

- [Raspberry Pi camera stack](https://www.raspberrypi.com/documentation/computers/camera_software.html)
- [GDAL COG driver](https://gdal.org/en/stable/drivers/raster/cog.html)
- [STAC](https://stacspec.org/en/about/stac-spec/)
- [PyCOLMAP](https://colmap.github.io/pycolmap/index.html)
- [Hierarchical Localization](https://github.com/cvg/Hierarchical-Localization)
- [FAISS](https://github.com/facebookresearch/faiss)
- [GTSAM](https://github.com/borglab/gtsam)
- [PX4 external position](https://docs.px4.io/main/en/ros/external_position_estimation)
- [MAVLink ODOMETRY](https://mavlink.io/en/messages/common.html#ODOMETRY)
- [MCAP](https://github.com/foxglove/mcap)
- [Hailo Raspberry Pi 5 examples](https://github.com/hailo-ai/hailo-rpi5-examples)
