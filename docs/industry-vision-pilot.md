# Global View industry vision pilot

These workflows use the existing site configuration and camera worker. Publish a draft before expecting a result from an enrolled worker. Keep the deployment's approved Cloud or On-premises compute placement for each camera.

## Logistics dock occupancy

1. Assign the dock camera to the dock boundary. In a site draft, go to **Devices → Edit device → CCTV analytics rules → Add dock monitoring**.
2. Draw the bay around the vehicle ground-contact area in the image, or start with the keyboard-editable rectangle. Leave adjacent traffic lanes outside the region, and add exclusion regions if needed. Save the device and publish the draft.
3. Open **Posture → Dock occupancy** or open the boundary detail. The row identifies the boundary, camera, and rule. **Open camera** goes to that camera. **Review arrival** looks up the recorder at the arrival time when the event is available.

The default rule confirms occupancy after two continuous seconds of vehicle evidence. It clears after five seconds of evaluated vacancy. A brief missed detection preserves the confirmed state and breaks a pending transition. A restart, long frame gap, or new rule version starts at Unknown. An initially occupied bay establishes an occupancy duration without creating an arrival. Posture shows Unknown when the current worker sample is more than fifteen seconds old, the feed is unavailable, or the released rule is paused.

An Occupied result describes the image region during the available samples. An arrival event describes the transition from Vacant to Occupied. Use a separately recorded trailer or shipment ID for appointments. Anonymous occupancy can inform a schedule exception, while the check-in record supplies identity. [Oracle Yard Management guide](https://docs.oracle.com/cd/E26401_01/doc.122/e53453/T632044T632632.htm)

Open **Shared dock schedule** to preview and publish up to 100 appointment windows from a CSV. The gateway retains the active schedule and operator check-ins. A unique arrival is associated by bay and time, and overlapping matches appear as **Ambiguous match**. See the [shared dock workflow](global-view-vnext.md#share-a-dock-schedule).

## Factory safety review

1. Assign the camera and boundary to the same floor in the site draft. Under **CCTV analytics rules**, add **Person in vehicle lane** or **Stopped vehicle in marked walkway** and draw the corresponding image region.
2. Publish the configuration. The presets run in Observe mode with default confirmation times of one second for a person in a vehicle lane and six seconds for a stopped vehicle in a walkway.
3. In **Posture → CCTV analytics → Observe mode review**, choose a **Factory safety** filter. Review live or replay results by camera and published version. A reviewer can save **Confirmed**, **False detection**, or **Unable to verify** with a note and check available footage.

The stopped-vehicle preset also covers a vehicle already visible when the worker starts sampling. Review placement, camera perspective, and site-specific day and night examples before deciding how to use these results in operations. Evaluate enough continuous footage to count missed events as well as false detections. The workflow creates observations for human review.

## Held-open door and camera

1. In the site draft, link the access appliance to its camera and assign both to the same monitored boundary. Add a camera crossing event source, using a published CCTV tripwire in alert mode or an enrolled camera adapter.
2. Open **Alerts → Door and camera correlation**. Enable **Group new matching events** and **Include held-open doors with one eligible crossing**. Set the occurrence window and late-arrival allowance. Save the rule and publish the site draft.
3. Open the resulting incident and its replay timeline. The first received event remains the root. A unique companion joins the existing incident, with both timestamps visible. Forced-open matches have priority when a crossing can match either door event.

The gateway pairs available events within the same camera, boundary, and configuration version. A held-open event with ambiguous eligible matches stays separate. Expired events remain available in history, and duplicate deliveries preserve the original incident. The checklist and timeline are shared with authorized operators. Pairing corroborates timing and camera coverage. Access grants and counts of people using a credential would be separate evidence for a future tailgating workflow. [Milestone XProtect Access](https://doc.milestonesys.com/xprotect/xprotect-smart-client/2026r1/en/xprotect-access.html)

## Simulated assembly inspection

1. In a site draft, add **Inspection station ID (pilot)** to a vision device and publish. The example fixture uses `assembly-line-2`.
2. Start an existing loopback gateway with the camera's ID and a distinct simulated adapter token configured through `--telemetry-devices`. Run a fixture against that gateway using the camera ID, a private token file, and an ISO timestamp after publication. Retry with the same run ID and timestamp.

```bash
python scripts/dev/replay_inspection_results.py \
  --gateway http://127.0.0.1:8765 --camera station-camera \
  --token-file /absolute/private/station.token --run-id trial-01 \
  --start-at "$(python -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"
```

3. In **Posture → Inspection results**, select a camera and Pass or Fail. Select a result to see the station, version, recipe, component, part or lot, and boundary. A failed result can open the station camera or look up its recording at the result time.

Completed cycles carry stable IDs and separate pass/fail metadata. The simulator skips idle, uninspected, and undetected states. The local gateway validates the station mapping published at occurrence, scopes access by the assigned camera, and stores results separately from incident traffic. The result is an operator-visible simulation. Vendor and OPC UA clients require validation against the actual station, including result-state mapping, restart behavior, transport, timestamps, and representative parts under the station lighting. [iFactory component presence](https://ifactoryapp.com/ai-vision-camera/assembly-verification-component-presence-detection-ai-vision) · [OPC UA Machine Vision result events](https://reference.opcfoundation.org/specs/OPC-40100-1/8.3.8) · [Siemens Inspekto VIS integration, section 3.1.2.10](https://support.industry.siemens.com/cs/attachments/109977467/109977467_SiemensInspekto_ProfinetIntegration_DOC_V13_en.pdf)

When evaluating future surface or assembly models, measure defect escape and false reject rates against labeled plant samples and retain the result ID, recipe version, and failure evidence. MVTec AD is a research benchmark with a non-commercial license. Customer-approved fixtures and data with the necessary use rights can support a shippable model evaluation. [iFactory inspection traceability](https://ifactoryapp.com/ai-vision-camera/ai-vision-inspection-audit-trail-traceability) · [MVTec AD license](https://www.mvtec.com/research-teaching/datasets/mvtec-ad)

## Verification and evidence

From the repo root, use the Python environment with ONNX Runtime and the existing CCTV test dependencies, plus Chrome, Node, FFmpeg, and MediaMTX. Keep fixture media and gate output in private local paths outside the checkout. Use the reviewed origin and hash in [the dock fixture manifest](../tests/fixtures/cctv-industry-g336-dock.json).

| Stage | Local evidence |
| --- | --- |
| `baseline` | Gateway contracts, production build, corpus contract, and 570 annotated MEVA cases |
| `dock` | State machine tests, Chrome publication and Posture, reviewed MEVA replay |
| `safety` | Deterministic positive and negative tracks, camera-scoped review, Chrome controls |
| `security` | Opt-in, ambiguity, priority, delivery grace, and two operators across a Chrome and gateway restart |
| `integrated` | Full gateway and frontend checks, twelve-camera entrance and stair tests in Chrome, optional mixed dock/entrance RTSP, and case-by-case corpus comparison |
| `inspection` | Station and camera mapping, durable scoped results, simulator replay, Chrome review |

```bash
python scripts/dev/run_industry_vision_gate.py --plan integrated
python scripts/dev/run_industry_vision_gate.py safety --output-dir /absolute/private/test-runs
python scripts/dev/run_industry_vision_gate.py integrated \
  --provider CoreMLExecutionProvider --reference-report /absolute/private/baseline/meva-corpus.json \
  --output-dir /absolute/private/test-runs
```

Set `PROXIGO_CCTV_YOLO11` to the pinned YOLO11s ONNX export. Baseline and integrated need `PROXIGO_CCTV_CORPUS`, the external MEVA corpus cache. The dock stage requires `PROXIGO_CCTV_DOCK_VIDEO`, the original verified G336 recording. Integrated needs `MEDIAMTX`, `PROXIGO_CCTV_LOAD_VIDEO` with the G326 entrance fixture, and `PROXIGO_CCTV_STAIRS_VIDEO` with the verified G639 source.

Set `PROXIGO_CCTV_DOCK_VIDEO` for the integrated stage to include one G336 dock camera alongside eleven G326 entrance cameras. This checks live sampling, the worker heartbeat, linked arrival evidence, and Posture at twelve concurrent streams.

The runner executes one heavy model check at a time and writes a receipt with command outcomes, elapsed time, input hashes, source fingerprints, artifact paths, and failure reasons. Freeze source edits during benchmark runs. Keep the receipt with any replay or browser logs when handing off. Before a site pilot, review its own continuous positive and negative footage across lighting, occlusion, camera placement changes, and the expected mix of simultaneous streams.
