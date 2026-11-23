"""Export approved held clips and a detached, verifiable gateway signature."""

from hashlib import sha256
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_STORED

from onvif import CameraError


def build_package(archive, state, payload, operator):
    openssl = shutil.which("openssl")
    if not openssl:
        raise CameraError("OpenSSL is required for signed evidence exports.")
    requested, timeline, mask = (payload.get(field) for field in ("clips", "timeline", "mask"))
    incident = payload.get("incidentId")
    if not isinstance(incident, str) or not 1 <= len(incident) <= 450 or not isinstance(requested, list) or not 1 <= len(requested) <= 4:
        raise CameraError("Select up to four held clips for an incident.")
    if not isinstance(timeline, list) or len(timeline) > 50 or not all(isinstance(entry, dict) for entry in timeline):
        raise CameraError("The evidence timeline can include up to 50 events.")
    if mask is not None:
        if not isinstance(mask, list) or len(mask) != 4 or any(type(value) is not int or not 0 <= value <= 100 for value in mask):
            raise CameraError("Enter four whole-number redaction percentages.")
        x, y, width, height = mask
        if not width or not height or x + width > 100 or y + height > 100:
            raise CameraError("The redaction rectangle must fit inside the video frame.")

    with tempfile.TemporaryDirectory(dir=state) as scratch:
        root = Path(scratch)
        key = state / "evidence-signing.pem"
        public = root / "signing-key.pem"
        if not key.exists():
            draft = root / "private.pem"
            subprocess.run([openssl, "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-out", str(draft)],
                           check=True, timeout=5, capture_output=True)
            draft.chmod(0o600)
            draft.replace(key)
        subprocess.run([openssl, "pkey", "-in", str(key), "-pubout", "-out", str(public)], check=True, timeout=5, capture_output=True)
        manifest = {"version": 1, "incidentId": incident, "exportedBy": operator, "exportedAt": datetime.now(timezone.utc).isoformat(), "timeline": timeline, "clips": [],
                    "signingKeySha256": sha256(public.read_bytes()).hexdigest()}
        used = set()
        for index, reference in enumerate(requested):
            camera, clip = (reference.get("cameraKey"), reference.get("clipId")) if isinstance(reference, dict) else (None, None)
            if not isinstance(camera, str) or not re.fullmatch(r"[a-f0-9]{48}", camera) or not isinstance(clip, str) or not re.fullmatch(r"[a-f0-9]{32}", clip) or clip in used:
                raise CameraError("Select distinct held gateway clips.")
            used.add(clip)
            source = root / f"source-{index}.mp4"
            with archive.lock:
                entry = archive.entry(camera, clip)
                if not entry or not entry.get("heldUntil") or not entry.get("sha256"):
                    raise CameraError("Each selected clip needs an active evidence hold.")
                shutil.copyfile(archive.directory / (clip + ".mp4"), source)
            if entry["heldUntil"] <= datetime.now(timezone.utc).timestamp() or sha256(source.read_bytes()).hexdigest() != entry["sha256"]:
                raise CameraError("A clip failed its hold checksum. Evidence was not exported.")
            target = root / f"clip-{index + 1}.mp4"
            if mask:
                filter_expression = f"drawbox=x=iw*{x}/100:y=ih*{y}/100:w=iw*{width}/100:h=ih*{height}/100:color=black:t=fill"
                subprocess.run([archive.ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-i", str(source),
                    "-vf", filter_expression, "-map", "0:v:0", "-an", "-map_metadata", "-1", "-c:v", "libx264", "-threads", "2",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)], check=True, timeout=25, capture_output=True)
            else:
                source.replace(target)
            if target.stat().st_size > 32 * 1024 * 1024:
                raise CameraError("A clip exceeds the evidence export size limit.")
            manifest["clips"].append({"file": target.name, "sha256": sha256(target.read_bytes()).hexdigest(), "clipId": clip,
                                      "hold": {field: entry[field] for field in ("sha256", "heldAt", "heldUntil", "heldBy")}, "redactionPercent": mask})
        index = root / "manifest.json"
        signature = root / "manifest.sig"
        index.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        subprocess.run([openssl, "dgst", "-sha256", "-sign", str(key), "-out", str(signature), str(index)], check=True, timeout=5, capture_output=True)
        package = root / "evidence.zip"
        with ZipFile(package, "w", compression=ZIP_STORED) as bundle:
            for member in [index, signature, public, *root.glob("clip-*.mp4")]:
                bundle.write(member, member.name)
        return package.read_bytes(), manifest["signingKeySha256"]
