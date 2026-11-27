#!/usr/bin/env python3
"""Run the local industry pilot checks and retain independent evidence receipts."""

import argparse
from contextlib import nullcontext, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop-app"
GATEWAY = ROOT / "services/camera-gateway"
FIXTURE = ROOT / "tests/fixtures/cctv-industry-g336-dock.json"
MODEL_SHA256 = "05b04fd920332046673160ec15eca13de3e005027c5508f16a3e09101bb1348c"
START = "2026-01-01T00:00:00+00:00"
STAGES = {
    "baseline": "Gateway contracts, frontend build, corpus contract and pinned YOLO11 replay.",
    "dock": "Deterministic dock checks, Chrome fixture, and reviewed MEVA dock replay.",
    "safety": "Factory tracking and scoped review, plus the Chrome factory fixture.",
    "security": "Security correlation contracts and the two-operator Chrome restart pilot.",
    "integrated": "Full gateway and frontend checks, twelve-camera Chrome loads, and corpus comparison.",
    "inspection": "Simulated production station adapter and Chrome review.",
    "commissioning": "Tripwire annotation and replay contracts plus pinned YOLO11 in Chrome.",
    "yard": "Shared dock-schedule versions, check-in and restart in Chrome.",
    "passage": "Door episode ambiguity, scope, late delivery, and two Chrome operators.",
    "native-camera": "Read-only ONVIF PullPoint fixture and camera status in Chrome.",
    "opcua": "OPC UA subscription, station export, deduplication and video in Chrome.",
    "vnext-integrated": "Five workflow gates, existing and mixed camera loads, enterprise isolation and corpus comparison.",
    "enterprise-operations": "Enterprise gateway contracts and four Chrome workflows with local VMS, case and recovery fixtures.",
    "site-intelligence": "AI drafting and recordings, camera degradation and radar correlation with local fixtures.",
    "enterprise-integrated": "Enterprise operations, prior workflow regressions, twelve-camera loads and the pinned corpus comparison.",
}

FEATURES = {
    "commissioning": ("test_commissioning_runs", "tripwire commissioning"),
    "yard": ("test_vnext_operations.OperationsTests.test_dock_revisions_dst_scope_and_unassigned_history", "shared dock schedule"),
    "passage": ("test_vnext_operations", "door passage review"),
    "native-camera": ("test_vnext_operations.OperationsTests.test_native_events_require_local_connector_and_released_native_rule", "camera-native ONVIF"),
    "opcua": ("test_inspection_results", "OPC UA inspection"),
}
INTEGRATED = ("integrated", "vnext-integrated", "enterprise-integrated")
WORKFLOW_REGRESSION = ("vnext-integrated", "enterprise-integrated")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    partial = path.with_suffix(path.suffix + ".tmp")
    partial.write_text(json.dumps(data, indent=2) + "\n")
    partial.replace(path)


def fingerprint():
    paths = [ROOT / "AGENTS.md", Path(__file__), ROOT / "scripts/dev/benchmark_cctv_meva_corpus.py",
             ROOT / "scripts/dev/replay_inspection_results.py", ROOT / "tests/test_cctv_meva_corpus.py",
             DESKTOP / "package.json", DESKTOP / "package-lock.json", DESKTOP / "playwright.config.ts",
             DESKTOP / "vite.config.ts", DESKTOP / "tsconfig.json", DESKTOP / "tsconfig.node.json"]
    for directory, extensions in [
        (GATEWAY, {".py", ".json", ".txt"}),
        (DESKTOP / "src", {".ts", ".tsx", ".css"}),
        (DESKTOP / "tests/e2e", {".ts", ".json", ".mp4"}),
        (ROOT / "tests/fixtures", {".json", ".py"}),
    ]:
        paths.extend(path for path in directory.rglob("*") if path.is_file() and path.suffix in extensions
                     and not any(part.startswith(".") for part in path.relative_to(directory).parts))
    combined = hashlib.sha256()
    for path in sorted({path for path in paths if path.is_file()}):
        combined.update(f"{path.relative_to(ROOT)}\0{digest(path)}\n".encode())
    return combined.hexdigest()


