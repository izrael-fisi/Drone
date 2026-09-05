# Change playbook and validation ladder

Use this playbook for every navigation-stack change. Its purpose is to prevent a locally appealing model or performance change from weakening the system's ability to reject bad global localization.

## Change request template

Copy this into a pull request or task before implementation.

```markdown
## Intent
What operational limitation is being addressed? What is explicitly out of scope?

## Affected contracts
- [ ] frame/time/calibration   - [ ] map release
- [ ] model adapter            - [ ] geometry gate
- [ ] hypothesis/state machine - [ ] factor graph
- [ ] PX4 boundary             - [ ] logging/replay

## Assumptions and operating envelope
Camera, altitude, attitude, speed, GSD, lighting, map epoch, and companion hardware.

## Safety behaviour
What evidence is required to publish? What happens when confidence is absent, stale, ambiguous, or inconsistent?

## Verification
Unit tests, replay data, expected metrics/thresholds, fault injections, and the
required prop-off hardware/flight-validation stage.

## Rollback
Config/model/map version to restore; how an in-flight/field regression is detected.
```

## Standard implementation sequence

1. **State the contract.** Add or revise a typed message/config field before adding behaviour. Include units, frame, timestamp clock, validity, and covariance semantics.
2. **Write the rejection path first.** Decide how invalid input, stale data, no match, multiple plausible matches, a degenerate geometry fit, or an optimizer exception appears in diagnostics and blocks publication.
3. **Create deterministic fixtures.** Include nominal, repetitive-pattern alias, too-few-inlier, non-planar, wrong-calibration, stale-map and time-jump cases.
4. **Make inference replaceable.** Put checkpoint/device/tensor code in an adapter. The estimator consumes its stable typed output, never a framework tensor or uncalibrated score.
5. **Calibrate confidence.** Scores are not probabilities until calibrated with held-out data. Tune thresholds on flights disjoint from model/map training and report false accepts, not only recall.
6. **Replay before live use.** Benchmark with the exact map/model/config digest. Require a deterministic decision trace.
7. **Run the applicable ladder below.** Keep all results, raw evidence, and maps linked to the change request.

## Validation ladder

| Stage | Purpose | Required evidence | Stop condition |
| --- | --- | --- | --- |
| static/unit | contract, coordinate, gate correctness | fast tests; ENU/NED fixtures; property tests for malformed inputs | any silent validity/covariance/frame conversion defect |
| recorded replay | accuracy and rejection with realistic timing | flight bags; frozen map/model/config; decision trace | unexplained non-determinism, false accept, or budget breach |
| stress replay | discover unsafe tails | blur, low light, dynamic masks, map age, aliasing, delay, dropped/reordered frames | output remains `TRACKING` after evidence no longer warrants it |
| prop-off PX4 bench | estimator/transport behaviour | MAVLink packet capture; PX4 estimator status; scripted loss/recovery playback | wrong frames/timestamps/covariance or unsafe loss action |
| hardware timing/load | physical timing/load | P50/P95/power/thermal measurement; failsafe behaviour observation | deadline misses, clock drift, unstable estimator innovation |
| constrained flight | operational validation | independent observer; approved envelope; complete logs; emergency plan | any unexplained estimate/vehicle divergence |

Flight only progresses when the prior stage's acceptance criteria have been reviewed and signed by the owners responsible for perception, estimator, vehicle integration, and safety.

## Metrics to add to dashboards

### Accuracy and availability

- global position error (median, P95, P99) and heading error after alignment;
- time to first global fix, recovery time after forced loss, percent time in each navigation mode;
- VPR Recall@1/5/12; geometry pass rate; dense-verification yield;
- false global-fix rate and false `TRACKING` duration—the primary safety metric;
- map-age/season/altitude/attitude-stratified performance.

### Estimation health

- covariance trace, normalized estimation error squared (when ground truth exists), innovation statistics;
- factor counts/residuals, hypothesis posterior/margin, inlier count/ratio/grid coverage/RMSE;
- measurement age and dropped/reordered frame count.

### Edge performance

- end-to-end latency P50/P95/P99, per-stage latency, GPU memory, power, temperature and throttle state;
- map cache hit rate, storage bandwidth, CPU/GPU queue depth;
- PX4 publication rate, send error/retry rate, estimator-rejection status.

## Fault-injection catalogue

Every release should replay a subset from each category:

| Fault | Inject | Expected response |
| --- | --- | --- |
| timestamp | delay, duplicate, reorder, clock step | drop/mark fault; never create an out-of-order factor |
| calibration | wrong intrinsics/extrinsics/image resolution | reject ingress or geometry; no low-covariance output |
| image quality | blur, darkness, saturation, sky, rain, vibration | quality decreases; recovery/withholding rather than hallucinated match |
| map | wrong epoch, tile seam, stale imagery, incorrect CRS | compatibility rejection or higher uncertainty; no silent mixing |
| retrieval | semantically similar but geographically distant tile | multi-hypothesis margin remains insufficient; no publish |
| geometry | repeated texture, collinear points, parallax/terrain violation | USAC/PnP/homography gate rejects inappropriate model |
| compute | OOM, model timeout, GPU reset | bounded degradation; watchdog/health event; PX4 output withheld |
| PX4 | link drop, estimator reject, frame mismatch | logged transport health; vehicle's configured safety action owns response |

## Model change protocol

Models are data releases, not merely dependencies. For SALAD/MegaLoc, LightGlue, RoMa, Video2BEV, MASt3R-SLAM, VGGT, TopicGeo, or a new model:

1. Pin repository revision, checkpoint digest, licence, model card, preprocessing, input shape, device precision, TensorRT build and hardware target.
2. Regenerate offline features under a *new map/model version*; never mix descriptor spaces in one index.
3. Measure retrieval and matching independently, then measure end-to-end false accept/recovery behaviour.
4. Calibrate model confidence against held-out operation-area data. Preserve a conservative default if calibration is unavailable.
5. Run dual/shadow inference against the incumbent on replay, then live shadow mode. Promote by config, not a code fork.
6. Retain rollback artifacts and compatibility readers until old replay datasets age out.

## Map release protocol

1. Assign map version, geographic extent, CRS/ENU origin, imagery source/date, GSD, DEM source/date, model feature version and checksums.
2. Validate tile coverage, overlap, tile centre coordinates, north alignment, image format and feature dimensions.
3. Run fixed held-out routes across new and prior map versions; inspect changed areas and aliases.
4. Publish index atomically. The runtime must refuse a partial manifest or incompatible calibration/model dimension.
5. Make rollback an index/config selection, not a manual file replacement on the vehicle.
