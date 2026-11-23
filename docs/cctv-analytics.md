# CCTV analytics for Global View

Use the camera or recorder already on site as a video source. The Proxigo worker pulls an RTSP stream, decodes sampled frames, runs a detector and per-camera tracker, and sends rule matches to the site gateway. Supervisors select where each camera is processed. Global View uses the same published rules, camera permissions, incident replay, and notification outbox in either placement.

**Ground analytics** cover people and road vehicles, including bicycles. A rule can watch a directed tripwire, detect entry into an image region, measure dwell, or detect a stopped vehicle. These are defined behaviors with inspectable thresholds. Tracking does not assign a person's identity or carry identities between cameras.

**Drone analytics** produce possible visual sightings. Rules require an image region, a minimum box size, a confidence floor, and repeated detection. On the map, the incident is located at the observing camera and linked to the selected boundary. A single camera image supplies a viewing direction and image coordinates. It does not establish the drone's world position, altitude, or range.

## Integration choices

| Layer | Pilot implementation | Alternative or next comparison |
| --- | --- | --- |
| Installed video | Read-only RTSP from a camera, a recorder channel, or an approved site restreamer. An analog camera needs a network encoder or DVR output. FFmpeg uses TCP RTSP and drops unread live frames when inference lags. | Use ONVIF Profile T for compatible devices and discovery/configuration. Profile M is useful when a device already generates metadata. Require no analytics support on the source camera. |
| People and vehicles | YOLOX-Nano ONNX at 416 × 416 on CPU. The worker decodes the original author export and maps the COCO classes used by site rules. | Measure YOLOX-S, RTMDet-Tiny, and RT-DETR on the same held-out site clips if small or partially occluded people are missed. |
| Tracking and irregularities | Short-lived, per-category tracks using box overlap, predicted position, and an association distance gate. Compare each track against an image-space line or polygon. Confirmation resets on a gap, rule republish, or loss of qualification. | Compare ByteTrack on dense scenes and occlusions. Review ID switches and fragmentation along with alert quality. Use HOTA or IDF1 when track labels exist. |
| Drone candidates | OWLv2 Base Patch16 Ensemble with drone prompts and contrast classes for birds, airplanes, helicopters, and kites. The worker can also load a locally supplied, class-mapped YOLOX ONNX drone model. | Test 2 × 2 overlapping crops for small targets. Benchmark and fine-tune a dedicated RGB detector with documented data and weight terms. Test separate models for visible and thermal feeds. |
| Edge compute | Start on one available machine with CPU inference and measure actual sampling rate. Each camera has an independent worker, with separate ground and drone inference loops sharing one latest-frame decoder. | On Intel hardware benchmark OpenVINO and media decode. At larger stream counts compare CUDA/TensorRT or NVIDIA DeepStream batching and hardware decode. Size hardware from the site's measured workloads. |
| Alerts and logs | Publish an image rule and assign it to one existing boundary. Observe mode stores detections for review. Alert mode creates the existing site incident and signed notification when configured. | Keep new rules in Observe during calibration. Compare the alerts against officer review and site policy before promotion. |

