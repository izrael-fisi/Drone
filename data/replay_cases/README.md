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
