# Global View commissioning and industry operations

These workflows run on the site gateway and use published site configuration and camera access. Connect to the gateway from **Posture**. Publish the devices and boundaries before testing the workflows.

## Commission a tripwire

Start the gateway with `--commissioning-worker /private/commissioning.json`. Keep the selected Python environment and model private. A sample configuration looks like this.

```json
{
  "python": "/absolute/cctv-venv/bin/python",
  "model": {
    "path": "/absolute/models/yolo11s.onnx",
    "sha256": "05b04fd920332046673160ec15eca13de3e005027c5508f16a3e09101bb1348c",
    "provider": "CoreMLExecutionProvider"
  }
}
```

1. Publish a directional tripwire on a registered camera with a recorder. Save a candidate draft containing the same rule ID, object, direction, and boundary.
2. Go to **Posture → CCTV analytics → Commission a tripwire**. Choose the camera, rule, and UTC recording time. Open the clip and create a comparison.
3. Mark expected crossings using the playback position or clip offsets. Set the start and end of the reviewed interval and save it. Leave the crossing list empty for a reviewed interval with no crossings. Select **Run comparison**.
4. Review matched, missed, and extra crossings for both policies. Select a result to seek in the clip. A second authorized operator can reopen the saved run.

The replay uses the same pinned YOLO11 export for both policies and runs locally at 640 × 360 and 4 fps. Runs retain policy snapshots and the clip fingerprint. Recorder retention determines whether a later replay can retrieve that clip. Use a longer set of independently reviewed clips before setting customer alert thresholds. [NIST ActEV](https://actev.nist.gov/)

## Share a dock schedule

Assign a camera with an active dock occupancy rule to each bay. Go to **Posture → Dock occupancy → Shared dock schedule**. In the site’s timezone, choose the day on which the appointments start. A supervisor can import, preview, and publish the CSV. Its first row is `appointmentId,boundaryId,startsAt,endsAt`. Use timezone-qualified timestamps and the published boundary IDs. Publishing replaces that day’s active schedule and keeps its revision history.

Operators can select a bay and enter a shipment reference or select a published appointment at that bay. The check-in supplies shipment identity. Camera observations supply arrival and occupancy evidence. A unique match can be marked **Overstay** while that same arrival is still observed in the occupied bay. An unavailable feed shows **Unknown**. [Oracle Yard Management](https://docs.oracle.com/cd/E26401_01/doc.122/e53453/T632044T632632.htm)

## Review a door passage

In a site draft, link the access device to a camera and select its **Inbound passage tripwire**. Use a directional person rule on the camera. Assign both devices to the rule’s boundary and publish. Map the access adapter or XProtect event types to `access-granted`, `door-opened`, and `door-closed`. Use stable event IDs and synchronized timestamps.

Open **Alerts → Door passage review**. A completed episode with one unambiguous grant, current camera evidence, and distinct inbound tracks can produce a **Possible extra passage** item. The gateway waits 15 seconds after the door closes for delayed events. The maximum episode is 30 seconds. If the required evidence is unavailable the review shows **Insufficient evidence**. Select an episode to replay its original source events and look up recordings when supported. Passage episodes are Observe-mode evidence. [Milestone XProtect Access](https://doc.milestonesys.com/xprotect/xprotect-access-for-users/2026r1/en/using-the-events-list)

## Use camera-native ONVIF analytics

On a supported device, configure a directional line in the camera’s own analytics settings. In Global View, select **Use camera-native ONVIF events** on its corresponding CCTV tripwire and publish. Start the private gateway with `--onvif-analytics /private/onvif-analytics.json`. Enroll one entry per camera using the fields below.

```json
[
  {
    "cameraId": "loading-camera",
    "address": "https://192.0.2.24/onvif/device_service",
    "username": "read-only-account",
    "password": "replace-in-private-file",
    "ruleId": "entrance-tripwire",
    "deviceRule": "camera-configured-line",
    "videoSourceToken": "camera-video-source-config"
  }
]
```

Use trusted camera endpoints within the gateway’s allowed source networks. The adapter probes for the standard `RuleEngine/LineDetector/Crossed` topic, checks the device clock, and uses a read-only PullPoint subscription. **Posture → CCTV analytics → Camera-native analytics** shows capability, connectivity, and last valid event time. The native rule consumes camera observations in Observe mode. Legacy worker rules on other cameras continue through their configured compute placement. The test fixture validates the protocol exchange. Capability and topic mapping require a physical camera check before customer use. [ONVIF Profile M](https://www.onvif.org/profiles/profile-m/) · [ONVIF Analytics Service specification](https://www.onvif.org/specs/srv/analytics/ONVIF-Analytics-Service-Spec-v1812.pdf)

## Connect a read-only inspection station

Enroll the station ID on its camera in a site draft. After publication, configure an adapter token for that camera with the gateway’s `--telemetry-devices` file. Install `services/camera-gateway/opcua-requirements.txt` into a dedicated adapter environment. Run `python services/camera-gateway/inspection_opcua.py --config /private/inspection-opcua.json`.

The private configuration contains `endpoint`, `gateway`, `cameraId`, `stationId`, `tokenFile`, `outbox`, and the station’s `eventType` NodeId. The optional `eventSource` defaults to the server notifier. Map the event field names with `fields` using `resultId`, `occurredAt`, `recipeId`, `componentId`, `partId` and/or `lotId`, `outcome`, and optional `simulated` and `partial` flags. Map the station’s terminal statuses to `pass`, `fail`, and `incomplete` in `outcomes`. Partial notifications wait for a terminal result. Configure `security` with `certificate`, `privateKey`, and `serverCertificate` for the encrypted connection. The `loopbackFixture` option is reserved for local protocol tests.

Use **Posture → Inspection results** to filter by camera, outcome, recipe, and lot. **Export filtered results** downloads up to 1,000 results within the operator’s camera permissions. Failed results can open the camera or request its recording at the result time. The adapter stores undelivered records in its private outbox, and the gateway deduplicates by station and result ID. The protocol pilot uses a simulated result event and mapping. Physical acceptance still requires a station-specific mapping and reviewed sample parts. [OPC UA Machine Vision](https://reference.opcfoundation.org/specs/OPC-40100-1/8.3.8) · [FreeOpcUa client](https://github.com/FreeOpcUa/opcua-asyncio)

## Run the verification gates

The runner accepts `commissioning`, `yard`, `passage`, `native-camera`, `opcua`, and `vnext-integrated`. It writes private receipts outside the checkout with source fingerprints, fixture hashes, commands, and reports.

Use the existing YOLO11, MEVA, FFmpeg, MediaMTX, and Chrome setup from the [industry vision pilot](industry-vision-pilot.md). Set `PROXIGO_OPCUA_PYTHON` to the Python with the optional OPC UA dependency and `PROXIGO_ENTERPRISE_PYTHON` to the Python with enterprise requirements. Set `PROXIGO_CCTV_DOCK_VIDEO` to include the existing mixed dock load. Use the same provider as the baseline corpus report.

```bash
python scripts/dev/run_industry_vision_gate.py --plan vnext-integrated
python scripts/dev/run_industry_vision_gate.py vnext-integrated \
  --provider CoreMLExecutionProvider --reference-report /private/baseline/meva-corpus.json \
  --output-dir /private/verification
```