@dataclass
class Run:
    args: argparse.Namespace
    directory: Path
    started: float = field(default_factory=time.monotonic)
    commands: list = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)

    def require(self, key, expected=None):
        value = os.getenv(key)
        if not value or not Path(value).is_file():
            raise ValueError(f"Set {key} to an existing local file.")
        path = Path(value).expanduser().absolute()
        sha = digest(path)
        self.inputs[key] = {"path": str(path), "sha256": sha}
        if expected and expected != sha:
            raise ValueError(f"{key} does not match the pinned SHA-256.")
        return path

    def command(self, label, evidence, command, cwd=ROOT, env=None, result=None):
        remaining = self.args.timeout - (time.monotonic() - self.started)
        if remaining <= 0:
            raise TimeoutError("The stage time limit expired.")
        log = self.directory / (label + ".log")
        before = fingerprint()
        start = time.monotonic()
        item = {"name": label, "evidence": evidence, "argv": [str(value) for value in command], "log": str(log),
                "stdout": str(result or log), "sourceBefore": before, "startedAt": datetime.now(timezone.utc).isoformat()}
        self.commands.append(item)
        with log.open("wb") as errors, (result.open("wb") if result else nullcontext(errors)) as stdout:
            process = subprocess.Popen(item["argv"], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=errors, start_new_session=os.name == "posix")
            try:
                process.wait(remaining)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                if os.name == "posix":
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
                raise
            finally:
                after = fingerprint()
                item.update(exitCode=process.returncode, elapsedSeconds=round(time.monotonic() - start, 2), sourceAfter=after)
        if before != after:
            raise ValueError(f"Source changed while {label} was running. Repeat this check on a fixed source tree.")
        if process.returncode:
            raise RuntimeError(f"{label} failed. See {log}.")
        print(f"Passed {label}", flush=True)

    def gateway(self, label="gateway", tests=()):
        args = ["discover", "-s", "."] if not tests else list(tests)
        self.command(label, "deterministic fixtures", [sys.executable, "-m", "unittest", *args], cwd=GATEWAY)

    def chrome(self, label, spec, grep, env=None, evidence="gateway fixtures in Chrome"):
        report = self.directory / (label + ".json")
        self.command(label, evidence,
                     ["npm", "run", "--silent", "test:e2e", "--", spec, "--grep", grep, "--reporter=json", "--output",
                      self.directory / (label + "-playwright")], cwd=DESKTOP, env=env, result=report)
        self.artifacts[label] = str(report)
        stats = json.loads(report.read_text())["stats"]
        if stats["expected"] < 1:
            raise RuntimeError(f"{label} ran no passing tests. Inspect the report for skip reasons.")
        return stats

    def corpus(self):
        model = self.require("PROXIGO_CCTV_YOLO11", MODEL_SHA256)
        cache_dir = os.getenv("PROXIGO_CCTV_CORPUS")
        if not cache_dir:
            raise ValueError("Set PROXIGO_CCTV_CORPUS to the external MEVA corpus cache.")
        cache = Path(cache_dir).resolve()
        self.command("meva-corpus", "annotated public footage with pinned YOLO11", [
            sys.executable, ROOT / "scripts/dev/benchmark_cctv_meva_corpus.py", cache, "--model", model,
            "--sha256", MODEL_SHA256, "--provider", self.args.provider,
        ])
        report = self.directory / "meva-corpus.json"
        shutil.copy2(cache / "meva-corpus-report.json", report)
        self.artifacts["corpus"] = str(report)
        current = json.loads(report.read_text())
        if current["summary"]["cases"] != 570:
            raise ValueError("The integrated regression requires the full 570-case corpus.")
        if self.args.stage in INTEGRATED:
            path = self.args.reference_report.resolve()
            self.inputs["baselineCorpus"] = {"path": str(path), "sha256": digest(path)}
            reference = json.loads(path.read_text())
            fields = ("modelSha256", "annotationSha256", "provider", "summary", "byActivity", "recordings")
            comparison = {name: current[name] == reference[name] for name in fields}
            result = self.directory / "corpus-comparison.json"
            write_json(result, {"matchesReference": comparison, "current": current["summary"], "reference": reference["summary"]})
            self.artifacts["corpusComparison"] = str(result)
            if not all(comparison.values()):
                raise ValueError("The corpus results differ from the pinned baseline. Review the comparison.")

    def dock(self):
        fixture = json.loads(FIXTURE.read_text())
        source = self.require("PROXIGO_CCTV_DOCK_VIDEO", fixture["sourceSha256"])
        model = self.require("PROXIGO_CCTV_YOLO11", MODEL_SHA256)
        clip, config, policy = (self.directory / name for name in ("dock-window.mkv", "dock-config.json", "dock-policy.json"))
        observations, detections = (self.directory / name for name in ("dock-observations.jsonl", "dock-detections.jsonl"))
        self.command("dock-clip", "public MEVA fixture preparation", ["ffmpeg", "-nostdin", "-v", "error", "-ss",
                     fixture["startSeconds"], "-i", source, "-t", fixture["durationSeconds"], "-an", "-vf",
                     f"fps={fixture['fps']}", "-c:v", "ffv1", clip])
        write_json(config, {"sensorId": "meva-g336", "width": fixture["width"], "height": fixture["height"], "fps": fixture["fps"],
                            "models": {"ground": {"kind": "yolo11", "path": str(model), "sha256": MODEL_SHA256,
                                       "provider": self.args.provider, "detailRegion": fixture["detailRegion"], "detailObjects": ["vehicle"]}}})
        write_json(policy, fixture["policy"])
        self.command("dock-replay", "reviewed MEVA footage with pinned YOLO11", [
            sys.executable, GATEWAY / "analytics_worker.py", "--config", config, "--policy", policy,
            "--replay", clip, "--start-at", START, "--detections", detections,
        ], result=observations)
        transitions, arrival_links = [], set()
        for line in detections.read_text().splitlines():
            sample = json.loads(line)
            [dock] = sample["dockOccupancy"]
            if not transitions or transitions[-1]["state"] != dock["state"]:
                transitions.append({"state": dock["state"], "seconds": sample["seconds"]})
            if dock.get("arrivalEventId"):
                arrival_links.add(dock["arrivalEventId"])
        events = [json.loads(line) for line in observations.read_text().splitlines()]
        expected = fixture["transitions"]
        passed = (len(transitions) == len(expected)
                  and all(actual["state"] == target["state"] and target["windowSeconds"][0] <= actual["seconds"] <= target["windowSeconds"][1]
                          for actual, target in zip(transitions, expected))
                  and len(events) == fixture["expectedArrivals"] and {event["id"] for event in events} == arrival_links
                  and all(event["analytics"]["source"] == "replay" and event["analytics"]["object"] == "vehicle" for event in events))
        report = self.directory / "dock-result.json"
        write_json(report, {"result": "pass" if passed else "fail", "recording": fixture["recording"], "expected": expected,
                            "transitions": transitions, "arrivals": len(events), "observations": str(observations), "detections": str(detections)})
        self.artifacts["dockReplay"] = str(report)
        if not passed:
            raise ValueError("Dock replay did not match the reviewed transition and arrival windows.")

    def load(self, label, video, grep, dock=False, intelligence=False):
        self.require("PROXIGO_CCTV_YOLO11", MODEL_SHA256)
        self.require(video)
        self.require("MEDIAMTX")
        env = {name: value for name, value in os.environ.items() if not name.startswith("PROXIGO_CCTV_")}
        env.update(PROXIGO_CCTV_PYTHON=sys.executable, PROXIGO_CCTV_YOLO11_PROVIDER=self.args.provider,
                   PROXIGO_CCTV_YOLO11=self.inputs["PROXIGO_CCTV_YOLO11"]["path"], **{video: self.inputs[video]["path"]})
        if dock:
            source = self.require("PROXIGO_CCTV_DOCK_VIDEO", json.loads(FIXTURE.read_text())["sourceSha256"])
            env["PROXIGO_CCTV_DOCK_VIDEO"] = str(source)
        env["PROXIGO_SITE_INTELLIGENCE_LOAD"] = "1" if intelligence else "0"
        self.chrome(label, "cctv-analytics.spec.ts", grep, env, "real YOLO11 over loopback RTSP and Chrome")

    def feature(self, name):
        unit, grep = FEATURES[name]
        self.gateway(name + "-contract", (unit,))
        env = dict(os.environ)
        evidence = "gateway fixtures in Chrome"
        if name == "commissioning":
            model = self.require("PROXIGO_CCTV_YOLO11", MODEL_SHA256)
            video = self.require("PROXIGO_CCTV_LOAD_VIDEO")
            env.update(PROXIGO_CCTV_PYTHON=sys.executable, PROXIGO_CCTV_YOLO11=str(model),
                       PROXIGO_CCTV_LOAD_VIDEO=str(video), PROXIGO_CCTV_YOLO11_PROVIDER=self.args.provider)
            evidence = "released tripwire and pinned YOLO11 on retained public footage in Chrome"
        elif name == "opcua":
            env["PROXIGO_OPCUA_PYTHON"] = str(self.require("PROXIGO_OPCUA_PYTHON"))
            evidence = "local OPC UA server and recorder fixture in Chrome"
        elif name == "native-camera":
            evidence = "local ONVIF SOAP PullPoint fixture in Chrome"
        self.chrome(name + "-chrome", "industry-vnext.spec.ts", grep, env, evidence)

    def enterprise(self):
        env = dict(os.environ)
        env["PROXIGO_ENTERPRISE_PYTHON"] = str(self.require("PROXIGO_ENTERPRISE_PYTHON"))
        env["MEDIAMTX"] = str(self.require("MEDIAMTX"))
        self.chrome("enterprise-chrome", "enterprise-sign-in.spec.ts", "enterprise sign-in", env,
                    "enterprise identity and camera-isolation fixtures in Chrome")

    def enterprise_operations(self):
        env = dict(os.environ)
        env["MEDIAMTX"] = str(self.require("MEDIAMTX"))
        stats = self.chrome("enterprise-operations-chrome", "enterprise-upgrades.spec.ts", "^(?!.*site intelligence)", env,
                            "two-operator case evidence, vendor HTTPS fixtures, inventory and gateway recovery in Chrome")
        if stats["expected"] != 4 or stats["skipped"]:
            raise RuntimeError("Run all four enterprise operations fixtures without skips.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES, nargs="?", default="baseline")
    parser.add_argument("--plan", action="store_true", help="List the selected stage and exit.")
    parser.add_argument("--output-dir", type=Path, help="Create a fresh private run directory here, outside the repo.")
    parser.add_argument("--timeout", type=int, default=1800, help="Maximum stage runtime in seconds.")
    parser.add_argument("--provider", default=os.getenv("PROXIGO_CCTV_YOLO11_PROVIDER", "CPUExecutionProvider"))
    parser.add_argument("--reference-report", type=Path, help="Required baseline MEVA corpus JSON for integrated comparison.")
    args = parser.parse_args()
    if args.plan:
        print(f"{args.stage}: {STAGES[args.stage]}")
        return 0
    if args.timeout <= 0 or args.stage in INTEGRATED and (not args.reference_report or not args.reference_report.is_file()):
        parser.error("Use a positive timeout and an existing --reference-report for the integrated stage.")
    os.umask(0o077)
    parent = args.output_dir.resolve() if args.output_dir else Path(tempfile.gettempdir()).resolve()
    if parent.is_relative_to(ROOT):
        parser.error("Store gate artifacts outside the repo.")
    parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=args.stage + "-", dir=parent))
    run = Run(args, destination)
    before = fingerprint()
    print(destination, flush=True)
    unit = "test_camera_analytics"
    browser_fixture = "dock occupancy and factory safety"
    try:
        if args.stage in ("baseline", *INTEGRATED):
            run.gateway()
            run.command("frontend", "TypeScript and production build", ["npm", "run", "build"], cwd=DESKTOP)
            run.command("corpus-contract", "deterministic corpus checks",
                        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_cctv_meva_corpus.py"])
            if args.stage == "enterprise-integrated":
                run.enterprise_operations()
                run.chrome("site-intelligence-chrome", "enterprise-upgrades.spec.ts", "site intelligence")
                run.chrome("operator-workspace-chrome", "operator-workspace.spec.ts", ".")
                run.chrome("camera-workspace-chrome", "camera-workspace.spec.ts", ".")
                run.chrome("organization-dispatch-chrome", "camera-gateway.spec.ts", "organization dispatch opens")
            if args.stage in WORKFLOW_REGRESSION:
                for feature in FEATURES:
                    run.feature(feature)
                run.enterprise()
                run.chrome("previous-dock-and-safety", "cctv-analytics.spec.ts", browser_fixture)
                run.chrome("previous-held-open", "camera-gateway.spec.ts", "published held-open door")
                run.chrome("previous-inspection", "cctv-analytics.spec.ts", "simulated inspection results")
            if args.stage in INTEGRATED:
                run.load("twelve-entrance", "PROXIGO_CCTV_LOAD_VIDEO", "twelve-camera site sustains",
                         intelligence=args.stage == "enterprise-integrated")
                run.load("twelve-stairs", "PROXIGO_CCTV_STAIRS_VIDEO", "twelve-camera twilight stair")
                if os.getenv("PROXIGO_CCTV_DOCK_VIDEO"):
                    run.load("twelve-mixed", "PROXIGO_CCTV_LOAD_VIDEO", "twelve-camera mixed site", dock=True)
                if args.stage in WORKFLOW_REGRESSION:
                    run.load("twelve-native-mix", "PROXIGO_CCTV_LOAD_VIDEO", "twelve-camera native mix")
            run.corpus()
        elif args.stage == "site-intelligence":
            run.gateway("site-intelligence-contract", ("test_site_intelligence",))
            run.chrome("site-intelligence-chrome", "enterprise-upgrades.spec.ts", "site intelligence")
        elif args.stage == "enterprise-operations":
            run.gateway("enterprise-operations-contract", ("test_enterprise_operations",))
            run.enterprise_operations()
        elif args.stage == "dock":
            run.gateway("dock-contract", (unit + ".TrackingTests.test_dock_tracks_state_across_vehicles_misses_and_worker_restarts",
                                         unit + ".GatewayAnalyticsTests.test_dock_snapshot_requires_the_current_released_camera_rule"))
            run.chrome("dock-chrome", "cctv-analytics.spec.ts", browser_fixture)
            run.dock()
        elif args.stage == "safety":
            run.gateway("safety-contract", (unit + ".TrackingTests.test_factory_safety_fires_once_and_rejects_short_or_moving_events",
                                           unit + ".GatewayAnalyticsTests.test_factory_safety_review_filters_use_the_released_rule_and_camera_scope"))
            run.chrome("safety-chrome", "cctv-analytics.spec.ts", browser_fixture)
        elif args.stage == "security":
            run.gateway("security-contract", ("test_site_monitoring",))
            run.chrome("security-chrome", "camera-gateway.spec.ts", "published held-open door")
        elif args.stage == "inspection":
            run.gateway("inspection-contract", ("test_inspection_results",))
            run.chrome("inspection-chrome", "cctv-analytics.spec.ts", "simulated inspection results")
        elif args.stage in FEATURES:
            run.feature(args.stage)
        result, reason = "pass", ""
    except (OSError, TimeoutError, ValueError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        result, reason = "fail", str(error) or type(error).__name__
    after = fingerprint()
    if before != after:
        result, reason = "fail", "The source fingerprint changed during the stage. Repeat the gate on a fixed source tree."
    write_json(destination / "receipt.json", {"stage": args.stage, "result": result, "reason": reason,
                                              "sourceBefore": before, "sourceAfter": after, "provider": args.provider,
                                              "elapsedSeconds": round(time.monotonic() - run.started, 2), "inputs": run.inputs,
                                              "artifacts": run.artifacts, "commands": run.commands})
    if reason:
        print(reason, file=sys.stderr)
    return int(result != "pass")


if __name__ == "__main__":
    sys.exit(main())