Stream integration and runtime documentation. [FFmpeg RTSP](https://ffmpeg.org/ffmpeg-protocols.html#rtsp) · [ONVIF Profile T](https://www.onvif.org/profiles/profile-t/) · [ONVIF Profile M](https://www.onvif.org/profiles/profile-m/) · [ONNX Runtime execution providers](https://onnxruntime.ai/docs/execution-providers/) · [OpenVINO inference devices](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes.html) · [DeepStream analytics](https://docs.nvidia.com/metropolis/deepstream/9.2/text/DS_plugin_gst-nvdsanalytics)

Research and model sources. [YOLOX paper](https://arxiv.org/abs/2107.08430) · [YOLOX author ONNX export](https://github.com/Megvii-BaseDetection/YOLOX/tree/main/demo/ONNXRuntime) · [RTMDet](https://github.com/open-mmlab/mmdetection/tree/main/configs/rtmdet) · [RT-DETR](https://github.com/lyuwenyu/RT-DETR) · [ByteTrack](https://arxiv.org/abs/2110.06864) · [HOTA](https://arxiv.org/abs/2009.07736) · [OWLv2 paper](https://arxiv.org/abs/2306.09683) · [OWLv2 Apache-2.0 model card](https://huggingface.co/google/owlv2-base-patch16-ensemble) · [SAHI small-object study](https://arxiv.org/abs/2202.06934)

## Pilot workflow

1. Inventory each existing stream. Record codec, resolution, frame rate, field of view, fixed or PTZ behavior, network owner, recording coverage, and allowable concurrent sessions. Use a substream for ground analytics when it preserves sufficient detail. Use a higher-resolution fixed sky view for the drone pilot. Change PTZ presets only with the corresponding image rule calibration.
2. Register the camera in Global View and assign it to a site boundary. Use **Posture → Site configuration and versions** to open a draft. Under **Devices → Edit device → CCTV analytics rules**, add a rule, select its boundary, and place its line or polygon in image coordinates. An optional reference image stays in the browser. Match it to the deployed camera stream and crop. Save the device, save the shared draft, and publish.
3. Use **Add exclusion zone** to mask up to four regions per rule. Both time profiles share the same masks. A person or vehicle qualifies by the bottom center of its box, and a possible drone qualifies by its box center. Entering an exclusion resets confirmation. A tripwire also checks the crossing point, including when sampled positions fall on either side of a narrow mask. Use **Use day and night profiles** to configure separate confidence, confirmation time, and minimum object size. Each profile applies from its start time until the other starts. The site time zone is saved in each published schedule, and confirmation starts over when the active profile changes.
4. Select **Analytics compute** in **CCTV analytics rules** and start with **Observe only**. Cloud is the default for the first rule on a camera. Choose **On-premises** for local inference. Enroll the matching worker and publish the site draft. Existing analytics configurations keep their current worker until a supervisor changes the placement.
5. Check **Posture → CCTV analytics** for the published placement, worker status, inference rate, model fingerprint, and queued events. The Observe mode review queue filters by camera, source, decision, and published configuration. Select a detection to look up the associated gateway recording and record a decision with an optional note. Reviews are shared and audited. Viewer accounts can read reviews for their assigned cameras. Use **Unable to verify** when the evidence is insufficient.
6. Open **Observe rule report** in the same section to compare review counts by rule and profile for the current or a prior published version. Live CCTV and replay have separate totals. Live mean gateway delivery covers observations with usable timestamps, measured from worker frame receipt to gateway receipt, including retries. Select **Refresh report** after recording new reviews. The report covers collected Observe candidates. Review independently sampled footage to measure missed events.
7. Promote qualified rules to **Create site alerts** and publish. Use Alerts and replay to inspect the model, rule, object class, score, camera, configuration version, and active profile. A score comes from the detector and is not a calibrated probability of a security incident.
8. Treat a possible drone sighting as an operator assessment. Use the recorded video and any independent approved source to corroborate it. The initial integration does not estimate world position from a box or automatically combine visual and RF reports.

## Compute placement and video access

| Placement | Video path | Gateway path |
| --- | --- | --- |
| Cloud (default) | A worker on a private cloud network reads an approved camera or recorder stream over an encrypted site-to-cloud connection. Decoded frames are held in worker memory for inference. | The worker fetches policy and sends rule matches to the site's HTTPS gateway endpoint. |
| On-premises | A worker on the customer network reads a site-local stream and processes it on the local host. | The worker communicates with the site-local gateway. Analytics can continue during an internet outage while the local stream and gateway remain reachable. |

The worker sends bounding boxes, class, confidence, model fingerprint, anonymous track ID, rule ID, and frame-receipt time. Model files are loaded locally and the worker opts out of ONNX Runtime telemetry before initialization. Apply outbound network rules for the approved stream and gateway in either placement. Gateway records attribute each live match to the authenticated compute placement. Existing browser playback, recording export, and optional telemetry forwarding have their own access and retention settings. [ONNX Runtime privacy controls](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md#disabling-telemetry)

Enroll one token per camera and placement. The published configuration grants policy and heartbeat access to the selected worker. The worker stops reading the video if authorization is revoked or if it cannot refresh the policy lease within 15 seconds. The gateway checks queued matches against the placement that was published when they occurred. A local SQLite outbox retries accepted-format metadata with the same event ID. Use a separate outbox for each camera and placement. Outboxes hold up to 10,000 events. Synchronize worker and gateway clocks.

For a cloud deployment, use a private subnet with routing to the approved stream through a site VPN or equivalent encrypted link. Restrict cloud network access to the approved video channel and the private gateway origin, and use a read-only recorder or stream credential. Bind the site gateway to loopback behind TLS termination and allow the cloud worker to reach only its camera's `/api/cameras/<camera-id>/analytics` endpoint and `POST /api/site/telemetry`. Use a certificate trusted by the worker. Keep camera and recorder endpoints on private networks. Cloud provisioning and network policies belong to the deployment environment.

To move a camera on-premises, provision its local worker and separate token, publish **On-premises**, and verify the new worker in Posture. Remove the cloud worker's network access to that stream and revoke its stream credential to enforce the media boundary. Analytics remains on-premises until a supervisor publishes another placement. There is no automatic cross-placement failover.

## Development and deployment

Use the [cloud pilot runbook](cctv-cloud-pilot.md) for the VM comparison and the Docker Compose worker setup.

Use Python 3.12 or another Python version with wheels for the chosen backends. Install FFmpeg and set up dependencies outside the repository. For a named site time zone such as `America/Los_Angeles`, the gateway and worker need an IANA time zone database. Install Python's `tzdata` package in either environment if its host lacks one.

```bash
python3.12 -m venv ~/.local/share/proxigo/cctv-venv
~/.local/share/proxigo/cctv-venv/bin/python -m pip install \
  -r services/camera-gateway/video_analytics/drone-requirements.txt
```

For a ground-only installation, use the `requirements.txt` in that directory. The worker opens model assets from disk without fetching them at runtime. Obtain the author-released `yolox_nano.onnx` and the model and processor files at the OWLv2 revision below. Review checkpoint and dataset terms when selecting an alternate artifact. COCO annotator terms and each source image's terms are separate. [YOLOX Apache-2.0 license](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/LICENSE) · [COCO terms](https://cocodataset.org/dataset/termsofuse.htm) · [Pinned OWLv2 revision](https://huggingface.co/google/owlv2-base-patch16-ensemble/tree/cfd3195ba4ea9592eec887ded089f4c08eff231d)

Start the site gateway with `--site-access /private/site.json --analytics-workers /private/analytics-workers.json`. Generate an independent token with `openssl rand -hex 32` for each enrolled placement. Save tokens under their exact Global View camera IDs. For example, the enrollment file for a cloud camera and a local camera has this structure.

```json
{
  "north-yard-camera": { "cloud": "<64 hex characters>" },
  "server-room-camera": { "on-premises": "<different 64 hex characters>" }
}
```

Write the appropriate token alone to the worker's token file. Keep credentials and the private RTSP URL in restricted operator-managed files. Existing workers using `--telemetry-devices` can keep running for cameras with the existing placement until they are moved to a named pipeline.

Example private worker configuration for a camera that sees both ground and sky. Leave out an unused model lane. Choose output dimensions matching the source aspect ratio and adjust rates and bounds using the field test.

```json
{
  "sensorId": "north-yard-camera",
  "placement": "cloud",
  "source": "rtsp://recorder.internal:554/channel/substream",
  "gateway": "https://analytics-gateway.internal",
  "tokenFile": "/private/north-yard-camera.token",
  "state": "/state/north-yard-cctv.sqlite3",
  "width": 1280,
  "height": 720,
  "fps": 6,
  "models": {
    "ground": {
      "kind": "yolox",
      "path": "/models/yolox_nano.onnx",
      "sha256": "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d",
      "fps": 4
    },
    "drone": {
      "kind": "owlv2",
      "path": "/models/owlv2-base-patch16-ensemble",
      "sha256": "e1e130b9e404cf91a75ad45644c1da9d7fa5284085eecc864266a6923efb99e7",
      "fps": 0.3
    }
  }
}
```

For on-premises, set `"placement": "on-premises"`, use the site-local RTSP URL, and set the local gateway origin. A colocated worker can use `http://127.0.0.1:8765`. Use a private HTTPS gateway for workers on a separate host. The worker can start before publication and waits for an active policy. Run a native installation under a service supervisor and point `state` at a writable private directory.

```bash
~/.local/share/proxigo/cctv-venv/bin/python services/camera-gateway/analytics_worker.py \
  --config /private/north-yard-camera.json
```

The same container image runs on a cloud host or an on-site server. The default image contains the ground models' runtime dependencies. Add `--build-arg ANALYTICS_REQUIREMENTS=drone-requirements.txt` to the build for OWLv2. Model files and deployment secrets are mounted at runtime. The container runs as UID 10001, so grant that account access to the private input files and read-only model files. Configure private routing and DNS on the host network before starting it. Inside a container, use a reachable private HTTPS gateway origin.

```bash
docker build -f docker/cctv-analytics/Dockerfile -t proxigo-cctv-analytics .
docker run -d --name north-yard-analytics --restart unless-stopped \
  --read-only --tmpfs /tmp --cap-drop=ALL --security-opt=no-new-privileges \
  --mount type=bind,src=/private/proxigo/yard,dst=/private,readonly \
  --mount type=bind,src=/private/proxigo/models,dst=/models,readonly \
  --mount type=volume,src=proxigo-north-yard-state,dst=/state \
  proxigo-cctv-analytics --config /private/north-yard-camera.json
```

For replay, save the deployed camera policy as a JSON file with `version` and `rules`. Each rule has `id`, `name`, `kind`, `object`, `mode`, `boundaryId`, `points`, `direction`, `minimumScore`, `minimumPixels`, and `seconds`. Include `exclusions` as an array of image polygons and `schedule` with `dayStartsAt`, `nightStartsAt`, and `night` thresholds for rules that use them. Set the policy's `timeZone` to the published site time zone. The `--replay` command takes a local video and prints JSONL observations without connecting to the gateway. Supply `--start-at` with the first frame's timestamp and time zone offset to compare profiles on a fixed clip. The optional detection trace includes per-frame boxes and elapsed video seconds. The gateway treats submitted observations marked as replay as Observe evidence and validates them against the policy active at their occurrence time.

```bash
~/.local/share/proxigo/cctv-venv/bin/python services/camera-gateway/analytics_worker.py \
  --config /private/north-yard-camera.json --replay /private/clip.mp4 \
  --policy /private/camera-rules.json --detections /private/boxes.jsonl \
  --start-at 2026-11-23T18:00:00-08:00
```

## Bench evidence before acquiring cameras

The initial bench ran on an Apple M1 Pro with CPU inference and two threads per detector, using public media and local replay.

| Sample | Result | Scope |
| --- | --- | --- |
| CC0 street photo looped into 3 seconds of video | YOLOX processed 12 sampled frames at about 18 ms per frame and emitted three person entries and four vehicle stops in the fixture. | Exercises inference, track confirmation, rules and metadata. The source is one still photo, so it does not estimate moving-target accuracy. |
| CC0 fixed-camera pedestrian video from Taurus Media Technik | YOLOX processed 90 sampled frames at about 17 ms per frame and emitted 11 track-level crossings of a center tripwire. | Exercises moving people and image-space track association. Counts are detector output from one unlabelled sequence. [Pedestrian sample](https://commons.wikimedia.org/wiki/File:Video_Codec_Test_pedestrian_area_1080p25.y4m.webm) |
| Halmstad visible clip `V_DRONE_017` | OWLv2 proposed a drone in 20 of 22 sampled images, emitted two track candidates, and averaged about 2.75 s per frame. Against the source annotations, 5 of 22 sampled predictions reached IoU 0.30 and 0 reached IoU 0.50. | The clip supports testing sightings while showing limited box agreement. The result does not establish range or operational sensitivity. |
| Halmstad visible clip `V_BIRD_001` | OWLv2 generated zero drone candidates across 20 sampled frames at the same threshold. | One negative clip. Measure false alarms over extended negative footage before enabling notifications. |
| CC0 near-drone photo looped into 3 seconds of video | OWLv2 proposed the drone in all six sampled frames and emitted one candidate event. | Repeatable stream-path fixture for Chrome. |

The drone replays used a confidence floor of 0.08 and a longest-side floor of 8 pixels. The Halmstad box comparison matched FFmpeg's 2 fps samples to original frame checksums before looking up the labels. The published UI defaults start at 0.15 and 12 pixels for drone rules. Tune those values for the actual scene. [Street sample](https://commons.wikimedia.org/wiki/File:People_walking_across_urban_street_(Unsplash).jpg) · [Near-drone sample](https://commons.wikimedia.org/wiki/File:Quadcopter_camera_drone_in_flight.jpg) · [Halmstad data and CC0 terms](https://github.com/DroneDetectionThesis/Drone-detection-dataset/tree/442d0c6750708cc221636f7a1fd31cbd8cb0aeb8) · [Halmstad paper](https://arxiv.org/abs/2111.01888)

The demo asset setup stages the licensed sources and pinned model files outside the checkout. It downloads about 610 MB and requires cURL and FFmpeg. The Chrome test uses MediaMTX to serve continuous RTSP streams from the two reference images and uses OpenSSL to generate a test certificate for the cloud uplink. It runs YOLOX and OWLv2 inference against an isolated gateway.

```bash
python3 scripts/dev/prepare_cctv_analytics_demo.py /tmp/proxigo-cctv-demo
MEDIAMTX=/path/to/mediamtx \
PROXIGO_CCTV_PYTHON="$HOME/.local/share/proxigo/cctv-venv/bin/python" \
PROXIGO_CCTV_YOLOX=/tmp/proxigo-cctv-demo/yolox_nano.onnx \
PROXIGO_CCTV_OWLV2=/tmp/proxigo-cctv-demo/owlv2 \
PROXIGO_CCTV_GROUND_IMAGE=/tmp/proxigo-cctv-demo/street.jpg \
PROXIGO_CCTV_DRONE_IMAGE=/tmp/proxigo-cctv-demo/near-drone.jpg \
npm --prefix desktop-app run test:e2e -- cctv-analytics.spec.ts
```

## Score a labeled entrance tripwire

The public MEVA fixture runs the current worker on camera G329. It pins the source video, the Geo/Types/Activities label files, and the author-released model files by SHA-256. The first run downloads about 170 MB. The script requires FFmpeg, cURL, the ground worker's Python dependencies and PyYAML.

```bash
~/.local/share/proxigo/cctv-venv/bin/python -m pip install PyYAML
~/.local/share/proxigo/cctv-venv/bin/python scripts/dev/benchmark_cctv_meva.py /tmp/proxigo-meva
```

The benchmark derives one directed crossing from the labeled person track, then compares YOLOX-Nano and Tiny on the same eight-second excerpt. It aligns worker samples to source frames and records each match, miss, duplicate, and unreviewed event in `meva-report.json`. The report and per-model JSONL traces are saved next to the downloaded assets. A match requires a detection within one second of the reference crossing and at least 0.30 box IoU at the sampled frame. The reference crossing is `4.517` seconds into the excerpt.

In the initial 4 fps run both models missed the crossing. Each emitted a person detection before or after the threshold, with no candidate through the crossing. This fixture is one labeled positive with partial activity annotation. Whole-scene false-alarm rates require independently reviewed footage. [MEVA dataset by Kitware and IARPA, CC BY 4.0](https://mevadata.org/) · [Public annotation format](https://mevadata.org/resources/README-meva-kf1-data.html)

## Compare drone detector settings

Generate one replay trace per setting from the same source file. Use the same decode rate, rules, and capture floor. For the crop comparison, set the drone model's `tiles` to `1` for full-frame inference or `2` for overlapping crops. Recent traces record decode rate, capture floor, and processing time.

The evaluator uses FFmpeg frame checksums to align traces to the original clip, keeps frames shared by every trace, and excludes frames with ambiguous labels. It reports matched and missed frames at each score and overlap threshold, matches by annotated object size, and frames with candidates on a negative clip. When traces include timing, the evaluator also reports mean processing time across the shared frames.

Install `mcos-decoder` into the analytics virtual environment before running the annotated Halmstad comparison.

```bash
~/.local/share/proxigo/cctv-venv/bin/python scripts/dev/evaluate_cctv_replay.py \
  --video /tmp/proxigo-cctv-demo/drone-017.mp4 \
  --halmstad-labels /tmp/proxigo-cctv-demo/drone-017.mat \
  --fps 2 --captured-at 0.08 --score 0.15 0.55 0.75 \
  /tmp/proxigo-cctv-demo/drone-full.jsonl /tmp/proxigo-cctv-demo/drone-tiled.jsonl
```

Use `--negative-clip` in place of `--halmstad-labels` when the clip contains no drone. On six common samples from `V_DRONE_017`, both settings matched 2 of 6 annotated boxes at IoU 0.30 and 0 of 6 at IoU 0.50. Both settings produced zero candidates on five common samples from `V_BIRD_001`. The tiled replay averaged approximately 11 seconds per frame on the M1 Pro CPU, compared with approximately 2.75 seconds in the full-frame run. Full-frame remains the default for this pilot. These clips are a paired smoke test. [Halmstad dataset](https://github.com/DroneDetectionThesis/Drone-detection-dataset) · [Slicing Aided Hyper Inference](https://arxiv.org/abs/2202.06934)

## Qualification before customer alarms

- **Ground lane.** Collect authorized footage covering arrivals, departures, legitimate stops and dwell, occlusion, headlights, reflections, forklifts, bicycles, weather, compression, and night scenes. Keep entire sessions from separate cameras out of training and threshold selection. Report precision and recall for each event type, false alarms per camera-hour, missed short events, and time from action to incident. Compare a lightweight tracker against ByteTrack only where occlusion and ID switches are causing operator-visible misses or duplicates.
- **Drone lane.** Use Halmstad visible clips for initial comparisons and split by session and location. Add bird-only, airplane, helicopter, insects, changing clouds, and glare. Drone-vs-Bird Challenge material requires its data-use agreement. DUT Anti-UAV can add visible-scene diversity once its use terms and provenance are confirmed. A dataset recorded *from* a drone, such as VisDrone, serves ground-object research and does not directly benchmark spotting drones in the sky. [Drone-vs-Bird research and access](https://pmc.ncbi.nlm.nih.gov/articles/PMC8072977/) · [DUT Anti-UAV](https://github.com/wangdongdut/DUT-Anti-UAV) · [VisDrone benchmark](https://arxiv.org/abs/2001.06303)
- **Drone optics.** Measure recall by apparent size in native pixels, for example under 12, 12–23, 24–47, and 48+ pixels. Record camera resolution, horizontal field of view, zoom, exposure, and lighting. Wider views give more sky coverage and fewer pixels on a distant target. Measure what the installed stream resolves before expecting a reliable result from a different model.
- **Operations.** Agree on acceptable incident recall, false alarms per camera-hour, latency, and acceptable uncertainty with the pilot customer before moving a rule from Observe to Alert. Include browser-closed incidents, recorder alignment, reconnects, publication during live inference, and temporary gateway loss. Use status and delivery measurements to size compute per site. Require a new acceptance run if the camera moves, the stream changes crop or resolution, or the model fingerprint changes.
