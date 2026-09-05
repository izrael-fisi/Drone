# Learned Local Performance Benchmark

This benchmark measures the actual XFeat/LighterGlue ONNX CPU pair on saved
images. It opens no camera or MAVLink connection and changes no PX4 settings.
Use it only on an idle, prop-off Pi bench. CPU contention during flight or a
camera collection session can compromise those workloads even without vehicle
commands.

It measures the local stage, not the complete navigation system: retrieval,
the temporal student, VIO, capture timing, DSM/PnP and transport are excluded.
Homography is measured only as a planar consistency check, not a position fix.

## Prerequisites

- The same repository revision used to export the models.
- An intact [local ONNX export](learned-local-onnx.md) and its runtime config.
- Base project dependencies plus ONNX Runtime. Torch/Kornia are not needed.
- Saved query imagery and one to four representative candidate map images.
- On the Pi: cooling, appropriate power, propellers removed and other flight/
  capture workloads stopped by the operator. The script does not stop services.

The export contains parity samples and performs startup checks automatically.
Do not remove them to make startup faster or modify source hashes by hand.

## Desktop Trial

```bash
python -m vision_nav.neural.local_benchmark \
  --config /data/local-onnx.json \
  --query /data/frames/query.png \
  --map /data/maps/candidate.png \
  --output-dir /data/benchmarks/local-warm-001 \
  --duration-s 60 \
  --cache-mode warm
```

Installed entrypoint: `vision-nav-benchmark-local-onnx`. Repeat `--query` for
up to 256 distinct images and `--map` for up to four candidates. Queries cycle
in their supplied order for the requested duration; each query checks every
supplied candidate. Repeated imagery measures workload cost, not generalization.
Input hashes are recorded, and each decoded file is verified against its hash.

## Pi Bench

Transfer the complete export directory, config and representative images to
the Pi. Adjust `export_dir` in the config to that local export location. Do not
run the workload until the operator confirms the bench is idle and prop-off.

```bash
VISION_NAV_BENCH_IDLE_PROP_OFF=yes \
  ./scripts/pi/benchmark_local_onnx.sh \
  --config "$HOME/drone-data/local-onnx.json" \
  --query "$HOME/drone-data/benchmark-images/query.png" \
  --map "$HOME/drone-data/benchmark-images/map.png" \
  --output-dir "$HOME/drone-data/benchmarks/local-warm-001" \
  --duration-s 300 \
  --cache-mode warm
```

Set `VISION_NAV_PYTHON` if the runtime environment is not
`$HOME/drone_vision_nav_venv/bin/python`. The wrapper requires a Raspberry Pi
device-tree identity; a Mac/Linux workstation cannot report a Pi result.
The environment acknowledgement is an operator attestation, not hardware proof
that props are removed or the aircraft is disarmed.

Run again with a new output directory and `--cache-mode cold`:

- **Warm:** extract map features once before measured iterations; reuse those
  features. Query decode, preprocessing, extraction, matching and geometry are
  still measured every iteration.
- **Cold:** decode and extract each map candidate on every iteration. This
  tests a feature-cache miss, not a guaranteed storage-cache miss. OS disk
  caches are not flushed, and cached versus uncached SSD/microSD performance
  must not be inferred from this distinction alone.

Start with one candidate, then repeat with the intended bounded candidate
count. Do not compare runs that silently change model thresholds or images.

## Evidence

Each output directory must be new. It contains:

- `iterations.jsonl`: flushed warmup/measured records, per-stage timing,
  actual feature/match counts, geometry decisions and any inference failure.
- `summary.json`: graph/checkpoint/provider provenance, input hashes, hardware
  identity, startup/parity and cache preparation times, P50/P95/P99 stage and
  complete-iteration times, process CPU time, sampled memory/temperature and
  Pi power/throttle flags.

Three warmup iterations are excluded from latency statistics. The default
measurement duration is 60 seconds with at least 20 completed iterations;
`--max-iterations` is a hard loop-count cap and cannot exceed 100,000.
Duration is checked between iterations, so a current inference may finish
after the requested duration. This is not a hard inference timeout/watchdog.

CPU percentage is relative to one core and may exceed 100% with parallel
inference. Linux RSS uses `/proc/self/status`; macOS uses the process-lifetime
peak from `getrusage`, explicitly labeled rather than called current RSS.
Resource observations are sampled around startup/warmup and approximately once
per second during the run. Short peaks can be missed. The benchmark's logging
and measurement overhead is part of process memory/CPU, while resource polling
is outside the per-iteration stage latency.

Pi flags distinguish current undervoltage/capping/throttling/soft-temperature
limits from flags latched since boot. Pre-existing latched flags are reported
separately; newly observed latched flags and current flags fail the limit
assessment. If the initial flag read is unavailable, a later latched flag is
not falsely claimed to have started during the benchmark.
[Raspberry Pi flag definitions](https://www.raspberrypi.com/documentation/computers/os.html#get_throttled)

## Result Interpretation

The summary separates execution (`completed` or `failed`) from limits:

- `within_measured_limits`: all required observations and workload coverage
  were available and stayed within the requested engineering limits.
- `incomplete`: insufficient duration/iterations, no learned attention
  exercised, or missing required memory/temperature/ Pi throttle observations.
- `exceeded`: an input/inference failure or an observed performance, memory,
  temperature or power/throttling violation.

Exit 0 requires completed execution and `within_measured_limits`. Exit 2 may
mean an otherwise completed desktop run lacks thermal readings; inspect the
summary, not just the exit code. Unknown measurements are never zero/healthy.
Defaults are P95 1000 ms, process memory 2048 MiB, temperature 80 C. These are
initial engineering limits, not flight-qualified budgets. Known memory,
temperature or current power/throttle violations prevent or stop additional
load after the next observation; no cooling or power configuration is changed.

No result promotes a model or enables navigation output. Reports retain
`position_authority: false` and `px4_external_vision: disabled`. Test genuine
camera cadence, drop rates, terrain localization accuracy, thermal soak and
the full concurrent runtime separately before any vehicle integration.

## Initial Desktop Evidence

On 2026-09-05, the real pretrained local ONNX pair ran for 30 seconds per mode
on the arm64 Mac host (10 logical CPUs), one upstream sample candidate,
640x384 inference images and 512-feature budget:

| Mode | Completed iterations | Total P50 | Total P95 | Total P99 |
| --- | ---: | ---: | ---: | ---: |
| Warm feature cache | 479 | 62.25 ms | 65.54 ms | 72.87 ms |
| Cold feature cache | 330 | 89.84 ms | 94.17 ms | 104.32 ms |

These are preliminary desktop workload checks, not Pi throughput or flight
performance. Each report records its benchmark-source digest; the cold run
also includes a logging-memory improvement made after the warm run. A strict
comparative hardware study must rerun both on one frozen source revision.
Both commands completed inference but returned exit 2 with an **incomplete**
limit assessment because temperature was unavailable. No Pi benchmark was
performed in this pass, and no missing sensor reading was called healthy.
