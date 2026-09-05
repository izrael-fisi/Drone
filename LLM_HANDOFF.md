# Start Here: LLM / Engineering Handoff

Handoff date: **2026-09-05**. Repository: **izrael-fisi/Drone**, branch **main**.
This file is independent of any particular assistant or conversation history.

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
  publication remain unqualified. Short 20 Hz experiments still produced zero
  advancing current global updates, despite passing fail-closed tests.
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
- The transfer includes the latest bounded PnP assessment cache and per-stage
  estimator timing diagnostics. These are research optimizations/observability,
  not a fix for the advancing-current global-position gap. See the final transfer
  supplement in the verification guide for checks against these exact changes.
- Subsequent estimator updates include within-transaction validation reuse and
  default private `msgpack_v1` transport (`.[estimation]` now requires msgpack).
  JSON replay identities are unchanged. A single advancing update occurred in
  one compact-link trial, but repeated trials remained at zero; sustained
  current-state navigation is still an open requirement, not a completed feature.
- The next update reuses exact canonical snapshot bytes, wakes refreshes when
  new native data arrives and separates compact conditional status from full
  replay writes. Status now identifies pending versus written evidence. The
  stronger test samples the actual status reader, not just solve-completion
  checks; its final 20 Hz observation still exposed no advancing current pose.

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
