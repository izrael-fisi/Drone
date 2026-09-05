# Start Here: LLM / Engineering Handoff

Handoff date: **2026-09-05**. Repository: **izrael-fisi/Drone**, branch **main**.
This file is independent of any particular assistant or conversation history.

## Transfer Checkpoint

The latest implementation checkpoint covered by this handoff is
`fe13385f19f550af38941421280c077a9e59917c`. Subsequent documentation-only commits
may update this handoff without changing that implementation. Inspect `git log`
and `git status` on the receiving machine before starting work.

**Done:** real learned inference, native local visual-inertial odometry,
correlated delayed-map alignment, bounded worker transport, replay evidence and
an existing map-first desktop app. **Not done:** dependable continuous global
navigation, physically calibrated uncertainty, qualified Pi performance,
field-trained accuracy or learned PX4 publication. This is not a flyable
GNSS-denied navigation release.

**Next:** validate stabilized camera/IMU captures, resolve continuous-current
estimation, establish geographic training holdouts, then benchmark/train under
an explicitly approved RunPod budget. Leave UI expansion secondary.

## Read In This Order

1. [Technical handoff](docs/technical-handoff-2026-09-05.md): what the project
   is, requirements, implemented work, gaps, ownership and next steps.
2. [Handoff verification](docs/handoff-verification-2026-09-05.md): actual
   checks, failures, environment assumptions and artifacts missing from Git.
3. [AGENTS.md](AGENTS.md): repository working rules and safety boundaries.
4. [Architecture](docs/architecture.md), [interfaces](docs/interface-reference.md),
   [PX4 boundary](docs/px4-integration.md), and
   [change playbook](docs/change-playbook.md).
5. [Current research assessment](docs/current-navigation-assessment-2026-09-05.md):
   source-backed decisions and limitations, including historical evidence.

## Do Not Misinterpret The State

- The core product is learned, offline visual navigation for Pi 5 + PX4.
  The current implementation is **shadow research, not flight-ready navigation**.
- Native local VIO and real learned model execution exist. Continuous current
  global position, calibrated physical uncertainty, Pi performance and PX4
  publication remain unqualified. Current-state availability remains
  intermittent; passing fail-closed tests is not proof of usable continuous
  navigation. Earlier zero-update observations remain in the evidence ledger.
- `pi5_crossview_nav_v1` is the target profile name, not a certified checkpoint.
  The current student is project-owned cross-attention, not a reproduced
  Video2BEV/Bearing-UAV model. ORB/AKAZE/SIFT remain frozen baselines only.
- The user's immediate priority is flight data collection, camera stabilization,
  calibration, vision development and RunPod training. UI expansion can wait.
- GitHub's newer map-first UI, Proxigo integration and companion API changes are
  preserved. They do not qualify navigation or authorize aircraft control.
- Git contains source and example manifests, **not** model weights, map imagery,
  camera captures, native binaries, local credentials or full experiment outputs.
- Build, Rust and documented targeted checks passed in their recorded setups.
  The verification guide retains an exact-numerical-equality environment caveat,
  a corrected missing monitoring dependency and five open npm audit findings.
- Estimator improvements include bounded PnP reuse, timing diagnostics, exact
  canonical identity reuse, default private `msgpack_v1` transport and compact
  status independent of full replay writes. Install `.[estimation]` for the
  worker dependencies. Canonical JSON evidence and rejection gates are unchanged;
  status distinguishes pending versus written evidence.
- Latest child-side identity reuse produced intermittent operator-visible
  updates in three 60-frame runs (11, 6 and 4 distinct advancing poses), with
  exact replay and unchanged gates. Gaps remain substantial, so this is measured
  progress, not sustained-current or flight qualification. Read the latest
  verification section rather than treating earlier zero-update reports as the
  final observation.

## Verification At The Implementation Checkpoint

- 442 focused estimator/native tests and 84 learned/native integration tests
  passed in the recorded Mac environments, with no skips.
- All seven local preflight stages, including 104 main checks, passed.
- Desktop frontend build and Rust `cargo check` passed. Rust tests were not
  rerun for the latest Python-only change; older results remain historical.
- These results depend on ignored native binaries and model exports. A clone
  alone cannot reproduce every test. Follow the verification document's artifact
  inventory and setup instructions; report missing artifacts rather than silently
  accepting skipped integration tests.

These are recorded results, not new executions on the receiving machine, Pi
benchmarks, aircraft evidence or approval to enable flight-control output.

## First Session

Check branch, commit, working-tree changes and artifact availability before
editing. Reproduce the relevant checks in the verification document. Ask for
measured camera/IMU calibration and a sealed capture before claiming hardware
readiness. Do not provision paid cloud resources or connect to aircraft merely
because example scripts exist. Preserve original timestamps, correlation and
rejection gates; never force a green status by suppressing missing evidence.

Read current source before relying on an older plan. Several historical docs
describe intended features rather than completed implementations. The dated
technical handoff resolves the most important conflicts without deleting history.
