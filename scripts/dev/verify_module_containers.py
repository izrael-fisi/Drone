#!/usr/bin/env python3
"""Build/check local module images without aircraft access or registry pushes.

Python standard library only. Each run preserves commands, logs, image identity
and actual native replay evidence. A static web probe is stopped before exit.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[2]

# The same byte-level tree digest runs on the host and in the built image.
TREE_DIGEST_CODE = '''
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(root.rglob("*")):
    if (path.is_file() and "__pycache__" not in path.parts
            and path.suffix != ".pyc" and path.name != ".DS_Store"):
        digest.update(path.relative_to(root).as_posix().encode() + b"\\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
print(json.dumps({"sha256": digest.hexdigest()}))
'''


def tree_digest(path):
    import sys
    return json.loads(subprocess.check_output([sys.executable, "-c", TREE_DIGEST_CODE, str(path)], text=True))["sha256"]


def assert_native_report(report):
    worker = report.get("worker", {})
    if (report.get("complete") is not True or report.get("position_authority") is not False
            or report.get("px4_external_vision") != "disabled"
            or worker.get("completed_batch_count") != 160 or worker.get("thread_alive") is not False):
        raise ValueError("Native container did not complete the shadow-only 160-frame smoke")


def assert_rejection(log, reason):
    records = []
    for line in log.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    if not records:
        raise ValueError("Missing structured rejection evidence")
    result = records[-1]
    if (result.get("state") != "rejected" or reason not in result.get("reason", "")
            or result.get("position_authority") is not False
            or result.get("px4_external_vision") != "disabled"):
        raise ValueError("Launch failed for an unexpected reason or lost its shadow boundary")


def assert_isolation(service):
    if (service.get("network_mode") != "none" or not service.get("read_only")
            or not service.get("init") or service.get("privileged")
            or service.get("devices") or service.get("cap_drop") != ["ALL"]
            or service.get("tmpfs") != ["/tmp:rw,nosuid,nodev,size=512m"]):
        raise ValueError("Resolved module service lost its offline/least-privilege configuration")


def source_fingerprint():
    paths = [ROOT / "pyproject.toml", ROOT / "README.md", ROOT / ".dockerignore"]
    for directory in ("src", "research/native/openvins", "docker/module"):
        paths.extend(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", help="Explicit Docker context; otherwise use the selected context")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args(argv)
    output = (args.output or ROOT / "logs/module-validation" / uuid.uuid4().hex).resolve()
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    runs = output / "runs"
    inputs.mkdir()
    runs.mkdir()
    docker = ["docker"] + (["--context", args.context] if args.context else [])
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    env = {**os.environ, "MODULE_INPUTS": str(inputs), "MODULE_OUTPUTS": str(runs),
           "MODULE_CONFIG": str(ROOT / "config/module/generic.json"),
           "MODULE_UID": str(uid), "MODULE_GID": str(gid)}
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    env["SOURCE_REVISION"] = revision + ("-working-tree" if dirty else "")
    report = {"schema_version": "vision_nav_container_verification_v1", "status": "running",
              "started_utc": datetime.now(timezone.utc).isoformat(), "source_revision": revision,
              "working_tree_dirty": dirty, "source_fingerprint": source_fingerprint(),
              "steps": [], "images": {}, "position_authority": False,
              "scope": "local_container_synthetic_checks_not_hardware_or_model_accuracy"}
    web_name = "vision-nav-web-check-" + uuid.uuid4().hex[:12]
    web_started = False

    def execute(name, command, expected=0, timeout=180):
        start = time.monotonic()
        path = output / (name + ".log")
        with path.open("w") as log:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=timeout)
        report["steps"].append({"name": name, "command": command, "returncode": completed.returncode,
                                 "expected_returncode": expected, "duration_s": time.monotonic() - start,
                                 "log": path.name})
        if completed.returncode != expected:
            raise RuntimeError(f"{name} failed: {path.read_text()[-3000:]}")
        print(f"[OK] {name}", flush=True)
        return path

    compose = docker + ["compose", "-f", "docker/module/compose.yaml"]
    try:
        execute("engine", docker + ["version", "--format", "json"])
        resolved = json.loads(execute("compose", compose + ["--profile", "research", "config", "--format", "json"]).read_text())
        for service in ("module", "research"):
            assert_isolation(resolved["services"][service])
        if args.build:
            execute("build-module", compose + ["build", "module", "research"], timeout=5400)
            if not args.skip_web:
                execute("build-web", compose + ["build", "gcs"], timeout=1200)
        images = ["drone-vision-module:cpu", "drone-vision-module:research-native"]
        if not args.skip_web:
            images.append("drone-vision-gcs:web")
        for index, image in enumerate(images):
            details = json.loads(execute(f"image-{index}", docker + ["image", "inspect", image]).read_text())[0]
            report["images"][image] = {key: details.get(key) for key in ("Id", "Architecture", "Os", "RepoDigests")}
            if details.get("Config", {}).get("User") in (None, "", "0", "root"):
                raise ValueError(f"Root-default image: {image}")
        execute("diagnostics", compose + ["run", "--rm", "module", "run", "--run-id", "diagnostics"])
        launch = json.loads((runs / "diagnostics/launch.json").read_text())
        if launch["position_authority"] is not False or launch["command"] != []:
            raise ValueError("Unexpected diagnostic authority or execution")
        rejected = execute("duplicate-rejected", compose + ["run", "--rm", "module", "run", "--run-id", "diagnostics"], expected=2)
        assert_rejection(rejected.read_text(), "File exists")
        # Load all runtime interfaces inside the real CPU image, without hardware.
        execute("runtime-imports", compose + ["run", "--rm", "--entrypoint", "python", "module", "-c",
            "import cv2, onnxruntime, rasterio, serial; import vision_nav.neural.map_conditioned_replay; "
            "import vision_nav.mavlink_bench; print('runtime interfaces imported')"])
        for service in ("module", "research"):
            # Compare the actually imported package, not just the Docker source label.
            code = "import vision_nav; sys_path = __import__('pathlib').Path(vision_nav.__file__).parent\n" + \
                   TREE_DIGEST_CODE.replace("root = Path(sys.argv[1])", "root = sys_path")
            result = json.loads(execute(f"{service}-source", compose + ["run", "--rm", "--entrypoint", "python",
                                service, "-c", code]).read_text().splitlines()[-1])
            if result["sha256"] != tree_digest(ROOT / "src/vision_nav"):
                raise ValueError(f"{service} image contains stale/different Python source; rebuild")
        native_source = json.loads(execute("native-adapter-source", compose + ["run", "--rm", "--entrypoint", "python",
            "research", "-c", TREE_DIGEST_CODE, "/opt/vision-nav/native-adapter"]).read_text().splitlines()[-1])
        if native_source["sha256"] != tree_digest(ROOT / "research/native/openvins"):
            raise ValueError("Native adapter source differs from the current checkout; rebuild")
        for profile in ("holybro-x500-v2.json", "holybro-x650-v2.json"):
            env["MODULE_CONFIG"] = str(ROOT / "config/module" / profile)
            execute(profile[:-5], compose + ["run", "--rm", "module", "inspect"])
        env["MODULE_CONFIG"] = str(ROOT / "config/module/serial-bench.json")
        rejected = execute("bench-attestation-rejected", compose + ["run", "--rm", "module", "run", "--run-id", "bench"], expected=2)
        assert_rejection(rejected.read_text(), "requires explicit --propellers-removed")
        env["MODULE_CONFIG"] = str(ROOT / "config/module/native-smoke.json")
        execute("native-smoke", compose + ["run", "--rm", "research", "run", "--run-id", "native"], timeout=300)
        assert_native_report(json.loads((runs / "native/result/vio/report.json").read_text()))
        if not args.skip_web:
            execute("web-start", docker + ["run", "-d", "--name", web_name, "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                    "-p", "127.0.0.1::8080", "drone-vision-gcs:web"])
            web_started = True
            details = json.loads(execute("web-inspect", docker + ["inspect", web_name]).read_text())[0]
            port = int(details["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"])
            origin = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urllib.request.urlopen(origin + "/healthz", timeout=2) as response:
                        if response.read() != b"ok\n":
                            raise ValueError("Unexpected web health response")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.5)
            for route in ("/", "/mission-planner", "/maps"):
                with urllib.request.urlopen(origin + route, timeout=3) as response:
                    if b'id="root"' not in response.read():
                        raise ValueError(f"Missing SPA entry for {route}")
            report["web"] = {"health": "passed", "spa_routes": ["/", "/mission-planner", "/maps"],
                             "scope": "HTTP_static_frontend_not_Tauri_backend"}
        if source_fingerprint() != report["source_fingerprint"]:
            raise ValueError("Source changed during container verification")
        report["status"] = "passed"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        if web_started:
            try:
                execute("web-stop", docker + ["rm", "-f", web_name])
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                report.update(status="failed", cleanup_error=str(exc))
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['status']}: {output / 'verification.json'}")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
