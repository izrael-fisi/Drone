# CCTV analytics for Global View

Use the camera or recorder already on site as a video source. The Proxigo worker pulls an RTSP stream, decodes sampled frames, runs a detector and per-camera tracker, and sends rule matches to the site gateway. Supervisors select where each camera is processed. Global View uses the same published rules, camera permissions, incident replay, and notification outbox in either placement.

**Ground analytics** cover people and road vehicles, including bicycles. A rule can watch a directed tripwire, detect entry into an image region, measure dwell, or detect a stopped vehicle. These are defined behaviors with inspectable thresholds. Tracking does not assign a person's identity or carry identities between cameras.

Choose **Time in zone** in a camera's CCTV rule editor to set the required time inside its detection region. **Maximum detection gap** allows up to one second between qualifying observations when detection is interrupted. The timer pauses during the gap and can resume only when the same track reconnects nearby inside the region without crossing an exclusion. A longer gap restarts the timer. The gateway records the observed duration and shows it in the incident details. Existing rules have a zero-second gap until edited. Update the analytics worker before publishing a rule that uses gap tolerance.

**Drone analytics** produce possible visual sightings. Rules require an image region, a minimum box size, a confidence floor, and repeated detection. On the map, the incident is located at the observing camera and linked to the selected boundary. A single camera image supplies a viewing direction and image coordinates. It does not establish the drone's world position, altitude, or range.

## Integration choices

| Layer | Pilot implementation | Alternative or next comparison |
| --- | --- | --- |
| Installed video | Read-only RTSP from a camera, a recorder channel, or an approved site restreamer. An analog camera needs a network encoder or DVR output. FFmpeg uses TCP RTSP and drops unread live frames when inference lags. | Use ONVIF Profile T for compatible devices and discovery/configuration. Profile M is useful when a device already generates metadata. Require no analytics support on the source camera. |
| People and vehicles | YOLO11s with a static 640 × 640 ONNX export and per-camera tracking. The pilot starts with a 4 fps target per camera. | Compare candidate exports on the same held-out clips, scoring the boxes and the resulting incidents. |
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

### Pilot detector and alternate models

