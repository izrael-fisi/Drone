"""Verify an evidence bundle using a public key trusted outside the bundle."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import tempfile
from zipfile import BadZipFile, ZipFile


def verify(package, public_key):
    fingerprint = sha256(public_key.read_bytes()).hexdigest()
    with ZipFile(package) as bundle, tempfile.TemporaryDirectory() as directory:
        names = bundle.namelist()
        if len(names) > 7 or len(set(names)) != len(names):
            raise ValueError("Unexpected evidence package contents.")
        if bundle.getinfo("manifest.json").file_size > 65_536 or bundle.getinfo("manifest.sig").file_size > 1024:
            raise ValueError("Invalid manifest size.")
        root = Path(directory)
        manifest_bytes = bundle.read("manifest.json")
        (root / "manifest.json").write_bytes(manifest_bytes)
        (root / "manifest.sig").write_bytes(bundle.read("manifest.sig"))
        subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(public_key), "-signature", str(root / "manifest.sig"), str(root / "manifest.json")],
                       check=True, capture_output=True, timeout=10)
        manifest = json.loads(manifest_bytes)
        if manifest.get("version") != 1 or manifest["signingKeySha256"] != fingerprint or not isinstance(manifest.get("clips"), list):
            raise ValueError("Unexpected evidence manifest or signing key.")
        verified = {"manifest.json", "manifest.sig", "signing-key.pem"}
        if bundle.getinfo("signing-key.pem").file_size > 4096 or sha256(bundle.read("signing-key.pem")).hexdigest() != fingerprint:
            raise ValueError("Bundled key does not match the pinned key.")
        for clip in manifest["clips"]:
            name = clip["file"]
            if not re.fullmatch(r"clip-[1-4]\.mp4", name) or name in verified or bundle.getinfo(name).file_size > 32 * 1024 * 1024:
                raise ValueError("Unexpected clip name or length.")
            digest = sha256()
            with bundle.open(name) as media:
                for chunk in iter(lambda: media.read(65_536), b""):
                    digest.update(chunk)
            if digest.hexdigest() != clip["sha256"]:
                raise ValueError(f"Checksum mismatch for {name}.")
            verified.add(name)
        if set(names) != verified or len(verified) == 3:
            raise ValueError("Evidence files do not match the manifest.")
    return fingerprint


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("trusted_key", type=Path)
    args = parser.parse_args()
    try:
        print("Evidence verified. Signing key SHA-256 " + verify(args.package, args.trusted_key))
    except (BadZipFile, KeyError, ValueError, TypeError, AttributeError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Evidence verification failed. {exc}\n")
