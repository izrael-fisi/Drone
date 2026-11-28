# Global View AI and sensor analytics pilot

Start with one published gate and its registered camera, recorder, and access controller. Use site-local evidence and the site's configured compute policy. Each feature is enabled independently through the private gateway file passed to `--site-intelligence`. Keep this file, model credentials, and fixture output outside Git.

## Enable the features

The following example enables the health baseline, automatic triage, handoff, frame review, and video review. Set `clipReview` and `videoReview` to `false` to run a text-only pilot. Replace the boundary and camera IDs with the published site IDs. A rectangle uses `[left, top, width, height]` percentages of the original image.

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
    "videoReview": true,
    "cloudVideo": true,
    "clipBoundaries": ["front-gate"],
    "dailyQuota": 30,
    "masks": { "front-camera": [[0, 0, 20, 25]] }
  }
}
```

Start the gateway with its existing site-access, recorder, and appliance settings and add `--site-intelligence /private/site-intelligence.json`. Nova 2 Lite handles automatic triage, handoff, and frame review through `us.amazon.nova-2-lite-v1:0`. Pegasus 1.5 handles video through `us.twelvelabs.pegasus-1-5-v1:0`. Existing sites that grant invocation only to Haiku must grant the Nova inference profile and foundation-model ARNs in all destinations for the source region. Grant the analogous Pegasus permissions and activate its Marketplace subscription for sites that enable video review.

Approve cloud frame transfer with `cloudClips` and cloud video transfer separately with `cloudVideo`. Verify configured masks against the original image. For video review, the gateway prepares a masked, silent excerpt of up to 12 seconds around the event. Playback links use the original recorder clip. Existing frame-only configurations require explicit video approval before offering Pegasus. Set AWS Bedrock retention to `none` where the selected model permits it, confirm effective retention and invocation logging in the target account, and validate all destinations of the US profiles before using customer data. Use workload IAM credentials on AWS or a short-lived IAM Roles Anywhere profile at the site. [Nova regions](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-lite.html) · [Pegasus regions](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-twelvelabs-pegasus-v1-5.html) · [Bedrock retention](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html) · [IAM Roles Anywhere](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/credential-helper.html)

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

Automatic triage shares the site's automatic allowance. An explicitly configured `ai.automaticDailyLimit` can further limit triage. A provider failure, interrupted review, or exhausted quota leaves the recorded key events available. An interrupted review recovers without a second model request. Turning the switch off stops future submissions and keeps completed reviews. The setting survives gateway restarts. Restoring a backup starts with the switch off. The gateway keeps reviews for up to 30 days.

## Enable automatic frame review

On a site with Bedrock frame review approved, a supervisor can open **Posture → Automatic frame review** and select **Enable automatic frame review**. The gateway processes new camera-origin incidents in the configured `ai.clipBoundaries`. It waits for the recorder window, then sends one event-time frame with the configured privacy masks applied to Nova. Each incident gets one automatic review. The site starts with the switch off, and the switch persists across gateway restarts. A restored backup starts with the switch off.

The result appears in **Alerts → Automatic camera review** for operators with access to the incident camera. **Open automatic review in replay** shows the Nova draft and, when available, Pegasus segments with start and end playback links. Draft results are available for up to 24 hours. Playback uses the original recording and follows the recorder cache and evidence hold policy.

After a cached clip expires, the timestamp links ask the authorized recorder for the event interval and align the cited time to the new clip. Replay still shows the event timeline when the footage has aged out of the recorder. Use an evidence hold for clips that need to remain available for a case.

## Enable automatic video fallback

On a site with approved Nova frame review and Pegasus video review, a supervisor can open **Posture → Automatic video fallback** and select **Enable automatic video fallback**. The setting starts off and persists across gateway restarts. A restored backup starts with fallback off.

The trigger is an **Unable to verify** result from Nova during an automatic review or after an operator selects **Analyze event frames** in replay. The gateway then prepares the same event recording for Pegasus and keeps both draft observations in the review. A missing event frame or a provider failure leaves the operator's manual review controls available. Each dispatched fallback consumes a second model call from the site's daily limit. The gateway checks the setting and remaining quota just before submitting the clip. Its private job audit records Nova usage and the additional fallback latency, analyzed video seconds, and Pegasus output tokens.

## Manage site AI usage and quota

Open **Posture → AI usage and quota** to see calls reserved today for operator requests, automatic triage, automatic frame review, and Pegasus fallback. The panel shows the remaining operator capacity, the amount protected from automation, and the remaining automatic allowance. The accounting day resets at midnight UTC.

A site supervisor can set a daily total from 1 to 500 and reserve at least one call for operators. The initial total comes from `ai.dailyQuota` and defaults to 30. The initial reserve is one-third of the total, rounded up, giving the default site 10 protected calls and an automatic allowance of 20. The reserve can equal the daily total to pause all automatic requests. Explicitly requested frame, video, and handoff reviews can use any remaining calls. An automatic Pegasus fallback always uses the automatic allowance, including after an operator requests a frame review. At the automatic limit, operators can request a Pegasus video review directly.

Edits take effect for future reservations and persist across gateway restarts. Existing reservations still count toward the current day after an edit. The counters measure quota use and include requests in progress. Backup snapshots preserve the policy and the day's quota totals as of the snapshot while omitting ephemeral draft jobs.

## Operator workflow

- **Draft handoff** appears under **Alerts → Response checklist and handoff** after taking ownership. Confirm the processing location before generating. Add selected incident notes only when needed. Review the citations, use the draft to fill the editable handoff form, and select **Save handoff** to share the approved text and source references. An expired draft or changed incident revision requires a new draft. Job details expire after 24 hours. The gateway retains up to 30 days of job usage metadata and omits ephemeral drafts from site backups.
- **Device degradation warnings** in **Posture** shows metric history, related boundaries, and recent recoveries. The gateway needs two samples in a bucket and at least 48 comparable five-minute buckets across seven days for each metric. Day is 06:00–18:00 site-local time. Warning and recovery each require three consecutive measured buckets. Maintenance and known outages are excluded from baseline updates. Recorder metrics depend on automatic recovery being enabled. Clock metrics use whatever recent device clock measurements the gateway has available.
- **Gate corroboration** appears on matching access incidents. The gateway matches reported activity within ten seconds of the door event and waits for delayed deliveries. Inspect the event links and record Confirmed, Incorrect correlation, or Unable to verify. The review label applies to the correlation shown and is separate from incident disposition.
- **Review with AI** appears in replay for approved boundaries with a linked camera. **Analyze event frames** checks occupancy at the event time and links to the cited recorder frames. **Analyze video clip** submits the video excerpt to Pegasus and shows draft observations with clickable start and end times. No matching segments may mean visibility was insufficient. Inspect the recording before closing the incident. Original recordings follow the existing hold and export policy.

## Acceptance and verification

Run the dedicated gate on a fixed source tree. It uses a local model protocol fixture, read-only radar protocol fixture, and an existing MP4 fixture.

```bash
python scripts/dev/run_industry_vision_gate.py site-intelligence --output-dir /private/verification
```

The Chrome test for both cloud models is opt-in. From `desktop-app`, set `AWS_PROFILE` to a test profile with both US models enabled, set `PROXIGO_BEDROCK_TEST_PYTHON` to Python with the gateway AI dependencies, and set `PROXIGO_BEDROCK_TEST_CLIP` to a public MP4 with a clearly visible person at its midpoint. Then run:

```bash
npx playwright test tests/e2e/enterprise-upgrades.spec.ts -g "live Bedrock reviews"
```

To measure fallback, set `PROXIGO_BEDROCK_FALLBACK_TEST_CLIPS` to a JSON array of public MP4 paths for reviewed empty scenes on which Nova abstains. Use the same AWS profile and Python settings and run:

```bash
npx playwright test tests/e2e/enterprise-upgrades.spec.ts -g "live Bedrock video fallback"
```

The test compares the same scene with fallback off and on. It attaches both job audits and verifies the Pegasus start and end playback links in Chrome.

To test automatic frame review, set `PROXIGO_BEDROCK_AUTO_FRAME_POSITIVE` to the path of a public MP4 with clear occupancy at its midpoint. Set `PROXIGO_BEDROCK_AUTO_FRAME_UNCERTAIN` to a reviewed empty scene with a midpoint on which Nova abstains. Use the same profile and Python settings. The test enables both site settings, submits camera telemetry, and checks an independent operator's results and playback in Chrome. It attaches event-to-display latency and each model's metered usage.

```bash
npx playwright test tests/e2e/enterprise-upgrades.spec.ts -g "automatically reviews new camera incidents"
```

The quota Chrome test uses 12 registered cameras, fixture recordings, and simulated model responses. It verifies that automatic frame reviews and Pegasus fallbacks share their allocation, then confirms operator requests can use the protected calls.

```bash
npx playwright test tests/e2e/enterprise-upgrades.spec.ts -g "12-camera AI quota"
```

The integrated gate also runs a twelve-camera YOLO11 workload with an in-flight AI request and live HLS playback. Use the existing pinned-model and media prerequisites from the [enterprise verification guide](global-view-enterprise-operations.md#verification).

Before customer rollout, review at least 30 forced-door incidents and 50 independently labeled clips for each approved model placement. Record handoff wall-clock time, corrections, source coverage, missing next steps, clip answer and abstention rates, cited-frame accuracy, and per-request cost. Compare health warning lead time and false warnings per camera-day against actual maintenance records. Compare the gate workflow with and without corroboration on continuous footage, recording missed events and false alerts per monitored hour. Compare candidate ranking against these rates using held-out shifts. Enable ranking only after a measured precision gain at matched recall. [NIST ActEV](https://actev.nist.gov/srl)
