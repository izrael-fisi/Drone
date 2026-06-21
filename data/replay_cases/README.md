# Replay Validation Cases

Use this folder as the registry for replay datasets used to validate terrain
navigation behavior before field use.

Each case should include:

- `case_name`
- `expected`: `good_map`, `degraded`, or `wrong_map`
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
