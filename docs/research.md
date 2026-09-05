# Research-to-design traceability

This project uses research components as replaceable evidence sources, rather than relying on an end-to-end model as a sole flight authority. Reported results do not transfer automatically to a new camera, map epoch, altitude, or operating area; validate the complete stack on representative flights.

| Work | Role in this scaffold | Concrete integration rule |
| --- | --- | --- |
| [Bearing-UAV (CVPR 2026)](https://openaccess.thecvf.com/content/CVPR2026/html/Liu_Beyond_Matching_to_Tiles_Bridging_Unaligned_Aerial_and_Satellite_Views_CVPR_2026_paper.html) | Neighbor-aware absolute position + heading evidence beyond isolated tile matching | Add a `BearingUAVCorrelator` beside tile retrieval; convert its mean/covariance to a global factor only after temporal/geometric consistency. |
| [Video2BEV (ICCV 2025)](https://arxiv.org/abs/2411.13610) | Video-window BEV to reduce UAV/satellite viewpoint disparity | Implement `LocalBEVBuilder` from bounded keyframes; correlate its features continuously around active hypotheses. |
| [MASt3R-SLAM (CVPR 2025)](https://arxiv.org/abs/2412.12392) | learned 3D reconstruction prior for local dense SLAM | Put it behind `VisualOdometryFrontend`; use output as local factors/keyframes, not absolute location. |
| [VGGT (CVPR 2025)](https://openaccess.thecvf.com/content/CVPR2025/html/Wang_VGGT_Visual_Geometry_Grounded_Transformer_CVPR_2025_paper.html) | multi-view camera/depth/point-map prior | Run on short recovery/map-build windows; cache point tracks and retain calibration/geometry gates. |
| [TopicGeo (ICCV 2025)](https://openaccess.thecvf.com/content/ICCV2025/papers/Wang_TopicGeo_An_Efficient_Unified_Framework_for_Geolocation_ICCV_2025_paper.pdf) | semantic/topic-aware cross-view retrieval/matching | Add topic score as optional, low-weight evidence; do not substitute it for correspondence geometry. |
| [RoMa (CVPR 2024)](https://openaccess.thecvf.com/content/CVPR2024/papers/Edstedt_RoMa_Robust_Dense_Feature_Matching_CVPR_2024_paper.pdf) | dense, certainty-aware candidate verification | Call only after sparse shortlist; refit PnP/homography with certainty-weighted/selective matches. |
| [LightGlue (ICCV 2023)](https://github.com/cvg/LightGlue) | adaptive sparse local matching | First-stage correspondence gate and keyframe matching; enforce coverage/inlier gates before dense work. |
| [SALAD (CVPR 2024)](https://openaccess.thecvf.com/content/CVPR2024/papers/Izquierdo_Optimal_Transport_Aggregation_for_Visual_Place_Recognition_CVPR_2024_paper.pdf) | global VPR descriptor | Build offline tile embedding index and retrieve Top-K during startup/loss/recovery. |
| [MegaLoc (CVPRW 2025)](https://arxiv.org/abs/2502.17237) | unified retrieval alternative | A/B behind `GlobalRetriever`; compare recall, map-domain generalization, latency and memory on held-out routes. |
| [AirLock+ (WACV 2026)](https://openaccess.thecvf.com/content/WACV2026/html/Deng_AirLock_Scaling_UAV-to-Satellite_Image_Registration_for_Target_Geolocalization_and_Geospatial_WACV_2026_paper.html) | end-to-end UAV-to-satellite registration with temporal trajectory use | Use as a field-oriented registration baseline; retain homography validity/uncertainty checks and propagate only accepted observations. |

## Selection is configuration, not architecture

`configs/default.yaml` names the intended production choices. `adapters/models.py` intentionally leaves their heavyweight bindings unimplemented: checkpoints, preprocessing, numerical precision, TensorRT engines, and feature-store layouts must be pinned and benchmarked for the target hardware. This prevents hidden model imports from leaking into estimation or PX4 integration.

### Baseline experiment matrix

For each map epoch and flight condition, record Recall@K, time-to-first-global-fix, global pose/heading error, VO drift during loss, false-accept rate, covariance calibration, P50/P95 latency, power/thermals, and `NO_GLOBAL_FIX` correctness. Sweep illumination, season, altitude, roll/pitch/yaw, blur, occlusion, repetitive texture, emergency map staleness, and intentional candidate aliases. Require recovery safety behavior as well as localization accuracy.
