# Bearing-UAV Dataset Evaluation

This research-only benchmark builds on the [official checkpoint adapter](bearinguav-reference-evaluation.md).
It loads the model once and evaluates multiple image tuples against separately
supplied labels. It does not train, change the Pi profile or publish vehicle poses.

The evaluator also accepts `--training-run` instead of `--checkpoint` for the
[new fine-tuning workflow](bearinguav-training.md). It checks the saved model
digest and binds the run's actual training membership to the evaluation audit;
reports distinguish trained checkpoints from the official reference.

## Existing pipeline integration

Our existing `vision_nav_training_map_pairs_v1` format contains a camera image,
one positive map tile and spatial hard negatives. Those negatives are not the
four neighboring tiles required by Bearing-UAV. No automatic substitution is
performed. The [cross-view dataset converter](bearinguav-dataset-converter.md)
now splits each candidate map crop into four quadrants and emits explicit oracle
context labels. It uses the portable cross-view package built from our training
pairs/windows, not the hard negatives as neighbors of the positive tile.

Training membership **can** be exported directly from our existing pair package:

```bash
vision-nav-export-training-membership \
  --pairs /absolute/path/to/training-pairs \
  --scene-registry /absolute/path/to/scenes.json \
  --training-release-id proprietary-training-v1 \
  --output /absolute/path/to/benchmark/training-membership.json
```

Use the dedicated reference environment described in the linked guide. The
entrypoint calls `vision_nav.neural.bearinguav_evaluation.membership_main`.
If the project is not installed, that same function can be invoked with
`PYTHONPATH=src python -c 'from vision_nav.neural.bearinguav_evaluation import membership_main; membership_main()'`
followed by the arguments above.

`scenes.json` maps each training `capture_session_id` to a reviewed geographic
scene ID, for example `{"flight-001": "field-west", "flight-002": "field-east"}`.
Different map versions of the same place should not receive different scene IDs.
This explicit classification is not inferred from map release names.

The exporter verifies the pair manifest's training JSONL checksum and every
training query's `frame_sha256`, preserves capture session identity, and records
the scene-registry digest. It exports only the training population, not validation
samples. Old pairs without pinned frame bytes must be rebuilt. Capture file paths
are read from the existing local pair records; no images are uploaded.

## Benchmark manifest

Each sample file uses the five-image contract in the reference guide, with
`split` set to `validation` or `test`. Evaluation labels live in a separate
manifest so they never become model inputs. The manifest contains:

```json
{
  "schema_version": "vision_nav_bearinguav_evaluation_v1",
  "dataset_release_id": "proprietary-evaluation-v1",
  "input_kind": "recorded",
  "split": "test",
  "context_policy": "known_context",
  "require_scene_disjoint": true,
  "label_protocol": "reviewed-camera-heading-and-map-block-convention-v1",
  "training_membership": {
    "path": "training-membership.json",
    "sha256": "REPLACE_WITH_ACTUAL_SHA256"
  },
  "records": [
    {
      "sample": {"path": "frame-001/sample.json", "sha256": "REPLACE_WITH_ACTUAL_SHA256"},
      "session_id": "held-out-flight-001",
      "scene_id": "field-north",
      "context_match": true,
      "ground_truth": {
        "coordinate_frame": "official_normalized_satellite_block_not_ENU",
        "position_xy": [0.1, -0.2],
        "direction_xy": [1.0, 0.0],
        "provenance": "REPLACE_WITH_BOUND_LABEL_RELEASE_AND_TRANSFORM"
      }
    }
  ]
}
```

Illustrative coordinates are not real labels. Position uses the official
normalized block frame; direction is a unit vector in the declared label
protocol. When that conversion is unavailable, use `direction_xy=null` with a
nonempty `direction_missing_reason`; the evaluator counts missing labels rather
than inventing angular errors. Do not insert PX4 yaw or an ENU target without a qualified conversion
through the camera/mount and map conventions. Direction error is not automatically
vehicle heading error. The current evaluator validates declared label structure,
not the correctness of the underlying calibration or GNSS measurements.

For explicitly wrong map contexts set `context_match=false` and
`ground_truth=null`. The model still regresses a vector; this is recorded rather
than hidden or interpreted as a successful fix. Labels never choose model inputs.
However, a producer that selects map neighborhoods using ground truth must set
`context_policy="oracle_context"`, not claim global-recovery evaluation.

Optional `ground_truth.metric_error_basis` requires:

```json
{
  "scope": "local_linear_error_conversion_not_global_pose",
  "provenance": "REPLACE_WITH_MAP_SCALE_AND_VALIDITY_ENVELOPE_EVIDENCE",
  "en_m_per_normalized_xy": [[64.0, 0.0], [0.0, 64.0]]
}
```

Columns map normalized coordinate errors into local east/north metre errors.
The values above are illustrative, not defaults. The evaluator checks finite,
nonsingular geometry but cannot establish that a supplied scale is geographically
correct. Without this basis it reports normalized error only. It never estimates
altitude or constructs an absolute metric pose from these labels.

## Run and interpret

```bash
PYTHONPATH=src .venv-bearing-reference/bin/python -m vision_nav.neural.bearinguav_evaluation \
  --manifest /absolute/path/to/benchmark/manifest.json \
  --source research/vendor/bearinguav \
  --checkpoint research/outputs/bearinguav-source-31098d2/Bearing_UAV/cross_view/best_model.pth \
  --configuration research/outputs/bearinguav-source-31098d2/Bearing_UAV/cross_view/training_configure.json \
  --device cpu --threads 2 --output /absolute/path/to/benchmark/report.json
```

The audit rejects sample/session/exact-query-byte overlap against the declared
training membership, duplicate query/context pairs, corrupted artifacts,
mixed real/synthetic data, mixed validation/test splits and invalid labels.
With scene-disjoint policy enabled it also rejects overlapping scene IDs;
otherwise it reports them. This does not detect all near-duplicate imagery or
prove disjointness from the official checkpoint's unknown original pretraining
population. The report explicitly limits that claim. The same query may be
tested against additional wrong contexts under distinct sample/case IDs from the
same capture session. A second positive case for the same query is rejected so
it cannot over-weight localization metrics. Distinct query count is reported
separately from case count.

Metrics include sample-weighted mean, median and P95 position/direction errors,
the count used for each statistic, invalid direction count and finite outputs on
wrong-context examples. A zero-length direction has no valid angular error and
is counted as invalid rather than silently scored as zero. Missing metric scale
reduces the reported metre-error count, not the normalized-error denominator.
These are not the upstream RSI macro-average or its quadrant-agreement metric.

Global-recovery recall, false-global-fix rate and learned no-match ability are
not inferred from this benchmark. All outputs remain `NO_GLOBAL_FIX`, with no
position authority and PX4 disabled. A complete report is written only after all
samples pass and inference finishes. Existing reports cannot be overwritten.

Tests include actual-checkpoint batches with explicitly synthetic images/labels,
analytic error conversion, angular wraparound, invalid directions, leakage and
integrity failures, and label/inference separation. Actual held-out aircraft
accuracy, official dataset reproduction, GPU/Pi timing and qualified heading
labels remain pending. The new converter now provides the four-neighbor input
path from the existing portable cross-view dataset.

Local synthetic CLI evidence is saved under
`research/outputs/bearinguav-benchmark-cpu-20260905-r1/`. Its numerical errors
exercise the scoring code against invented labels and are not flight accuracy.
