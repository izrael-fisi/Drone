# Global View AI and sensor analytics pilot

Start with one published gate and its registered camera, recorder, and access controller. Use site-local evidence and the site's configured compute policy. Each feature is enabled independently through the private gateway file passed to `--site-intelligence`. Keep this file, model credentials, and fixture output outside Git.

## Enable the features

The following example enables the health baseline and both generative workloads. Set `clipReview` to `false` to run a text-only pilot. Replace the boundary and camera IDs with the published site IDs. A rectangle uses `[left, top, width, height]` percentages of the original image.

```json
{
  "deviceDegradation": true,
  "ai": {
    "provider": "bedrock",
    "region": "us-east-1",
    "approveUsGeography": true,
    "handoff": true,
    "clipReview": true,
    "cloudClips": true,
    "clipBoundaries": ["front-gate"],
    "dailyQuota": 30,
    "masks": { "front-camera": [[0, 0, 20, 25]] }
  }
}
```

Start the gateway with its existing site-access, recorder, and appliance settings and add `--site-intelligence /private/site-intelligence.json`. A supervisor enables cloud clips only after this site approves the transfer of selected frames and its masks are checked against the actual camera image. Cloud clip setup fails when the approval is absent. Set AWS Bedrock retention to `none` where the selected model permits it, confirm the effective retention and invocation logging in the target account, and validate all destinations of the US inference profile before using customer data. Use workload IAM credentials on AWS or a short-lived IAM Roles Anywhere profile at the site. Give the gateway role only the approved Bedrock invocation permissions. Run a synthetic text and masked-frame request first to warm the two structured-output schemas and verify the provider contract. [Bedrock model regions](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html) · [Bedrock retention](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html) · [IAM Roles Anywhere](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/credential-helper.html)

Install the optional cloud dependency on the gateway.

```bash
python -m pip install -r services/camera-gateway/ai-requirements.txt
```

For on-premises inference, set `provider` to `on-premises` and set `localOrigin` to a private HTTPS origin on the gateway's allowed source network. Put the bearer credential in the file named by `localTokenFile`. Set `localCa` to the private CA file when needed. A same-host fixture may use loopback HTTP. Qualify a pinned release of vLLM with the pinned Qwen3-VL-8B-Instruct weights, serving the model name configured by `localModel`. Test text, multiple timestamped JPEGs, and structured outputs before authorizing operators. A local endpoint failure leaves the manual workflow available. [Qwen model card](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) · [vLLM multimodal serving](https://docs.vllm.ai/en/stable/features/multimodal_inputs/)

To enable gate corroboration, add a `gateRadars` array to the same top-level file. Enroll the published RF appliance, camera, and door in the same boundary and floor. Link the door to the camera in the site configuration. The pilot expects a specific Axis motion profile. Sign off on the door source clock before setting `doorClockVerified`.

```json
[
  {
    "sensorId": "front-radar",
    "boundaryId": "front-gate",
    "cameraId": "front-camera",
    "doorId": "front-door",
    "axisProfile": "Channel0Profile1",
    "address": "https://192.0.2.25/onvif/device_service",
    "caFile": "/private/radar-ca.pem",
    "username": "reader",
    "password": "<private credential>",
    "doorClockVerified": true
  }
]
```

The radar adapter checks the device clock and the advertised profile. It collects local motion-state changes through a read-only PullPoint subscription. Use **Posture → Gate radar status** to identify mapping, profile, clock, or connection issues. [Axis radar configuration and events](https://developer.axis.com/vapix/radar/radar-configuration/)

## Enable automatic AI triage

A site supervisor opens **Posture → Automatic AI triage** and selects **Enable automatic AI triage**. The switch appears when an AI provider is configured. Set `ai.allowAutomatedTriage` to `true` for an automatic-only site, or `false` to hide the switch. The switch starts off. It applies to new camera-linked incidents after activation.

The gateway waits about 30 seconds for related telemetry, then uses the site's configured provider for one review per incident. It sends related event metadata in the incident camera and boundary scope. The model selects relevant event references. The gateway builds the short summary and supplies exact UTC timestamps from the original event records. The automatic review in **Alerts** shows those facts and key timestamps. Select a timestamp to open replay and seek when the recorder has an available clip.

The default automatic limit is two-thirds of the site's daily AI quota, rounded down with a minimum of one. Configure it with `ai.automaticDailyLimit`. A provider failure, interrupted review, or exhausted quota leaves the recorded key events available. An interrupted review recovers without a second model request. Turning the switch off stops future submissions and keeps completed reviews. The setting survives gateway restarts. Restoring a backup starts with the switch off. The gateway keeps reviews for up to 30 days.

## Operator workflow

- **Draft handoff** appears under **Alerts → Response checklist and handoff** after taking ownership. Confirm the processing location before generating. Add selected incident notes only when needed. Review the citations, use the draft to fill the editable handoff form, and select **Save handoff** to share the approved text and source references. An expired draft or changed incident revision requires a new draft. Job details expire after 24 hours. The gateway retains up to 30 days of job usage metadata and omits ephemeral drafts from site backups.
- **Device degradation warnings** in **Posture** shows metric history, related boundaries, and recent recoveries. The gateway needs two samples in a bucket and at least 48 comparable five-minute buckets across seven days for each metric. Day is 06:00–18:00 site-local time. Warning and recovery each require three consecutive measured buckets. Maintenance and known outages are excluded from baseline updates. Recorder metrics depend on automatic recovery being enabled. Clock metrics use whatever recent device clock measurements the gateway has available.
- **Gate corroboration** appears on matching access incidents. The gateway matches reported activity within ten seconds of the door event and waits for delayed deliveries. Inspect the event links and record Confirmed, Incorrect correlation, or Unable to verify. The review label applies to the correlation shown and is separate from incident disposition.
- **Review with AI** appears in replay for approved boundaries with a linked camera. The site recorder supplies the event-time frames. Hovering or opening the control uses no model quota. Select **Analyze event frames** to submit. **Seek to** opens the exact recording used for review at the cited media offset. Missing event-time footage displays **Unable to verify**. Original recorder evidence follows the existing hold and export policy.

## Acceptance and verification

Run the dedicated gate on a fixed source tree. It uses a local model protocol fixture, read-only radar protocol fixture, and an existing MP4 fixture.

```bash
python scripts/dev/run_industry_vision_gate.py site-intelligence --output-dir /private/verification
```

The integrated gate also runs a twelve-camera YOLO11 workload with an in-flight AI request and live HLS playback. Use the existing pinned-model and media prerequisites from the [enterprise verification guide](global-view-enterprise-operations.md#verification).

Before customer rollout, review at least 30 forced-door incidents and 50 independently labeled clips for each approved model placement. Record handoff wall-clock time, corrections, source coverage, missing next steps, clip answer and abstention rates, cited-frame accuracy, and per-request cost. Compare health warning lead time and false warnings per camera-day against actual maintenance records. Compare the gate workflow with and without corroboration on continuous footage, recording missed events and false alerts per monitored hour. Compare candidate ranking against these rates using held-out shifts. Enable ranking only after a measured precision gain at matched recall. [NIST ActEV](https://actev.nist.gov/srl)
