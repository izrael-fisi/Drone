"""Prepare task-scoped worker configuration from ECS-provided settings."""

import json
import os
from pathlib import Path
import re
import sys

from site_activity import valid_event_device

GROUND_MODELS = {
    "yolox_nano": "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d",
    "yolox_s": "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
}


def ground_model(name, sha256=None):
    if name in ("yolo11s", "yolo11l"):
        if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError("Supply the SHA-256 of the approved YOLO11 ONNX export.")
        return {"kind": "yolo11", "path": f"/models/{name}.onnx", "sha256": sha256}
    if name not in GROUND_MODELS:
        raise ValueError("Choose a supported ground model for this ECS image.")
    return {"kind": "yolox", "path": f"/models/{name}.onnx", "sha256": GROUND_MODELS[name]}


def detail_pose_model(sha256, name="yolo11l-pose"):
    if name not in ("yolo11s-pose", "yolo11l-pose"):
        raise ValueError("Choose a supported YOLO11 pose model for this ECS image.")
    if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Supply the SHA-256 of the approved YOLO11 pose ONNX export.")
    return {"path": f"/models/{name}.onnx", "sha256": sha256}


def person_model(sha256):
    if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Supply the SHA-256 of the approved YOLO11 person export.")
    return {"path": "/models/yolo11-person.onnx", "sha256": sha256}


def prepare(environment, private):
    supplied = json.loads(environment.pop("PROXIGO_CCTV_SECRET"))
    token = supplied["gatewayToken"]
    if not isinstance(token, str) or not isinstance(supplied["source"], str) or not isinstance(supplied["gateway"], str):
        raise ValueError("Invalid worker secret fields.")
    identity = {"organizationId": environment["PROXIGO_ORGANIZATION_ID"], "siteId": environment["PROXIGO_SITE_ID"],
                "sensorId": environment["PROXIGO_CAMERA_ID"]}
    if not all(valid_event_device(value) for value in identity.values()):
        raise ValueError("Invalid worker identity.")
    model = ground_model(environment.get("PROXIGO_CCTV_GROUND_MODEL", "yolox_nano"),
                         environment.get("PROXIGO_CCTV_GROUND_MODEL_SHA256"))
    if "inferenceFps" in supplied:
        model["fps"] = supplied["inferenceFps"]
    if "inferenceThreads" in supplied:
        model["threads"] = supplied["inferenceThreads"]
    for option in ("detailRegion", "detailFromRules", "detailRegionLimit", "detailObjects", "detailPoseFallback", "rotationDegrees",
                   "personTrackSupport", "personMotionAssociation", "personTripwireRecovery", "personColorAssociation",
                   "vehicleStopConfirmationBoost"):
        if option in supplied:
            model[option] = supplied[option]
    pose = supplied.get("detailPose", False)
    if type(pose) is not bool:
        raise ValueError("Set detailPose to true or false for an ECS camera.")
    if pose:
        model["detailPose"] = detail_pose_model(environment.get("PROXIGO_CCTV_DETAIL_POSE_SHA256"),
                                               environment.get("PROXIGO_CCTV_DETAIL_POSE_MODEL", "yolo11l-pose"))
    person = supplied.get("personModel", False)
    if type(person) is not bool:
        raise ValueError("Set personModel to true or false for an ECS camera.")
    if person:
        model["personModel"] = person_model(environment.get("PROXIGO_CCTV_PERSON_MODEL_SHA256"))
    if "personModelMode" in supplied:
        if not person or supplied["personModelMode"] not in ("always", "monochrome"):
            raise ValueError("Set personModelMode to always or monochrome when personModel is enabled.")
        model["personModelMode"] = supplied["personModelMode"]
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
              "models": {"ground": model}}
    if "liveFrameBufferMs" in supplied:
        config["liveFrameBufferMs"] = supplied["liveFrameBufferMs"]
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