- **YOLO11s at 640 × 640.** Set the ground model kind to `yolo11`, provide a local static FP32 COCO ONNX detection export without embedded NMS, and pin its SHA-256. The worker uses ONNX Runtime with two inference threads by default. A twelve-camera site targets 48 analyzed frames per second across its workers. A native macOS worker can select `"provider": "CoreMLExecutionProvider"` when the installed ONNX Runtime supports it. [Per-camera cloud deployment](../infra/ecs-cctv/README.md#commission-twelve-cameras) · [CoreML provider](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html) · [YOLO11 licensing](https://www.ultralytics.com/license)
- **Legacy YOLOX.** Existing workers can load their previously selected Nano or S ONNX checkpoint and remain available for replay comparisons.
- **Targeted OWLv2.** A worker with the optional OWLv2 dependencies can set `models.ground.kind` to `owlv2`, `path` to the local model directory, and `sha256` to the pinned weights digest. It starts at 0.5 inference fps unless configured. Use the same worker configuration on an on-site server or a separately provisioned cloud host. Set `models.ground.device` to `cuda` on a host with a compatible Torch build and GPU. [OWLv2 paper](https://arxiv.org/abs/2306.09683) · [Fargate task limits](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html)

On a macOS host with CoreML, set `models.ground.coremlCacheDirectory` to an absolute directory such as `/Users/operator/Library/Caches/ProxigoCCTV`. Workers running under the same account can share the directory. Each approved ONNX digest and operating-system and runtime version receives its own private cache. A file lock serializes model loading for each cache. The directory holds compiled models. Camera footage and observation delivery use their existing paths. The local probe with ONNX Runtime 1.32.0 measured about 2.3 seconds with an empty cache and 0.14 seconds when reusing it, with byte-identical outputs for the probe input. [ONNX Runtime CoreML model cache](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html#available-options-new-api)

Use Python 3.12 or another Python version with wheels for the chosen backends. Install FFmpeg and set up dependencies outside the repository. For a named site time zone such as `America/Los_Angeles`, the gateway and worker need an IANA time zone database. Install Python's `tzdata` package in either environment if its host lacks one.

```bash
python3.12 -m venv ~/.local/share/proxigo/cctv-venv
~/.local/share/proxigo/cctv-venv/bin/python -m pip install \
  -r services/camera-gateway/video_analytics/drone-requirements.txt
```

For a ground-only installation, use the `requirements.txt` in that directory. The worker opens model assets from disk. Stage the approved YOLO11s export and, for drone rules, the model and processor files at the pinned OWLv2 revision. COCO annotator terms and each source image's terms are separate. [COCO terms](https://cocodataset.org/dataset/termsofuse.htm) · [Pinned OWLv2 revision](https://huggingface.co/google/owlv2-base-patch16-ensemble/tree/cfd3195ba4ea9592eec887ded089f4c08eff231d)

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
      "kind": "yolo11",
      "path": "/models/yolo11s.onnx",
      "sha256": "<verified SHA-256 of the approved export>",
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

Set `PROXIGO_CCTV_YOLO11` to the approved ONNX export to run the yard stream with YOLO11 and person track support. Both Chrome workflows accept `PROXIGO_CCTV_YOLO11_PROVIDER` for the local ONNX execution provider. The default is `CPUExecutionProvider`. On an Apple Silicon test host with the corresponding runtime support, set `PROXIGO_CCTV_YOLO11_PROVIDER=CoreMLExecutionProvider` and `PROXIGO_CCTV_OWLV2_DEVICE=mps`.

## Score labeled CCTV clips

The MEVA benchmark uses eight-second excerpts from three fixed cameras. It pins the source videos, the Geo/Types/Activities labels, and the author-released models by SHA-256. The script requires FFmpeg, cURL, the ground worker's Python dependencies, and PyYAML. [MEVA dataset, CC BY 4.0](https://mevadata.org/) · [Annotation format](https://mevadata.org/resources/README-meva-kf1-data.html)

```bash
cctv_python="$HOME/.local/share/proxigo/cctv-venv/bin/python"
"$cctv_python" -m pip install PyYAML
for camera in g329 g326 g328; do
  "$cctv_python" scripts/dev/benchmark_cctv_meva.py /tmp/proxigo-meva \
    --case "$camera" --models yolo11s --fps 4 \
    --yolo11 "$PROXIGO_CCTV_YOLO11" --yolo11-sha256 "$PROXIGO_CCTV_MODEL_SHA256"
done
```

G329 and G326 score a directed entrance crossing and aligned person boxes. G328 scores boxes for the labeled stopped vehicle. The script writes `meva-report.json` and per-model observations and box traces. Box matches require at least 0.30 intersection-over-union. Crossing matches also require an observation within one second of the reference time. Use `--fps 8` to compare a different sampling rate.

### Expanded MEVA evaluation corpus

The [corpus manifest](../tests/fixtures/cctv-meva-corpus.json) pins the Kitware evaluation annotations and 25 source recordings by SHA-256. The recordings total about 125 minutes from 24 camera views on seven dates. The benchmark selects up to 24 published actor activities per recording, interleaves activity categories, and scores five exact source frames per case. The first run evaluated 431 person cases and 139 vehicle cases across 14 activity categories. Box matches require a matching class, confidence of at least 0.25, and intersection-over-union of at least 0.50. Each detection can match one reference in a frame. Activity names identify the scenes used to test person and vehicle detection. [MEVA evaluation annotations and license](https://mevadata.org/#getting-data)

```bash
"$cctv_python" scripts/dev/benchmark_cctv_meva_corpus.py /tmp/proxigo-meva-corpus \
  --model "$PROXIGO_CCTV_YOLO11" --sha256 "$PROXIGO_CCTV_MODEL_SHA256" \
  --provider CoreMLExecutionProvider
```

The first run downloads about 4 GB to the external cache. Use `CPUExecutionProvider` on hosts without CoreML. Use `--clip` with a clip ID from the report to rerun a recording. The report includes per-case reference boxes and outcomes, totals for each activity, and the number of unique frames used for inference.

| Full-frame YOLO11s at 960-pixel decode width | Result |
| --- | ---: |
| Person boxes matched | 870 / 2,155 (40.4%) |
| Vehicle boxes matched | 494 / 695 (71.1%) |
| Cases with at least three of five boxes matched | 284 / 570 |
| Cases with zero boxes matched | 178 / 570 |
| Attributed forward crossings | 49 / 119 (41.2%) |
| Attributed wrong-direction reports | 1 / 119 negative controls |

The crossing test uses entrance and exit tracks that show a continuous passage across a line derived from the annotation. It runs the worker's tracker on frames sampled at approximately 4 fps and checks both directions on the same track. An observation must overlap the reference actor by at least 0.30 intersection-over-union. The run also produced two duplicate forward reports and 45 observations that could not be attributed to the reference actor. The paired controls measure wrong-direction reports for the labeled actor. Continuous false-alert rates require separately reviewed footage. [Reviewed timeline method](#continuous-doorway-replay)

### Four-model public replay at 4 fps

| Model | G329 person boxes and crossing | G326 person boxes and crossing | G328 vehicle boxes |
| --- | --- | --- | --- |
| YOLOX-Nano | 2/8, missed | 9/10, matched | 7/7 |
| YOLOX-S | 5/8, missed | 10/10, matched | 7/7 |
| YOLO11n | 5/8, missed | 10/10, matched | 7/7 |
| YOLO11s | 7/8, matched | 10/10, matched | 7/7 |

The YOLO11 models ran in a standalone public-data evaluation using Ultralytics 8.3.252 and the v8.3.0 checkpoints. The evaluation exported boxes at a 0.25 confidence floor and replayed them through the same tracker and entrance rules as YOLOX. YOLO11s detected the G329 crossing 0.23 seconds after its reference time. At 8 fps it matched 12 of 15 G329 person boxes and detected the same crossing. YOLOX-Nano and YOLOX-S missed the crossing at both rates. These three short clips have partial activity labels. Production decisions need additional camera views, night scenes, and independently reviewed negative footage. YOLO11 was evaluated offline. Confirm applicable Ultralytics terms before integrating it into a proprietary worker. [YOLO11 checkpoints](https://github.com/ultralytics/assets/releases/tag/v8.3.0) · [Ultralytics licensing](https://www.ultralytics.com/license)

Targeted OWLv2 averaged 3.2 seconds per frame in the separate G329 CPU replay and matched zero of eight person boxes at the same confidence floor. It produced vehicle observations in the street smoke test. Evaluate it against a specific object type and operating condition before assigning it to a camera.

The integrated YOLO11s ONNX worker reproduced the YOLO11s row above in all three replays. Use `--models nano small` for the YOLOX baselines.

### Crossing accuracy near tripwire endpoints

The worker locates a crossing using the consecutive qualifying observations that straddle the line. It waits for the track to clear the far side of the confirmation buffer, discards the pending intersection if the track returns to its original side, and checks the line endpoints, direction, duration, and exclusions before reporting. [Axis detection anchors](https://help.axis.com/en-us/axis-object-analytics) · [NVIDIA finite line crossing](https://docs.nvidia.com/metropolis/deepstream/text/DS_plugin_gst-nvdsanalytics.html)

With YOLO11s at 8 fps, the G329 crossing location error dropped from 21.58 to 18.23 source pixels. The G326 error dropped from 7.33 to 4.12 source pixels. Both full-length rules reported the labeled crossing once. Shortening the G326 test wire to end at normalized y = 0.99 excludes the labeled path, which crosses at y = 0.996. The previous estimate yielded one alert in that test and the new estimate yielded zero. The benchmark reports the normalized `expectedCrossingPoint` and `crossingLocationErrorSourcePixels` for matched crossings. [MEVA entrance annotations](https://mevadata.org/resources/README-meva-kf1-data.html)

### Twelve-camera load test

The test loops the labeled G326 excerpt on twelve distinct RTSP paths, starts twelve real YOLO11s workers with separate credentials, and connects Chrome to the site gateway. It starts workers in groups of three to limit concurrent model loading. With all twelve running together, it checks for a live crossing on each camera, at least 3.5 inference fps per camera over two heartbeat windows, and all twelve workers visible in Posture. Keep the public fixtures outside the checkout.

```bash
MEDIAMTX=/path/to/mediamtx \
PROXIGO_CCTV_PYTHON="$HOME/.local/share/proxigo/cctv-venv/bin/python" \
PROXIGO_CCTV_YOLO11=/path/to/yolo11s.onnx \
PROXIGO_CCTV_LOAD_VIDEO=/tmp/proxigo-meva/g326/g326-entrance.mkv \
npm --prefix desktop-app run test:e2e -- cctv-analytics.spec.ts --grep 'twelve-camera'
```

The test defaults to CPU, which needs enough capacity for twelve concurrent workers. Set `PROXIGO_CCTV_YOLO11_PROVIDER=CoreMLExecutionProvider` for a native Apple Silicon run. The fixture creates a shared compiled-model cache for the CoreML workers. The Playwright report attaches both sets of per-camera rates.

Set `PROXIGO_CCTV_YOLO11_CANARY` to a separate approved ONNX export to exercise a 12-camera rollout with one upgraded worker and a detail crop for people and vehicles. The test checks each camera's model fingerprint and live observations.
Set `PROXIGO_CCTV_ROTATED_CANARY=1` to send one sideways stream and exercise rotation correction, source-coordinate crossing, and the model fingerprint in Chrome. With both canary options enabled on the M1 Pro, eleven YOLO11s workers and one rotated YOLO11l detail worker all reported crossings. The per-camera rates were 3.96–4.10 fps across two windows. The rotated worker reported 3.99 fps in each window.
Set `PROXIGO_CCTV_YOLO11_DETAIL_POSE` to the approved pose ONNX export to exercise the optional person crop model on camera 12. With a 736 × 1280 YOLO11s full-frame export, a YOLO11l pose crop, and rotation enabled together, the camera reported 3.59 and 4.00 fps across two windows. Chrome confirmed all twelve live crossings and worker rows in Posture.
Set `PROXIGO_CCTV_RULE_DETAIL_CANARY=1` to derive the crop on camera 12 from its published tripwire. The test then publishes a full-frame zone and checks that both observations and Posture report the full-frame model fingerprint.
Set `PROXIGO_CCTV_RULE_DETAIL_CANARY=2` to publish a separated second region on camera 12. With CoreML on the M1 Pro, all twelve cameras reported live crossings. The two-crop camera ran at 4.10 and 3.90 inference fps in successive windows. Chrome showed all twelve cameras and confirmed the updated fingerprint after the full-frame rule was published.

Run the same command with `--grep 'rule detail follows'` to check a single camera's policy change in Chrome. The local CoreML run measured 4.05 inference fps with the derived crop and verified the switch to the full-frame fingerprint in live observations and Posture.

With the shared CoreML cache, all twelve square-input workers started and delivered a live crossing, including the camera with a rule-derived crop. At the end of the throughput window, the eleven full-frame workers reported 2.88–3.25 fps, and the crop worker reported 1.54 fps. That run missed the 3.5 fps requirement per camera. An experimental CoreML MLProgram GPU run exceeded its startup timeout, so the default execution mode was retained.

On an Apple M1 Pro using CoreML, all twelve workers delivered a live crossing and appeared in Posture. Two consecutive reporting windows measured 3.97–4.10 fps per camera and 47.84 and 48.16 fps across the site. The test uses short, looped footage on a local network. Measure sustained inference and incident delivery on the target customer hardware and private routes during commissioning.

The MEVA evaluation annotations provide other fixed-camera person and vehicle activities. VIRAT Ground offers additional person and vehicle activity under its dataset agreement. Use authorized footage from installed cameras for night qualification, including headlights, low-light noise, and infrared transitions. NightOwls and LLVIP publish annotated low-light data under noncommercial terms. [MEVA](https://mevadata.org/) · [VIRAT usage agreement](https://viratdata.org/resources/VIRAT-Video-Data-Set-Protection-Agreement-1-4-11.pdf) · [NightOwls terms](https://www.nightowls-dataset.org/download/) · [LLVIP terms](https://github.com/bupt-ai-cz/LLVIP)

### YOLO11s and YOLO11l comparison

An on-site worker can point its ground model configuration at an approved YOLO11l export and its digest. For cloud, build a YOLO11l worker image and pin it to selected cameras. Use the same clip, rate, rule and confidence threshold to compare models. Run both against the 12-camera load before changing a site default. [Cloud image profiles](../infra/ecs-cctv/README.md#growth-and-recovery)

For a distant doorway, driveway, or other fixed region, use `models.ground.detailRegion` with normalized left, top, right, and bottom coordinates. The worker keeps its full-frame inference and adds one detection pass in the region. The crop contributes person detections by default. Set `models.ground.detailObjects` to `["vehicle"]` for a driveway or `["person", "vehicle"]` for a shared approach. The two classes share one crop pass. Set the decode size to preserve the original stream's detail. Crop boxes map back to the original frame before tracking and rule evaluation. Posture includes a digest of the crop profile in the model fingerprint. Verify the camera's sample rate under site load.

For a camera whose subjects generally appear sideways or inverted, set `models.ground.rotationDegrees` to `90`, `180`, or `270` clockwise in the worker JSON. The default is `0`. The ECS camera secret accepts the same field at its top level. The worker rotates input to YOLO11 and returns the boxes to the original stream coordinates. Set the decode dimensions, detail region, rules, and exclusions using the original image. Rotation uses one inference per configured view. Posture includes the angle in the model fingerprint, such as `:r90`. Validate the setting on a full view before using it in Alert mode. [Overhead people detection research](https://arxiv.org/abs/2005.11623)

The MEVA tool accepts both exports in the same run. The new `g301` case scores box agreement for a labeled person in a hospital exit view.

```bash
"$HOME/.local/share/proxigo/cctv-venv/bin/python" scripts/dev/benchmark_cctv_meva.py /tmp/proxigo-meva-quality \
  --case g301 --models yolo11s yolo11l --fps 4 \
  --yolo11 "$PROXIGO_CCTV_YOLO11" --yolo11-sha256 "$PROXIGO_CCTV_MODEL_SHA256" \
  --yolo11l "$PROXIGO_CCTV_YOLO11L" --yolo11l-sha256 "$PROXIGO_CCTV_MODEL_LARGE_SHA256"
```

For a broader CAVIAR box comparison, run the time-in-zone benchmark once per export with `--case Browse_WhileWaiting2 --case Browse2 --case OneShopOneWait2front --case Rest_InChair`. It uses one-to-one box assignment within each sampled frame at IoU 0.30. The extra clips have partial scene annotations. Their box scores measure coverage of labeled people.

To replay a detail region at the full source size, add `--native-input --detail-region 0.52 0.27 0.83 0.67` to the G301 command. That rectangle covers the doorway in this test fixture. Use a different output directory for each comparison to retain the baseline reports.

| G301 profile at 4 fps and native 1920 × 1080 decode | Labeled person samples matched | Mean processing per frame on M1 Pro with CoreML |
| --- | ---: | ---: |
| YOLO11s full frame | 9/95 | 26 ms |
| YOLO11s with doorway detail | 44/95 | 47 ms |
| YOLO11l full frame | 7/95 | 46 ms |
| YOLO11l with doorway detail | 76/95 | 93 ms |

Both full-frame profiles matched both labeled MEVA entrance crossings. Across five CAVIAR clips, small matched 470 of 1,270 person boxes and large matched 544. The larger model gained on the longer waiting clip (40 to 84 matches) and shopping-centre view (314 to 352), and lost matches on two other clips. A selected detail region increased the waiting clip to 88 of 172 matches. The worker still missed the 30-second dwell event. The G301 detail region was chosen for that camera's doorway. Customer-site event recall and false alarms per camera-hour require separate evaluation on independently reviewed continuous footage. [YOLO11 variants](https://docs.ultralytics.com/models/yolo11/) · [Slicing research](https://arxiv.org/abs/2202.06934)

Add `--g301-dwell` to score a separate 15-second doorway rule derived from the labeled exit track. This replay starts at the first annotated frame. At 4 fps with native decode, YOLO11l with the doorway crop matched 78 of 96 labeled person samples and detected the event 0.75 seconds after the reference threshold. Both full-frame profiles and YOLO11s with the crop missed the event. [MEVA dataset](https://mevadata.org/)

For G301, keep YOLO11s full-frame inference and use a YOLO11s pose model in the doorway crop. Use `--case g301 --g301-dwell --models yolo11s --native-input --detail-region .52 .27 .83 .67 --detail-pose /path/to/yolo11s-pose.onnx --detail-pose-sha256 <digest>`. At 4 fps, the standard YOLO11s crop matched 46 of 96 labeled samples and missed the dwell. The small pose crop matched 90 and reported the event once at 15.00 seconds. The large pose crop matched 85 and also reported at 15.00 seconds. Processing averaged 46.4 ms per sample for the small pose profile and 75.3 ms for the large pose profile in these CoreML replays. [MEVA dataset](https://mevadata.org/) · [YOLO11 pose variants](https://docs.ultralytics.com/tasks/pose/)

Keep the smaller pose crop scoped to cameras where it passes replay. It missed the labeled entries on G638 and the March 13 G299 excerpt, matching 1 of 6 and 0 of 10 labeled boxes respectively. [MEVA dataset](https://mevadata.org/)

When G301 uses `models.ground.detailFromRules: true`, the worker derives the crop from the published zone. With the small pose model, it matched 88 of 96 boxes and reported the same 15.00-second dwell. In a separate live RTSP test with the manual crop, Chrome showed the dwell alert and 15.24 seconds of observed time.

With the same manual crop on the G301 source, a 1280 × 720 decode matched 92 of 96 labeled boxes and reported the dwell at 15.00 seconds. A 640 × 360 decode matched 81 and reported at 18.50 seconds. These are downscales of the original recording. Validate actual recorder substreams separately, since encoding and frame cadence can also affect the observations.

The Linux ARM64 `ecs-pose` image replayed the native G301 clip through the ECS entrypoint with the small pose export. It matched 91 of 96 labeled samples and reported one matching dwell at 15.00 seconds. This was an offline replay in a local VM. The AMD64 image also built and verified both model digests. Its full replay timed out under Apple Silicon emulation. Measure throughput on the target ECS hardware. [ECS image configuration](../infra/ecs-cctv/README.md#commission-twelve-cameras)

### Distant doorways and vehicles

Run the same MEVA command with `--models yolo11s --native-input` for the following fixtures. For the crop comparison, add the options shown below. The doorway rule and crop use the same positions across two recording dates.

| Fixture | Crop options | Matched reference boxes at 4 fps, full frame → crop | Rule result with crop |
| --- | --- | --- | --- |
| `g299-march07` | `--detail-region .65 .12 .88 .43` | 0/15 → 15/15 | Matched the labeled person |
| `g299-march13` | Same doorway crop | 0/10 → 7/10 | Matched the labeled person |
| `g336-stop` | `--detail-region .28 .16 .49 .36 --detail-objects vehicle` | 3/4 → 4/4 | Matched the stopped vehicle |
| `g336-turn` | Same roadway crop | 4/8 → 8/8 | Detected the moving vehicle and produced zero stops |

The full-frame profile missed both doorway observations. With a stricter box agreement threshold of 0.50 intersection over union, the doorway crop matched 19 of 25 labeled samples. The earlier vehicle rule produced one false stop on the moving turn with each detector profile. The updated rule requires box intersection over union of at least 0.55 with the starting box during confirmation, scaling the permitted apparent movement to the vehicle's image size. A skipped sample restarts confirmation. Each excerpt scores the labeled actor, and continuous site false-alarm rates require separate measurements. [MEVA dataset](https://mevadata.org/) · [FHWA video detection handbook](https://www.fhwa.dot.gov/publications/research/operations/its/06108/02a.cfm)

### Confirm movement before a vehicle stop

Enable **Require movement before the stop** on a Vehicle stop rule to report a vehicle the camera sees move and then stop. The worker arms the rule after the same track moves by more than half its reference box width or height and more than three pixels along that axis. It then starts confirmation inside the zone and allows position drift up to 7% of the starting box along each axis, with a one-pixel minimum tolerance. A skipped sample, missed detection, low-confidence box, or exclusion clears the approach evidence. Published day and night confidence and confirmation thresholds still apply. For cameras that need to report vehicles already stopped when the worker starts, leave this option off.

The `g328` fixture matches reports to the annotated sedan by time and box overlap. Both YOLO11s input shapes detected it in all seven labeled samples. Their standard stop rules emitted an early report while it was creeping and missed the labeled stop. With movement confirmation enabled, both reported its stop once at 4.75 seconds and produced no additional observations in the excerpt. Replay with `--case g328 --require-movement` and the approved export and digest. The feature requires approach footage before the stop.

The G336 roadway stop used native decode, 640 × 640 YOLO11s, and a vehicle crop (`--detail-region .28 .16 .49 .36 --detail-objects vehicle`). At 4 fps, the worker reported one stop on source frame 1282, three frames after the annotation ended. The strict scorer recorded one miss and one unreviewed observation. At 8 fps, the same profile matched the annotated stop once with no additional observations. The 4 fps turn replay produced no stop on the annotated turning vehicle. The 8 fps result is from a single-camera replay. [MEVA dataset](https://mevadata.org/)

For a YOLO11 worker with **Require movement before the stop** enabled on a published rule, set `models.ground.vehicleStopConfirmationBoost` to `true` to add one inference sample between regular samples while a vehicle is in the zone and awaiting stop confirmation. For example, set the worker's top-level `fps` to `8` and `models.ground.fps` to `4`. The configured decode rate must be at least twice the regular inference rate. The extra samples end when confirmation completes or qualifying evidence is lost. All ground rules on that worker use its sample stream. For ECS, put `"vehicleStopConfirmationBoost": true`, `"fps": 8`, and `"inferenceFps": 4` in the camera's worker secret. The stream must supply enough distinct frames for the extra samples.

On G336 with the same 8 fps decode for all three runs, fixed 4 fps processed 67 frames and missed the labeled stop. Confirmation boost processed 85 frames and reported the label once at 15.50 seconds. Fixed 8 fps processed 134 frames and reported once at the same time. Replay with `--fps 8 --inference-fps 4 --require-movement --vehicle-stop-confirmation-boost`. This saves inference work in the tested excerpt. Decode load still reflects the higher rate, and simultaneous confirmations across a site require separate capacity measurements. [MEVA dataset](https://mevadata.org/) · [Reducto](https://web.cs.ucla.edu/~ravi/publications/reducto_sigcomm20.pdf)

With boost enabled, the G328 replay matched its labeled sedan stop once with no additional observations. The G336 turn replay produced no stop on the labeled turn. In a one-camera RTSP test, the gateway recorded the G336 stop as an alert and Chrome showed **Observed before the stop** in its evidence.

To load-test confirmation boost, add `PROXIGO_CCTV_STOP_VIDEO=/path/to/g336-stop-entrance.mkv` to the twelve-camera Chrome command. Use the G336 approach excerpt produced by `--case g336-stop --require-movement`. Set `PROXIGO_CCTV_STOP_CAMERAS` to the number of feeds to run on that excerpt, from 1 to 12. Supply the square YOLO11s export as `PROXIGO_CCTV_YOLO11_CANARY` and the 384 × 640 export as `PROXIGO_CCTV_YOLO11` to reproduce the mixed test. Each stop feed uses native 1080p decode at 8 fps, a 4 fps regular inference setting, and the vehicle crop. Once all workers are running, the test requires a fresh live observation from each camera in each of two windows. Stop observations must include movement evidence and at least 0.30 box intersection-over-union with the final annotated G336 stop. Entrance cameras must report a line crossing. The report includes per-camera rates and observation times.

On the M1 Pro, the mixed twelve-feed test passed with one stop feed and again with three stop feeds. Across two reporting windows in the three-stop test, the nine entrance feeds reported 3.96–4.10 fps and the three stop feeds reported 4.58–5.27 fps, including additional confirmation samples. All twelve delivered their expected observations and appeared in Chrome Posture. In one loop, the three stop events arrived from frames received within 2 ms of one another. This is evidence from synchronized replays over local RTSP. [MEVA dataset](https://mevadata.org/)

With all twelve feeds configured for stop confirmation, the strengthened test passed both reporting windows. All twelve cameras produced a fresh observation matching the target vehicle in each window. Rates ranged from 5.19 to 5.32 fps in the first window and from 3.97 to 4.79 fps in the second. The twelve event timestamps spanned less than 0.75 seconds in each loop. Chrome Posture listed all twelve workers. The test uses one synchronized public recording and local RTSP. Installed-camera event recall, false alarms per camera-hour, and sustained capacity on the target hardware require separate measurements.

### Wider input and shadowed doorways

The worker accepts static FP32 YOLO11 ONNX exports with a square or rectangular input, batch one, and embedded NMS disabled. Input sides can be 320–1280 pixels in steps of 32. Use the same `models.ground.path` and `sha256` fields for a different export. Preserve native camera detail in the decoded frame. A wide input gives 16:9 streams more usable pixels in one pass. For example, export YOLO11s with `imgsz=(736, 1280)` (height, width). Record the resulting digest, and compare the same rule and source before enabling alerts. [Ultralytics export options](https://docs.ultralytics.com/modes/export/)

For a 640 × 358 decode, a 384 × 640 YOLO11s input keeps the decoded picture at its existing pixel size and reduces the model input area by 40% compared with 640 × 640. At 4 fps, both exports matched 7 of 8 person boxes at G329, all 10 at G326, and all 7 labeled vehicle boxes at G328 at IoU 0.30. Both reported the two reference crossings with the same timestamps. The powered rerun with eleven rectangular inputs and one square crop worker delivered crossings from all twelve feeds. Five workers were below the 3.5 fps requirement in the final sample, including the crop worker at 1.97 fps. [MEVA dataset](https://mevadata.org/)

At 4 fps on the native MEVA frames, the 736 × 1280 YOLO11s export with no crop matched 11 of 25 doorway boxes and recovered both G299 observations. The 640 × 640 full frame matched zero boxes, and the selected crop at that input size matched 22 of 25. The wider export also matched 7 of 8 labeled turning-vehicle samples, up from 4 of 8 at the standard full-frame input. The 640 × 1152 export gained more boxes at the hospital doorway, where the targeted large-model crop was still needed to recover the 15-second dwell. [MEVA dataset](https://mevadata.org/)

For a person-only detail region, set `models.ground.detailPose` to an object with the absolute ONNX `path` and its approved `sha256`. A YOLO11 pose export contributes its person boxes from the crop. The full-frame detection model still scans for people and vehicles. The worker sends the same box and event metadata as other profiles. The Posture fingerprint includes the crop model. [YOLO11 pose exports](https://docs.ultralytics.com/models/yolo11/)

For the shadowed MEVA doorway, use `--case g638 --models yolo11s --native-input --detail-region .14 .33 .40 .68` with the existing YOLO11s path and digest arguments. Add `--detail-pose /path/to/yolo11l-pose.onnx --detail-pose-sha256 <digest>` for the pose comparison. At the 0.25 confidence floor, standard full-frame inference matched 0 of 6 labeled samples, the standard crop matched 2, and the pose crop matched 3. The pose crop recovered the observation. The combined worker averaged about 79 ms per frame on the M1 Pro with CoreML. Gamma adjustment and histogram equalization were ineffective on this excerpt. The pose crop was weaker on the G299 holdout, so qualify it per camera in Observe mode. [MEVA dataset](https://mevadata.org/) · [Low-light enhancement study](https://journals.qu.edu.sa/jeit/article/id/39/print/)

### Try pose when the person zone has no qualifying box

Set `models.ground.detailPoseFallback` to `true` alongside a pinned `detailPose` model and a person crop. ECS accepts `"detailPoseFallback": true` in the camera secret alongside `"detailPose": true`. The worker runs the full frame and standard crop first. For a published person entry or dwell zone wholly within the crop, it adds a pose pass if the ordinary boxes fail to supply a person who meets that rule's current confidence, size, and exclusion settings. The extra pass can add a qualifying box in that zone and retains the ordinary results. A qualifying person already in the same zone prevents that zone from triggering the extra pass. Empty zones can trigger the third pass on every sample.

Add `--detail-pose-fallback` to the MEVA pose benchmark command. At 4 fps, the same YOLO11s pose export recovered the labeled G301 dwell at 15.00 seconds, recovered the labeled G638 shadowed entry, and preserved the G299 March 13 entry. The standard crop alone matched one of these three events. The pose pass ran on 55 of 101 G301 samples, 16 of 19 G638 samples, and 3 of 10 G299 samples. These excerpts produced no additional observations. [MEVA dataset](https://mevadata.org/) · [Chameleon adaptive inference research](https://www.microsoft.com/en-us/research/publication/chameleon-video-analytics-scale-via-adaptive-configurations-cross-camera-correlations/)

Across the full five-minute G638 and G299 daytime recordings, fallback with person track support matched all nine reviewed entries with zero duplicates and zero observations in the 87 reviewed empty seconds. Pose ran on 1,086 of 1,200 G638 samples and 1,161 of 1,201 G299 samples. These offline tests show the additional pass runs frequently on quiet scenes. In a separate live RTSP test, Chrome displayed the G301 dwell alert with 15.25 seconds of observed time. [Reviewed G638 intervals](../tests/fixtures/cctv-g638-timeline.json) · [Reviewed G299 intervals](../tests/fixtures/cctv-g299-timeline.json)

In the midnight G638 replay prefix, the pose pass ran on all 481 samples and produced zero observations, including the 60 reviewed empty seconds. [Reviewed midnight intervals](../tests/fixtures/cctv-night-g638-timeline.json)

To test pose fallback under site load, add `PROXIGO_CCTV_DWELL_VIDEO=/path/to/g301-exit.mkv` from the `--g301-dwell` replay and set `PROXIGO_CCTV_YOLO11_DETAIL_POSE` to the approved YOLO11s pose export in the twelve-camera Chrome command. Supply the square YOLO11s detector as `PROXIGO_CCTV_YOLO11_CANARY`. `PROXIGO_CCTV_DWELL_CAMERAS` sets how many feeds use the dwell fixture, from 1 to 12. `PROXIGO_CCTV_DWELL_HEIGHT` selects 1080 or 720 for worker decode. `PROXIGO_CCTV_DWELL_SOURCE_FPS` selects 4, 15, or 30 fps for the 1080p H.264 source. `PROXIGO_CCTV_DWELL_SOURCE_PRESET` selects the x264 `ultrafast` or `veryfast` fixture encoder preset. Workers sample the source at 4 fps. After all workers start, the fixture requires a fresh dwell of at least 15 seconds on each selected camera in each of two windows, with box overlap of at least 0.30 IoU against the annotated person at the dwell threshold. Other feeds still require a line crossing. All workers must meet 3.5 inference fps and appear in Chrome Posture.

On the M1 Pro with 4 fps H.264 sources, all twelve workers reported the labeled dwell in both windows at 1080p and again at 720p. At 1080p, rates were 3.89–4.10 fps in the first window and 3.96–4.16 fps in the second. At 720p, rates were 3.96–4.10 fps and 3.89–4.10 fps. The twelve observations in each window came from frames received within 0.25 seconds at 1080p and within 0.29 seconds at 720p. The tests used one synchronized public recording over local RTSP. [MEVA dataset](https://mevadata.org/)

The 30 fps `ultrafast` H.264 replay sustained 3.79–4.07 inference fps across twelve workers in the final sample and produced zero matching dwells. Offline replay of that exact stream at 1080p matched 58 of 96 labeled boxes and accrued at most 9.50 seconds of qualifying dwell. The 720p decode, the larger pose crop, and the rule-derived crop also missed the dwell on that encode. With x264 `veryfast`, the same recording and rate-control settings produced an average video bitrate of 5.68 Mb/s with both presets. The `veryfast` stream matched 86 of 96 labeled boxes and produced one matching dwell at 16.00 seconds offline. These are controlled encoder results for this recording. Qualify analytics with the installed camera or recorder's actual stream configuration. [H.264 compression and surveillance object detection](https://arxiv.org/abs/2211.05805)

With the `veryfast` 1080p, 30 fps source, all twelve workers reported a fresh matching dwell in both acceptance windows and appeared in Chrome Posture. Rates were 3.87–4.09 fps in the first window and 3.87–4.06 fps in the second. The per-camera observations covered 3.08 seconds in the first window and 27.51 seconds in the second, spanning different replay loops in the second window.

Add `PROXIGO_CCTV_DWELL_CYCLES=3` to score three complete loops after every worker starts. The test estimates each loop's start from FFmpeg publisher progress and reports clock drift, matching events by cycle, and final worker health. It requires exactly one matching dwell from the annotated person in every measured cycle. An instrumented baseline matched 31 of 36 expected camera-loop events. The five misses each followed a three-slot gap at 4 fps with measured receipt times 9–19 ms beyond the configured 0.75-second limit. [FFmpeg progress documentation](https://ffmpeg.org/ffmpeg.html) · [RTP timing standard](https://www.rfc-editor.org/rfc/rfc3550.html)

For live dwell reconnection, the worker uses the count of decoded frame slots to distinguish a small receipt-time overrun from a longer gap. When the nominal interval fits the published limit, the worker can tolerate jitter of up to 50 ms or half a decoded slot, whichever is smaller. It still checks continuity of the same track and its path through the unmasked zone. Interrupted time contributes zero seconds to the dwell total.

The corrected 1080p, 30 fps run matched all 36 camera-loop events, one in each of three loops across all twelve feeds. The final heartbeat sample showed 3.96–4.06 inference fps, and Chrome Posture showed all twelve workers. Matching events arrived 16.35–20.86 seconds into their loops. This checks repeated dwell detection on one synchronized source over local RTSP.

On a separate recording from the same camera, both crop profiles matched all seven labeled passages through the configured zone. Limiting the merge to boxes with closely aligned foot positions and substantial overlap removed one duplicate caused by partial occlusion. The replay scored labeled passages. Measure false alerts on continuous independently reviewed footage.

### Detail from published person rules

Set `models.ground.detailFromRules` to `true` in a YOLO11 worker JSON. For ECS, put `"detailFromRules": true` at the top level of that camera's secret. The worker adds one person-detail pass around the image points of its active published person rules. It keeps the full-frame pass for all configured object types. The crop extends 7.5% of the image width on each side, 14.5% of the height above the rule for the body, and 10.5% below it. It stays inside the source image. If the crop spans more than 65% of either image dimension or is smaller than 32 pixels on either side, the worker uses full-frame inference. [Slicing-aided detection research](https://arxiv.org/abs/2202.06934)

Publish the camera's image rules as usual. The worker recalculates the crop when it receives the new policy, and Posture identifies the resulting model with a `:rule-detail:` suffix. Published minimum confidence, size, masks, and confirmation times still govern the observations. An approved person pose model can supply the detail boxes. Use either the crop from rules or a manual `detailRegion` for each worker. Check the camera's Observe report and inference rate with the other streams running.

For a single steep person tripwire with an especially narrow crop, the derived region extends farther to either side of the wire and uses less padding below its lower endpoint. This gives the detector more of the pedestrian's approach while keeping the person large in the model input. The worker calculates the proportions in decoded pixels. [Sliced inference research](https://arxiv.org/abs/2202.06934)

For gates or walkways far apart in the image, set `models.ground.detailRegionLimit` to `2` with `detailFromRules` enabled. In ECS, set the same field at the top level of the camera secret. If the combined crop exceeds the size limit, the worker takes up to two valid individual crops, starting with the smallest distinct regions. It runs full-frame inference once and merges the crop detections in the original coordinates. On-demand pose evaluates each crop against the published rules it covers. The default crop limit is `1`.

To reproduce the separated-zone replay, use the G638 doorway setup from the continuous replay below and add a person zone-entry rule with the same confidence, size and confirmation settings for `[[.38,.93],[.60,.93],[.60,.995],[.38,.995]]`. At 4 fps and with person track support, the original combined-crop limit matched five of seven reviewed doorway entries. The two-crop profile matched all seven. Both matched the reviewed walkway approach at 120.50–125.00 seconds and had zero doorway duplicates or observations in the 60 reviewed empty doorway seconds. The rest of the walkway timeline is unscored. Mean processing time was 24.0 ms per frame for the original limit and 58.9 ms for two crops on the M1 Pro with CoreML. In a separate live RTSP run, Chrome showed distinct doorway and walkway alerts and their evidence. [MEVA dataset](https://mevadata.org/)

### Continuous doorway replay

Zone-entry rules keep a reported entry active when a tracked foot position drifts just outside the boundary. A clear exit allows a subsequent entry. The margin is 5% of the detected box height, capped at 1% of the frame's shorter dimension. The initial observation still requires qualifying samples inside the zone for the rule's confirmation time.

For a YOLO11 camera with repeat entries after brief detection gaps, enable `models.ground.personTrackSupport` in its worker JSON. The ECS camera secret accepts `"personTrackSupport": true` at its top level. The worker can use a person box at confidence 0.10 or higher to support a previously reported entry. Reconnection requires a stronger observation, a unique match consistent with the boxes and their motion, and a path inside the original unmasked zone. The window is at most 2.25 seconds from the last strong observation and 1.35 seconds from the supporting box. A skipped sample clears support. Events and dwell time require observations that meet the published rule. [ByteTrack association research](https://arxiv.org/abs/2110.06864)

The timeline scorer verifies the video digest and continuous inference samples for the selected lane. In the reference JSON, each event name maps to one or more half-open time intervals in seconds. Repeated observations within one event's intervals count as duplicates. Reviewers supply empty intervals independently of the worker's output. An empty-time false observation requires a reviewed empty interval. A window without a review provides no false-alarm evidence. [NIST ActEV evaluation](https://actev.nist.gov/) · [MEVA dataset](https://mevadata.org/)

Run the worker with `--start-at 2026-01-01T00:00:00+00:00`, capture stdout as `events.jsonl`, and save `--detections boxes.jsonl`. For the public fixtures, use a 4 fps ground worker with `sensorId` matching the reference. Both recordings use a `door` person zone-entry rule in Observe mode with confidence 0.25, a minimum size of 12 pixels and 0.5 seconds of confirmation.

| Reference | Recording | Worker decode size | Zone | Standard crop |
| --- | --- | --- | --- | --- |
| [G638](../tests/fixtures/cctv-g638-timeline.json) | [Shadowed doorway](https://mevadata-public-01.s3.amazonaws.com/drops-123-r13/2018-03-07/16/2018-03-07.16-50-00.16-55-00.school.G638.r13.avi) | 1920 × 1072 | `[[0.22,0.49],[0.32,0.49],[0.32,0.56],[0.22,0.56]]` | `[0.14,0.33,0.40,0.68]` |
| [G299](../tests/fixtures/cctv-g299-timeline.json) | [Gym doorway](https://mevadata-public-01.s3.amazonaws.com/drops-123-r13/2018-03-13/17/2018-03-13.17-35-00.17-40-00.school.G299.r13.avi) | 1920 × 1080 | `[[0.74,0.23],[0.79,0.23],[0.79,0.30],[0.74,0.30]]` | `[0.65,0.12,0.88,0.43]` |

```bash
python3 scripts/dev/score_cctv_timeline.py tests/fixtures/cctv-g638-timeline.json \
  --video /private/g638.avi --trace /private/boxes.jsonl --events /private/events.jsonl
```

On G638 the standard full-frame profile matched five of seven reviewed entries. The edge margin removed its one duplicate and retained all five matches. Both crop profiles matched all seven with zero duplicates. The pose crop kept all seven with track support enabled, including two closely spaced entries. On G299, standard full-frame inference matched both reviewed entries. The standard crop reported the return 3.75 seconds earlier and emitted one additional observation in an unreviewed interval. The 736 × 1280 profile reported the return 3.50 seconds earlier and emitted a duplicate after losing the track for two seconds. Track support removed that duplicate and kept both reviewed entries. All profiles had zero observations in the reviewed empty windows, which cover 60 seconds at G638 and 27 seconds at G299. The recordings show daytime conditions.

With 640 × 640 YOLO11s, `detailFromRules` and `personTrackSupport` enabled, the worker derived `[0.145,0.345,0.395,0.665]` for G638 and `[0.665,0.085,0.865,0.405]` for G299. It matched all seven G638 entries and both G299 entries across the full recordings. It emitted zero duplicates and zero observations in the 87 reviewed empty seconds. The G299 return was reported 3.75 seconds before the standard full-frame replay's observation. [MEVA dataset, CC BY 4.0](https://mevadata.org/)

In the CAVIAR shopping-centre clip `OneShopOneWait2front`, both track settings produced observations that matched the same six annotated people. Track support removed one repeat for a matched person during continuous occupancy. The comparison used 234 samples at 4 fps. [CAVIAR shopping-centre video and annotations](https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/)

### Nighttime person detection

The miniNIRPed benchmark verifies the published annotations and all images by SHA-256, then uses the same YOLO11 ONNX detector as the camera worker. It matches person boxes one-to-one in score order at IoU 0.30 and 0.50. It reports separate counts for boxes below 96 pixels in height, overlaps with ignore or other-class labels, and unmatched proposals. The images come from a vehicle-mounted near-infrared camera. The first comparison evaluates general-purpose models on both published splits. All three exports in that comparison used their original general-purpose weights. [NIRPed by Dai et al., CC BY 4.0](https://github.com/XiaobiaoDai/NIRPed)

Run the same command for each approved ONNX export and split. The first run stages approximately 186 MB for `val` or 308 MB for `train`, outside the checkout.

```bash
"$HOME/.local/share/proxigo/cctv-venv/bin/python" scripts/dev/benchmark_cctv_nirped.py /tmp/proxigo-nirped \
  --split val --weights /private/yolo11s.onnx --sha256 <approved-sha256> \
  --provider CoreMLExecutionProvider --confidence 0.25
```

| Model input and confidence | Mini validation matches / 341 | Validation unmatched | Holdout matches / 570 | Holdout unmatched |
| --- | ---: | ---: | ---: | ---: |
| YOLO11s, 640 × 640, 0.25 | 301 | 60 | 470 | 58 |
| YOLO11s, 1280 × 736, 0.35 | 307 | 58 | 470 | 60 |
| YOLO11l, 640 × 640, 0.35 | 308 | 57 | 494 | 56 |

At IoU 0.50, YOLO11l at 0.35 gained 31 matches over YOLO11s at 0.25 across both splits, including 13 additional matches below 96 pixels. Unmatched proposals fell from 118 to 113. Gamma correction and percentile autocontrast moved the YOLO11s validation result at 0.25 by one match in either direction. Both transformations remain benchmark options. Per-image p95 was 23–40 ms for the standard YOLO11s and 119–132 ms for YOLO11l on the M1 Pro using CoreML. The benchmark includes preprocessing and inference for a single stream. Allow for decode, concurrent cameras, and event delivery when sizing a worker. Use the larger model as a per-camera Observe candidate with the night confidence set to 35% while commissioning a supported near-infrared camera. [YOLO11 model profiles](https://docs.ultralytics.com/models/yolo11/) · [Per-camera canary deployment](../infra/ecs-cctv/README.md#commission-twelve-cameras)

For the midnight MEVA comparisons, use the same decode sizes and door zones as the daytime replays above. Replay the person zone-entry rule in Observe mode at 4 fps with 12 minimum pixels and 0.5 seconds of confirmation. Use confidence 0.25 for the standard YOLO11s or 0.35 for YOLO11l. Each reference requires a continuous trace for the first 120 seconds and scores the second minute. Reference review sampled each window at 4 fps and inspected regular frames and the largest local changes in the monitored doorway. Both model profiles produced zero door observations across those two one-minute windows.

| Reference | Recording |
| --- | --- |
| [G299 at midnight](../tests/fixtures/cctv-night-g299-timeline.json) | [Dark gym with doorway glare](https://mevadata-public-01.s3.amazonaws.com/drops-123-r13/2018-03-11/00/2018-03-11.23-55-00.00-00-00.school.G299.r13.avi) |
| [G638 at midnight](../tests/fixtures/cctv-night-g638-timeline.json) | [Monochrome courtyard with entrance lighting](https://mevadata-public-01.s3.amazonaws.com/drops-123-r13/2018-03-11/00/2018-03-11.23-55-00.00-00-00.school.G638.r13.avi) |

On the fixed portrait camera G639 at twilight, both people entered the reviewed walkway zone between seconds 210 and 216 of the original recording. At 4 fps, with the same minimum size and confirmation time, the standard model reported them at 212.50 and 214.75 seconds. YOLO11l reported them at 213.00 and 214.25 seconds, and the standard model with a detail crop reported them at 212.75 and 214.00 seconds. This replay covered the 21 seconds starting at 206. Use 1072 × 1920 decoding, zone `[[0.50,0.38],[0.70,0.38],[0.70,0.447],[0.50,0.447]]`, and detail crop `[0.44,0.25,0.83,0.57]` to reproduce the comparison. [G639 twilight recording from MEVID, CC BY 4.0](https://mevadata-public-01.s3.amazonaws.com/drop-5-mevid/2018-05-16.21-10-00.21-15-00.school.G639.r13.avi) · [MEVID](https://github.com/Kitware/mevid)

### Near-infrared person model

The fine-tuning experiment used 189 training images with 394 person boxes from the published miniNIRPed training split. Two separate capture dates (`20190508`, `20200529`) supplied 42 images and 117 targets to choose the checkpoint and confidence threshold. The final test used 46 validation images from five capture dates excluded from both training and calibration. The two published splits share capture dates, so the experiment excluded every image from those dates before training. [miniNIRPed](https://github.com/XiaobiaoDai/NIRPed) · [YOLO11 training and exports](https://docs.ultralytics.com/models/yolo11/)

| 640 × 640 model | Held-out near-infrared matches at IoU 0.50 | Unmatched proposals | Fixed-camera matches at IoU 0.50 | Unmatched proposals |
| --- | ---: | ---: | ---: | ---: |
| General YOLO11s at 0.25 | 83/97 | 12 | 14/16 | 1 |
| Fine-tuned YOLO11s at 0.35 | 90/97 | 11 | 16/16 | 1 |

ONNX Runtime's CPU provider reproduced the held-out match and unmatched-proposal counts from CoreML.

The independent fixed-camera check used the green near-infrared channel of the TRICLOBS source frames. Two people appear in each of the eight reviewed positive frames, and both models returned zero person boxes in the 22 reviewed empty frames. The two-crop general-purpose profile found 16/16 with three unmatched proposals. The 8.6-second C7 replay used source frames 292–544 every six frames at 5 fps and sampled at a 4 fps inference target. With a person tripwire at `x = 0.39`, `y = 0.55–0.79`, direction `negative`, a 12-pixel minimum, and 0.5 seconds of confirmation, the general model reported one of the two crossings and the specialist reported both. A local RTSP-to-gateway run showed both specialist alerts with their model fingerprint in Chrome. Site-wide alert rates and twelve-camera throughput need a separate validation run. [TRICLOBS dataset, CC BY 4.0](https://doi.org/10.6084/m9.figshare.3206887) · [TRICLOBS sensor channels](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0165016)

Use [the training script](../scripts/dev/train_cctv_nirped.py) with an approved YOLO11s training checkpoint and its SHA-256. The experiment used Ultralytics 8.3.252, PyTorch 2.5.1, and ONNX 1.17.0. Evaluate with [the benchmark script](../scripts/dev/benchmark_cctv_nirped.py), `--split val`, and a `--capture-day` argument for each of `20190326`, `20200401`, `20200525`, `20200624`, and `20210315`. Add `--person-only --confidence 0.35` for the specialized export. The tested export's SHA-256 is `6b9bebecf684b9dcb60763a2a2d4a4dc199c6ac73c79886d78cfccdee8737b2e`.

For an on-premises camera, add `"personModel": {"path": "/absolute/path/yolo11-person.onnx", "sha256": "<approved-digest>"}` under `models.ground` in its private worker configuration. The export must be static, batch 1, with one person class and without embedded NMS. Person rules use this export and vehicle rules use the general YOLO11 model. A camera with both classes active runs both models. Evaluate the profile in Observe mode at the camera's intended lighting and sampling rate before enabling alerts. Use the [ECS instructions](../infra/ecs-cctv/README.md#growth-and-recovery) to enable it for an individual cloud camera.

### Cameras that switch from color to infrared

For a day/night camera commissioned to output color in normal mode and monochrome when its infrared filter switches, set `"personModelMode": "monochrome"` alongside the approved `personModel` in `models.ground`. Use the same mode setting at the top level of the ECS camera secret alongside `"personModel": true`. The worker starts with general YOLO11, switches to the specialist after three consecutive varied monochrome samples, and returns to general YOLO11 on the first clearly colored sample. The switch uses the decoded video. Verify this mode on each camera's actual substream, especially if the camera can output grayscale in visible light or retain color at night. Use the `always` mode for a dedicated near-infrared feed. [Axis infrared camera modes](https://whitepapers.axis.com/en-us/ir-in-surveillance)

The worker keeps the person track and clears its pending tripwire confirmation on a model switch so a shift in box position cannot create a crossing. Evidence records the model selected for that observation. One model pass handles person-only rules and color-mode ground rules. A monochrome sample with both person and vehicle rules uses both models.

On two color CAVIAR clips, automatic selection kept the general model on every sample. At IoU 0.30 and confidence 0.25, it matched 163 of 172 labeled boxes in the upright lobby case and 314 of 482 in the storefront case. At the same confidence, the infrared specialist alone matched 99 and 300. At its near-infrared calibration of 0.35 it matched 76 and 293. [CAVIAR lobby annotations](https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/) · [CAVIAR storefront annotations](https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA2/)

At 4 fps, a 24.5-second replay of real footage joined a color MEVA G329 crossing, two near-infrared TRICLOBS C7 crossings, and a repeat of the color segment. Use G329 frames 2580–2819, resized to 640 × 358 and padded at y = 60 in a 640 × 480 frame. The color tripwire is at x = 0.55, spanning y = 0.438–0.587, with direction `positive` and confidence 0.25. The infrared segment uses the C7 source frames and tripwire above. The automatic profile matched all four expected observations with the expected model IDs and zero duplicate matches. General YOLO11s alone matched three. The specialist alone matched two. The clip simulates stream mode changes at the joins. Commissioning still needs a real day-to-night and night-to-day transition on the intended hardware. [MEVA G329 recording](https://mevadata-public-01.s3.amazonaws.com/drops-123-r13/2018-03-07/16/2018-03-07.16-50-00.16-55-00.admin.G329.r13.avi) · [TRICLOBS dataset](https://doi.org/10.6084/m9.figshare.3206887)

### Retain recent samples during RTSP bursts

For a feed that delivers decoded frames in short bursts, set `"liveFrameBufferMs": 350` at the top level of its private worker JSON or ECS camera secret. The ground lane can recover up to two preceding frames in order. It considers a preceding frame only while it is inside the configured window, which starts when FFmpeg hands that frame to the worker. The worker processes available frames immediately and returns to the newest frame when the window expires. The valid range is 0–500 milliseconds, with 0 selecting the latest-frame behavior. Allow for up to two extra decoded RGB frames per worker, about 1.8 MiB at 640 × 480 or 11.9 MiB at 1920 × 1080. [Bounded live-video queues](https://gstreamer.freedesktop.org/documentation/application-development/advanced/pipeline-manipulation.html#grabbing-data-with-appsink)

On an M1 Pro with CoreML, twelve independent 640 × 480 RTSP workers ran the 24.5-second day/night replay above for two complete loops after startup. Steady delivery produced 96 of 96 expected crossings. A local TCP relay paused video delivery for 480 ms every 1.69 seconds and then released the pending bytes in order. The latest-frame profile matched 88 of 96 crossings and logged 122 skipped-slot markers even though it reported 3.59–3.99 inference fps. With the 350 ms buffer, the same relay settings yielded 96 of 96, zero duplicate observations in the reviewed replay, zero skipped-slot markers, and 3.78–4.09 inference fps. P95 time from decode to inference start rose from 80 to 115 ms. These measurements cover short bursts on local replay, with the published stream, worker decode, and inference target all at 4 fps.

### Crowded walkway crossings

For a YOLO11 camera that swaps pedestrian tracks at a tripwire, test `models.ground.personMotionAssociation: true` in its worker JSON or `"personMotionAssociation": true` in its ECS camera secret. Person matching uses the motion estimate to project the last box to the time of the next sample, then scores overlap there. The option is off by default. On the separate G638 doorway replay, enabling it added three duplicate entry reports, so evaluate crowded crossings and ordinary entries for each camera before adopting it. [SORT motion and overlap association](https://arxiv.org/abs/1602.00763)

The [G336 twilight reference](../tests/fixtures/cctv-twilight-g336-crossings.json) covers ten pedestrian crossings and 33 seconds of reviewed empty tripwire footage in the first 59 seconds. Replay the original recording or a lossless prefix sampled at 4 fps. Use 1920 × 1080 decoding and a `walk` person line-crossing rule with points `[[.25,.50],[.25,.615]]`, direction `negative`, confidence 0.25, 12 minimum pixels and 0.5 seconds of confirmation. Use `[.14,.36,.37,.68]` for the detail crop. Score the recorded events with `score_cctv_timeline.py` and pass the original recording to `--video` for the digest check. [G336 twilight recording from MEVID, CC BY 4.0](https://mevadata-public-01.s3.amazonaws.com/drop-5-mevid/2018-05-16.21-10-08.21-15-08.school.G336.r13.avi)

The count scorer uses `expectedCounts` to check how many distinct tracks reported within each reviewed group. Review the event boxes on crowded groups to catch a single pedestrian reported under multiple track IDs. Full-frame YOLO11s matched seven people. YOLO11l with a detail crop also matched seven and reported one person twice under separate tracks. With motion association, that profile matched eight distinct people, including all four in the close group, and emitted zero alerts during the reviewed empty footage. A live RTSP test with the same opt-in profile delivered all four alerts from the close group to Chrome and reached 3.99 inference fps after startup on the M1 Pro.

For person tripwires with small targets, enable **Scale crossing buffer to person size** in the CCTV rule editor and publish the site draft. The versioned rule stores `scaleCrossingBuffer: true`. The worker uses half the box span perpendicular to the wire, bounded to 0.006–0.012 normalized image units. The usual direction, finite segment, confidence and confirmation checks apply. A missed detection still clears tripwire confirmation.

With the scaled buffer, the same 4 fps YOLO11l crop and motion-association profile matched nine of ten G336 crossings with zero alerts during the 33 reviewed empty seconds. The first overlapping pair reported at 36.00 and 36.75 seconds, with the boxes showing two distinct people. Chrome published the scaled rule and displayed both live alerts with their evidence. Replaying the scaled profile at 8 fps and testing both alternating 4 fps subsets of those samples each yielded nine crossings. One crossing in the second overlapping pair remains missed at both rates. Each of the same sampling checks also yielded one crossing on each labeled G329 and G326 entrance excerpt. [G336 twilight recording](https://mevadata-public-01.s3.amazonaws.com/drop-5-mevid/2018-05-16.21-10-08.21-15-08.school.G336.r13.avi) · [MEVA entrance annotations](https://mevadata.org/resources/README-meva-kf1-data.html)

### Recover a crossing across a brief detection gap

Set `models.ground.personTripwireRecovery: true` for an on-premises YOLO11 worker, or set `"personTripwireRecovery": true` at the top level of its ECS camera secret. An established person track can retain its approach confirmation through a detection gap of up to 0.75 seconds. The two most recent qualifying observations set the motion estimate. A unique qualifying observation on the opposite side must agree with that motion and pass the finite line, direction and exclusion checks. The worker clears pending confirmation on a skipped video sample or model transition. The feature runs the same inference passes as the underlying detector profile. [Observation-based tracking research](https://openaccess.thecvf.com/content/CVPR2023/html/Cao_Observation-Centric_SORT_Rethinking_SORT_for_Robust_Multi-Object_Tracking_CVPR_2023_paper.html)

At 4 inference fps, the option raised full-frame YOLO11s from four to five of the six G639 twilight stair crossings. The added observation was at 30.50 seconds, after two frames with no qualifying box. The same option recovered the labeled G329 entrance at a confidence of 0.35 using 8 fps decode and 4 fps inference. Its box matched the labeled actor, and the observation followed the reference crossing by 0.48 seconds. G336 retained nine of ten crossings, and the full-frame G639 daylight run retained seven of eight. None of these runs added an extra or duplicate report in the reviewed intervals. [Stair reference](../tests/fixtures/cctv-twilight-g639-stairs.json) · [Daylight stair reference](../tests/fixtures/cctv-daylight-g639-stairs.json) · [MEVA dataset](https://mevadata.org/)

In a controlled RTSP test, both workers processed the same G329 replay with one inference frame obscured near the line. The worker with recovery reported the crossing in all three loops. The control reported zero. Both reported all three crossings when the frame was visible.

### Tripwire benchmarks on independent crowds

The [MOT17 tripwire benchmark](../scripts/dev/benchmark_cctv_mot17.py) downloads checksummed copies of three fixed-camera sequences and their published labels. The MOT17 archive publishes this research footage and labels under CC BY-NC-SA 3.0. The benchmark places a vertical and a horizontal tripwire on each view and evaluates both directions. Reference crossings come from the published person trajectories at 30 fps using the same 0.5-second confirmation time and fixed crossing buffer as the worker. Each observation must agree with the rule, the labeled person box at the exact sampled frame at IoU 0.30 or higher, and the crossing time within one second. Matches are one-to-one. The report distinguishes duplicates from observations that could not be matched to a reference crossing. [MOT17 archive and license](https://motchallenge.net/data/MOT17/#license)

| Sequence | Reference crossings | YOLO11s 4 fps | With gap recovery | With gap recovery at 8 fps |
| --- | --- | --- | --- | --- |
| MOT17-02, large square | 25 | 11 | 11 | 11 |
| MOT17-04, elevated night street | 13 | 12 | 12 | 10 |
| MOT17-09, low-angle street | 22 | 8 | 9 | 11 |
| Total | 60 | 31 | 32 | 32 |

YOLO11s at 4 fps produced zero duplicate or unmatched reports across the three official 960 × 540 video previews. At 8 fps, it produced four unmatched reports. YOLO11l at 4 fps matched 30 crossings with two unmatched reports. Gap recovery raised that to 31 with the same two unmatched reports. The rectangular 640 × 1152 and square 960 × 960 YOLO11s exports also ran on MOT17-02, where they matched 11 and 9 crossings with recovery. They produced one and four unmatched reports respectively. The three clips cover 72.5 seconds of labeled footage.

Feeding the tracker the published person boxes at 4 fps matched 55 of 60 crossings, up from 54 before the tripwire endpoint change. The added crossing came from the low-angle sequence. The YOLO11s replays retained 31 matches with the baseline profile, 32 with gap recovery, and 34 with gap recovery and color association. In the low-angle sequence, four missed actors changed track IDs before crossing despite continuous published boxes. A one-to-one global assignment prototype on that clip raised the YOLO11s result from 9 to 11 with recovery, and added one duplicate. Production false alarms per camera-hour require longer reviewed footage. [MOTChallenge benchmark paper](https://arxiv.org/abs/2010.07548) · [Association research](https://www.ecva.net/papers/eccv_2022/papers_ECCV/html/315_ECCV_2022_paper.php)

```bash
python scripts/dev/benchmark_cctv_mot17.py /path/to/mot17-cache \
  --model /path/to/yolo11s.onnx --sha256 <approved-digest> \
  --provider CoreMLExecutionProvider --fps 4
```

### Use color to distinguish nearby pedestrian tracks

Set `models.ground.personColorAssociation: true` in the on-premises worker JSON or `"personColorAssociation": true` at the top level of its ECS camera secret. The worker samples two bands inside each qualifying person box. For recent observations of the same model, the color cue helps rank associations that already pass the tracker's position or overlap check. The descriptors live in worker memory with the short-lived camera tracks. Telemetry and replay traces carry the usual box, score and track ID fields. [Research on combining motion and appearance](https://arxiv.org/abs/1703.07402)

With YOLO11s, gap recovery and color association at 4 fps, the MOT17 research replay matched 34 of 60 crossings, up from 32 with gap recovery alone. Both added matches came from the low-angle view. There were zero duplicates or unmatched reports in either configuration. The color profile averaged 20.5–21.5 ms per sample on the M1 Pro. The separate MEVA replays retained nine G336 crossings, five full-frame G639 crossings, seven G638 doorway entries and two G299 doorway entries, with zero duplicates or alerts in their reviewed empty intervals. Add `--color-association` to the MOT17 command above to score the worker's combined profile.

### Twilight stairs with an oblique camera

The [G639 stair reference](../tests/fixtures/cctv-twilight-g639-stairs.json) covers six visually reviewed left-to-right crossings in a separate 90-second twilight view. Railings obscure the pedestrians' lower bodies. Use a person line-crossing rule named `stairs` with points `[[.58,.70],[.58,.91]]`, direction `negative`, confidence 0.25, a 12-pixel minimum, 0.5 seconds of confirmation, and `scaleCrossingBuffer: true`. Use the rule in Observe mode with sensor ID `g639`. The manual crop is `models.ground.detailRegion: [.43,.56,.94,.94]` and covers the approach as well as the line. [G639 twilight recording from MEVID, CC BY 4.0](https://mevadata-public-01.s3.amazonaws.com/drop-5-mevid/2018-05-16.21-10-00.21-15-00.school.G639.r13.avi)

| Worker profile at 4 fps | Decode size | Crossings matched | Mean processing per sample |
| --- | --- | --- | --- |
| YOLO11s, full frame | 1072 × 1920 | 4/6 | 31.2 ms |
| YOLO11s, original crop from published rule | 1072 × 1920 | 4/6 | 50.6 ms |
| YOLO11s, updated crop from published rule | 1072 × 1920 | 6/6 | 52.0 ms |
| YOLO11s, manual crop | 1072 × 1920 | 6/6 | 54.3 ms |
| YOLO11l, manual crop | 1072 × 1920 | 6/6 | 88.0 ms |
| YOLO11s, manual crop | 536 × 960 | 6/6 | 42.6 ms |

Every profile in the table produced zero duplicates, zero alerts during 67.5 reviewed empty seconds, and zero alerts outside the review intervals. The smaller decode with the manual crop matched all six crossings in a single-camera CoreML replay on an M1 Pro.

In a live RTSP test, twelve workers all used the 536 × 960 manual crop profile with a 350 ms recent-frame buffer. The gateway received all 72 expected crossings and zero extra alerts during one scored loop. Each worker reported 3.96–4.10 inference fps across five heartbeats. The twelve streams replayed the same camera footage over loopback. The test measures how a busy scene runs concurrently on the test host. Customer routes and camera views need their own commissioning measurements.

At 536 × 960, the original rule-derived crop matched five of six twilight crossings. Widening it and tightening the lower edge matched all six with zero extra observations. The same two crop profiles each matched all eight crossings in the separate [G639 daylight reference](../tests/fixtures/cctv-daylight-g639-stairs.json), with zero duplicates or extra observations. That reference includes reviewed right-to-left passages that the configured direction rejects. On two independent MEVA entrance views, G329 and G326, the new geometry preserved the labeled crossing and actor box count in each. [G639 daylight recording](https://mevadata-public-01.s3.amazonaws.com/drop-5-mevid/2018-05-16.14-25-01.14-30-01.school.G639.r13.avi)

Make a lossless 4 fps prefix with FFmpeg and use it for the worker replay. Set the worker's decode and ground inference to 4 fps. Run with `--start-at 2026-01-01T00:00:00+00:00 --detections /path/to/trace.jsonl` and write stdout to the event file. Pass the original AVI for the digest check.

```bash
ffmpeg -i /path/to/g639.avi -t 90 -vf fps=4 -c:v ffv1 /path/to/g639-90.mkv
python scripts/dev/score_cctv_timeline.py tests/fixtures/cctv-twilight-g639-stairs.json \
  --video /path/to/g639.avi --trace /path/to/trace.jsonl --events /path/to/events.jsonl
```

The Chrome test takes the original twilight G639 AVI, verifies its SHA-256, and scores all six crossings for each of the twelve workers after startup. Each worker derives its crop from the published tripwire. The test checks two or more throughput readings per camera at 3.5 fps or above, verifies the crop fingerprint in Posture, and opens a live alert.

With the updated crop from the published rule, the Chrome test matched all 72 crossings and zero extras. The 536 × 960 workers reported 3.90–4.10 inference fps over five or six heartbeats per camera in the scored loopback run.

```bash
MEDIAMTX=/path/to/mediamtx \
PROXIGO_CCTV_PYTHON=/path/to/cctv-venv/bin/python \
PROXIGO_CCTV_YOLO11=/path/to/yolo11s.onnx \
PROXIGO_CCTV_YOLO11_PROVIDER=CoreMLExecutionProvider \
PROXIGO_CCTV_STAIRS_VIDEO=/path/to/g639.avi \
npm --prefix desktop-app run test:e2e -- cctv-analytics.spec.ts --grep 'twilight stair site'
```

### Occluded-person detection benchmark

Around the second overlapping pair in G336, the detector produces one qualifying box for most of roughly four seconds while two heads remain visible. Test a detector change against overlapping-person labels before tuning the tracker on this clip. A person crossing still requires qualifying image evidence on both sides of the tripwire. [Crowded pedestrian detection research](https://openaccess.thecvf.com/content_CVPR_2020/html/Huang_NMS_by_Representative_Region_Towards_Crowded_Pedestrian_Detection_by_Proposal_CVPR_2020_paper.html)

The [Open Images cohort benchmark](../scripts/dev/benchmark_cctv_occlusion.py) uses `open-images-selection.json` in a local cache. The manifest records source image IDs, hashes of the public CVDF mirror images, author and license metadata, normalized person boxes, and the published occlusion attribute. The experiment reserved 319 images for training and 80 disjoint images for calibration, both from the published validation split. The held-out cohort uses 120 images from the published test split. It contains 360 occluded person boxes, 163 other person boxes in crowded images, and 44 boxes in the unoccluded image cohort. Match proposals one-to-one at IoU 0.50 and report unmatched proposals separately. The still-image cohort does not measure alert false alarms or track continuity. [Open Images V7 annotations and mirror](https://storage.googleapis.com/openimages/web/download_v7.html)

The benchmark verifies local images against the manifest and fetches a matching copy from the official mirror when needed.

```bash
python scripts/dev/benchmark_cctv_occlusion.py /path/to/cohort-cache \
  --phase holdout --weights /path/to/yolo11l.onnx --sha256 <approved-digest> \
  --provider CoreMLExecutionProvider --confidence 0.30
```

At the thresholds chosen on calibration, the stock YOLO11s export at 0.25 matched 215 of 360 held-out occluded boxes and 383 of 567 person boxes overall, with 83 unmatched proposals. The stock YOLO11l export at 0.30 matched 222 occluded boxes and 394 overall, with 66 unmatched proposals. A looser suppression threshold of 0.60 with YOLO11s confidence at 0.30 matched the same 215 occluded boxes with 71 unmatched proposals. [Open Images evaluation protocols](https://storage.googleapis.com/openimages/web/evaluation.html)

The 960 × 960 YOLO11l export at 0.45 matched 223 occluded boxes and 385 overall, with 57 unmatched proposals. It reported four of ten crossings on the G336 replay. At 640 × 640 and 0.30, YOLO11l matched nine crossings with zero duplicates and zero reports in the 33 reviewed empty seconds.

The six-epoch YOLO11s fine-tune started from the pretrained person classifier. The calibration threshold of 0.35 yielded 44 unmatched proposals within the stock model's budget of 51. The export matched 199 occluded boxes on the held-out set and 356 person boxes overall, with 50 unmatched proposals. Using the existing person-model override and the same crop and motion tracking, it reported six of ten G336 crossings. Among the evaluated profiles, the 640 × 640 stock YOLO11l achieved the most verified crossings and the most held-out person-box matches overall.

### Motion evidence through a pedestrian overlap

Two published YOLO11 head checkpoints and a YOLO11n trained on 240 Open Images photos were evaluated for visible head evidence. Thresholds selected on 54 calibration photos yielded 210 and 214 matched boxes for the published models and 314 for the locally trained model, from 461 labeled heads in 72 held-out photos at IoU 0.50. The corresponding unmatched counts were 66, 53, and 108. The locally trained model matched only three of 16 reviewed heads in the difficult G336 walkway pair at its calibrated threshold of 0.20. The tested tighter crops also missed most heads there. [Open Images annotations](https://storage.googleapis.com/openimages/web/download_v7.html) · [Published YOLO11 head checkpoints](https://huggingface.co/wesjos/YOLO-head-detection)

The [motion review](../scripts/dev/evaluate_cctv_occlusion_motion.py) starts from two actual body detections in each of two frames before the line. It follows distinct image features near each person's head, rejecting points that drift when tracked backward. It pairs the motion paths with two distinct, qualifying body boxes after the line and requires agreement across consecutive samples. The reported line points come from the observed body endpoints. The tool runs offline with OpenCV in the evaluation environment. [Head and body fusion](https://openaccess.thecvf.com/content_cvpr_2018_workshops/papers/w29/Henschel_Fusion_of_Head_CVPR_2018_paper.pdf) · [Forward-backward tracking validation](https://dspace.cvut.cz/entities/publication/4f34fd8a-dffe-4857-96c2-42ed3d5a8449)

```bash
python scripts/dev/evaluate_cctv_occlusion_motion.py tests/fixtures/cctv-twilight-g336-occlusion.json \
  --video /path/to/original-g336.avi --body-trace /path/to/boxes.jsonl
```

Use the checksummed original recording and the 4 fps YOLO11l crop trace from the crowded walkway replay. The difficult pair retained both motion paths in all eight reviewed overlap frames, with a median head-center error of 8.26 pixels. The body detector agreed with both paths at 52.75 and 53.00 seconds. The two-sample result corroborates both crossings retrospectively at 53.00 seconds, after the live scoring window. With one destination body observation supplied per frame, the same motion paths yielded zero confirmations. The earlier overlapping pair from the same recording also yielded zero confirmations with this method. [Reviewed G336 recording](https://mevadata-public-01.s3.amazonaws.com/drop-5-mevid/2018-05-16.21-10-08.21-15-08.school.G336.r13.avi)

### Prompted masks through a pedestrian overlap

On the 21-second G336 excerpt containing both overlapping pairs, mirroring, brightness changes, shifted crops and combining up to two crop views still yielded at most four of five expected person crossings with zero extras. The YOLO11m segmentation crop with box- or mask-based suppression also missed the second pedestrian in the difficult pair. [Test-time augmentation](https://docs.ultralytics.com/usage/cfg/#validation-settings) · [YOLO11 model variants](https://docs.ultralytics.com/models/yolo11/)

The [prompted mask evaluation](../scripts/dev/evaluate_cctv_occlusion_masks.py) initializes Meta SAM 2.1 Hiera Tiny from the two observed YOLO11 body boxes at 47.25 seconds. It checks for two adequately sized masks with low overlap, estimates the foot position from the bottom of each mask, and requires two consecutive samples that corroborate both crossings. The reviewed head positions are used to score mask assignment. They do not guide the prompts or the crossing check. [Meta SAM 2.1](https://github.com/facebookresearch/sam2/tree/2b90b9f5ceec907a1c18123530e92e794ad901a4)

Both crossings were corroborated at recording time 50.00 seconds, within the reviewed 51.50-second deadline. The CPU run took 24.88 seconds to reach that result on an M1 Pro after loading the checkpoint. All 16 reviewed heads had pixels from their assigned mask in a 7 × 7 pixel neighborhood and 11 had exclusive assignments. In the control, passing the first seed box for both objects yielded zero paired crossing observations. The full-precision MPS run exhausted local GPU memory. The mixed-precision run completed with CPU fallback and yielded zero paired crossing observations. This research replay covers one challenging overlap.

Use the official Hiera Tiny checkpoint and the same 4 fps YOLO11l box trace as the optical-flow replay. Use a separate research environment with PyTorch and the SAM 2 source pinned above. Add `--duplicate-seed` to repeat the control.

```bash
PYTHONPATH=/path/to/sam2 python scripts/dev/evaluate_cctv_occlusion_masks.py \
  tests/fixtures/cctv-twilight-g336-occlusion.json --video /path/to/original-g336.avi \
  --body-trace /path/to/boxes.jsonl --checkpoint /path/to/sam2.1_hiera_tiny.pt \
  --checkpoint-sha256 7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69
```

### Time-in-zone replay

The CAVIAR replay downloads two checksummed lobby videos and their hand-labeled tracks. It exercises a 30-second Observe rule at 4 fps. The longer waiting clip is the positive reference. The short walk clip checks that a clip shorter than the rule threshold yields no event. Use continuous authorized camera footage to measure false alerts per camera-hour. [CAVIAR data and attribution](https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/)

```bash
"$HOME/.local/share/proxigo/cctv-venv/bin/python" scripts/dev/benchmark_cctv_dwell.py /tmp/proxigo-caviar-dwell \
  --yolo11 "$PROXIGO_CCTV_YOLO11" --yolo11-sha256 "$PROXIGO_CCTV_MODEL_SHA256" --detection-gap 0.75
```

Use `--detection-gap 0` for a strict replay or `--provider CoreMLExecutionProvider` on a supported native macOS host. The report writes expected and observed dwell counts, event agreement with the labeled actor, and one-to-one box matches. With an allowed gap, the worker can preserve an accrued dwell timer when a track resumes nearby or the qualifying boxes overlap and agree with the prior motion. The gap contributes zero dwell time. The straight path between the detections must stay in the zone and clear of exclusions.

At 4 fps and a confidence floor of 0.25, adding `--rotation-degrees 90` improved YOLO11s on the four wide-angle lobby clips. On the shopping-centre view, zero rotation preserved the useful detections. These clips came from the EC Funded CAVIAR project/IST 2001 37540. [CAVIAR data and attribution](https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/)

| CAVIAR clip | Original YOLO11s box matches | 90° clockwise box matches |
| --- | ---: | ---: |
| Browse While Waiting 2 | 40/172 | 163/172 |
| Walk 1 | 29/250 | 88/250 |
| Browse 2 | 62/145 | 68/145 |
| Rest In Chair | 25/221 | 71/221 |
| One Shop One Wait 2, front view | 314/482 | 0/482 |

On the long waiting clip, the rotated worker averaged 18.9 ms per frame on an M1 Pro with CoreML, compared with 17.7 ms at zero rotation. With `--rotation-degrees 90 --detection-gap 1` and motion corroboration, it reported the labeled 30-second dwell. With a 0.75-second gap, it missed the event. The short walk clip produced zero events. Scenes with partial annotations contribute labeled box measurements, and measuring false alerts per camera-hour requires independently reviewed continuous footage. [NIST ActEV evaluation](https://actev.nist.gov/)

Add `--case Browse_WhileWaiting1 --case Browse_WhileWaiting2` for a multi-person negative and a long waiting positive. No labeled person in the first clip stayed in view for 30 seconds. The rotated YOLO11s profile at 4 fps and a one-second detection gap reported zero dwells in the shorter waiting clip and one 30-second dwell matching the labeled person in the long clip. [CAVIAR test scenarios](https://groups.inf.ed.ac.uk/vision/DATASETS/CAVIAR/CAVIARDATA1/)

Use `--storefront-dwell` to replay two annotated shopping-centre recordings with a 25-second rule at 4 fps. The benchmark uses the bottom centre of each labeled person box to score continuous visits inside the normalized zone `[.39, .49, .61, .77]`. An observation must overlap that person's box by at least 0.30 IoU after the visit reaches 25 seconds and before the person leaves. The report counts duplicate and unmatched observations separately. [CAVIAR shopping-centre recordings and ground truth](https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA2/)

```bash
"$HOME/.local/share/proxigo/cctv-venv/bin/python" scripts/dev/benchmark_cctv_dwell.py /tmp/proxigo-caviar-storefront \
  --storefront-dwell --provider CoreMLExecutionProvider --detection-gap 1 \
  --yolo11 "$PROXIGO_CCTV_YOLO11" --yolo11-sha256 "$PROXIGO_CCTV_MODEL_SHA256" \
  --detail-region .31 .275 .69 .83 --detail-pose "$PROXIGO_CCTV_YOLO11_DETAIL_POSE" \
  --detail-pose-sha256 "$PROXIGO_CCTV_YOLO11_DETAIL_POSE_SHA256" --detail-pose-fallback
```

With a one-second gap allowance, `OneShopOneWait2front` contains one qualifying visit and `OneShopOneWait1front` contains none. Both pose profiles matched the waiting person once and produced no duplicate observations. Mean processing time is for the positive clip on the M1 Pro with CoreML.

| YOLO11s ground profile | Labeled visits matched | Observations on comparison clip | Mean ms per frame |
| --- | ---: | ---: | ---: |
| Standard detail crop | 0/1 | 0 | 35.7 |
| On-demand YOLO11s pose crop | 1/1 | 0 | 44.6 |
| YOLO11l pose crop on every frame | 1/1 | 0 | 56.0 |

The on-demand pose pass ran on 112 of 234 samples in the positive clip and 196 of 220 in the comparison clip. A separate live RTSP run with the small fallback profile reported 25.01 seconds in zone. Chrome displayed the alert and its observed dwell time. These two clips establish fixture-level behavior. A site-level false-alert rate requires continuous footage from the site.

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
