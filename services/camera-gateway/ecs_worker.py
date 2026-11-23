"""Prepare task-scoped worker configuration from ECS-provided settings."""

import json
import os
from pathlib import Path
import sys

from site_activity import valid_event_device

MODEL_DIGEST = "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"


def prepare(environment, private):
    supplied = json.loads(environment.pop("PROXIGO_CCTV_SECRET"))
    token = supplied["gatewayToken"]
    if not isinstance(token, str) or not isinstance(supplied["source"], str) or not isinstance(supplied["gateway"], str):
        raise ValueError("Invalid worker secret fields.")
    identity = {"organizationId": environment["PROXIGO_ORGANIZATION_ID"], "siteId": environment["PROXIGO_SITE_ID"],
                "sensorId": environment["PROXIGO_CAMERA_ID"]}
    if not all(valid_event_device(value) for value in identity.values()):
        raise ValueError("Invalid worker identity.")
    private.mkdir(mode=0o700, exist_ok=True)
    token_path = private / "gateway.token"
    token_path.write_text(token)
    if "caBundle" in supplied:
        ca_path = private / "site-ca.pem"
        ca_path.write_text(supplied["caBundle"])
        environment["SSL_CERT_FILE"] = str(ca_path)
    config = {**identity, "placement": "cloud", "tokenFile": str(token_path),
              "state": str(private.parent / "staging.sqlite3"), "outboxQueueUrl": environment["PROXIGO_CCTV_QUEUE_URL"],
              "source": supplied["source"], "gateway": supplied["gateway"], "width": supplied.get("width", 640),
              "height": supplied.get("height", 360), "fps": supplied.get("fps", 4),
              "models": {"ground": {"kind": "yolox", "path": "/models/yolox_nano.onnx", "sha256": MODEL_DIGEST}}}
    target = private / "worker.json"
    target.write_text(json.dumps(config))
    return target


def main():
    os.umask(0o077)
    try:
        state = Path(os.environ.get("PROXIGO_STATE_DIR", "/state"))
        config = prepare(os.environ, state / "private")
    except (KeyError, OSError, TypeError, ValueError):
        raise SystemExit("Check the ECS worker settings, mounted state permissions, and Secrets Manager configuration.") from None
    worker = Path(__file__).with_name("analytics_worker.py")
    os.execv(sys.executable, [sys.executable, str(worker), "--config", str(config), *sys.argv[1:]])


if __name__ == "__main__":
    main()
