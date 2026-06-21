# Replay Validation Cases

Use this folder as the registry for replay datasets used to validate terrain
navigation behavior before field use.

Each case should include:

- `case_name`
- `expected`: `good_map`, `degraded`, or `wrong_map`
- `dataset_type`: `field`, `bench`, or `synthetic`
- `conditions`: one or more validation tags such as `good_texture`,
  `low_texture`, `blur`, `seasonal_change`, `lighting_change`,
  `altitude_scale_change`, `repeated_patterns`, or `wrong_map`
- `bundle`: bundle path used for replay
- `log`: replay/runtime JSONL path
- notes describing lighting, texture, blur, seasonal change, altitude/scale
  change, repeated patterns, or wrong-map setup

The manifest contract is captured in
[`replay_case_manifest.schema.json`](replay_case_manifest.schema.json). The
standalone manifest evaluator, coverage audit, and support-bundle replay-gate
packager all report schema issues. Schema errors fail the manifest before it can
count as field evidence; schema warnings keep provenance gaps visible without
blocking smoke tests.

To check manifest shape before every referenced log has been copied into place:

```bash
vision-nav-evaluate-replay-manifest \
  --manifest data/replay_cases/field_manifest.json \
  --schema-only
```

Evaluate a case with:

```bash
vision-nav-evaluate-replay-gates \
  --case-name good-texture-bench \
  --expected good_map \
  --log terrain-run/terrain_matches.jsonl
```

Wrong-map cases should pass only when accepted rate is zero by default.
Good-map accepted records must carry confidence, inlier count, reprojection
error, scale confidence, XY covariance, and finite local motion when positions
are available. Degraded cases may accept weak matches only when covariance is
inflated enough to keep the estimator from over-trusting the result.

Support bundles can package the same gate reports:

```bash
vision-nav-support-bundle \
  --bundle mission_bundle \
  --log terrain-run/terrain_matches.jsonl \
  --replay-case-manifest data/replay_cases/manifest.example.json
```

Generated reports are stored under `summaries/replay_gates/` inside the support
bundle.

## Register Field Logs

On the Pi, use the wrapper to turn runtime/replay logs into manifest cases,
generate the combined field-evidence report, and place it where support bundles
will include it automatically:

```bash
VISION_NAV_FIELD_CASE_NAME=field-good-texture \
VISION_NAV_FIELD_EXPECTED=good_map \
VISION_NAV_FIELD_CONDITION=good_texture \
VISION_NAV_FIELD_NOTES="clear texture, matching map, nominal lighting" \
./scripts/pi/register_field_replay_case.sh
```

The wrapper defaults to:

- manifest: `~/DroneTransfer/outgoing/replay-cases/field_manifest.json`
- evidence report:
  `~/DroneTransfer/outgoing/replay-cases/field_evidence_report.json`
- log: `~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl`

It also prints `__VISION_NAV_FIELD_EVIDENCE_REPORT__=...`, which lets the
desktop Module Setup workflow download the latest evidence report and show the
per-condition coverage checklist.

Use `VISION_NAV_FIELD_REPLACE=1` to update an existing case and
`VISION_NAV_FIELD_GATE_STRICT=1` once the complete field dataset is expected to
pass. Use the lower-level registry helper when curating a desktop dataset folder
or when custom manifest paths are needed:

```bash
vision-nav-register-replay-case \
  --manifest data/replay_cases/field_manifest.json \
  --case-name field-good-texture \
  --expected good_map \
  --dataset-type field \
  --condition good_texture \
  --bundle field-bundles/site-a/mission_bundle \
  --log ~/DroneTransfer/from-pi/terrain_matches.jsonl \
  --copy-log \
  --notes "clear texture, matching map, nominal lighting"
```

`--copy-log` copies the source log under the manifest folder, defaulting to
`<dataset-type>/<case-name>/`, so the dataset can be moved or reviewed as a
single folder. Use `--replace` to update a case after retesting.

After registering cases, run:

```bash
vision-nav-evaluate-replay-manifest \
  --manifest data/replay_cases/field_manifest.json \
  --output-dir data/replay_cases/field_reports

vision-nav-audit-replay-coverage \
  --manifest data/replay_cases/field_manifest.json
```

Then generate feature-method benchmark evidence used by the autonomy readiness
audit:

```bash
VISION_NAV_FEATURE_BENCH_EXPECTED=good_map \
./scripts/pi/run_feature_method_benchmark.sh
```

The Pi wrapper writes feature-method benchmark reports under
`~/DroneTransfer/outgoing/feature-method-bench/`, emits
`__VISION_NAV_FEATURE_METHOD_REPORT__=...` for Module Setup downloads, and lets
support bundles include method-comparison evidence automatically when present.

For coarse tile retrieval benchmarking, replay records can also include
precomputed higher-compute query descriptors. Use inline keys such as
`neural_global_descriptor` or path keys such as `query_neural_descriptor_path`
when tile descriptor `.npz` files or sibling sidecars contain matching
precomputed neural descriptor vectors. Missing neural descriptors are reported
as unavailable/degraded; ORB/AKAZE replay gates remain the low-compute default.

Then tune replay thresholds:

```bash
vision-nav-tune-replay-thresholds \
  --manifest data/replay_cases/field_manifest.json \
  --output data/replay_cases/threshold_tuning_report.json
```

The report records the gate config, observed accepted-rate margins, covered
conditions, and per-case gate status. It passes only when full real field
coverage and all replay gates pass under the selected thresholds. Pi support
bundles automatically include this report from
`~/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json` when it
exists.

## Coverage Audit

Before treating replay validation as field-ready, audit the manifest for the
real field conditions required by the ground-control implementation plan:

```bash
vision-nav-audit-replay-coverage \
  --manifest data/replay_cases/manifest.example.json
```

The audit requires real `dataset_type=field` cases, existing log paths, and
coverage for:

- good texture / matching map
- low texture
- blur
- seasonal or map-age change
- lighting or shadow change
- altitude / visual scale change
- repeated patterns
- wrong-map rejection

Use `--allow-synthetic` only to smoke-test the audit tool itself. Passing
synthetic replay gates does not satisfy the real field dataset requirement.

## Synthetic Smoke Suite

`synthetic_smoke/manifest.json` is a deterministic local smoke suite with:

- `synthetic-good-map`
- `synthetic-degraded-low-texture`
- `synthetic-wrong-map`

Run it with:

```bash
./scripts/dev/evaluate_synthetic_replay_cases.sh
```

or directly:

```bash
vision-nav-evaluate-replay-manifest \
  --manifest data/replay_cases/synthetic_smoke/manifest.json \
  --output-dir data/replay_cases/synthetic_smoke/reports
```

These logs are hand-authored synthetic gate fixtures. They prove the replay
gate machinery and registry wiring, but they do not replace real field replay
datasets for lighting, blur, seasonal change, altitude/scale change, or
repeated-pattern validation.
