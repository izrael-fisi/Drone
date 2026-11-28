"""Create and restore encrypted local gateway snapshots during maintenance."""

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time

from ai_quota import carry_job_usage
from gateway_state import StateLock
from onvif import CameraError
from site_accounts import SiteAccounts, timestamp

SNAPSHOT_FILES = {"activity.sqlite3", "cameras.json", "evidence-signing.pem"}
MAX_SNAPSHOT = 2 * 1024**3
MAX_MEMBERS = 84
CLIP = re.compile(r"clips/[0-9a-f]{32}\.(json|mp4)")


def digest(path):
    fingerprint = sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(65_536), b""):
            fingerprint.update(block)
    return fingerprint.hexdigest()


def checked_regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("A snapshot file is missing or is not a regular file.")
    return path


def check_db(db):
    if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise ValueError("The site database failed the integrity check.")
    binding = db.execute("SELECT organization,site,tenant FROM organization_binding WHERE singleton=1").fetchone()
    if not binding:
        raise ValueError("The state needs a configured enterprise site.")
    return list(binding)


def backup(state, destination, recipient):
    if destination.exists() or destination.resolve().is_relative_to(state.resolve()):
        raise ValueError("Choose a new encrypted backup file outside the gateway state.")
    started = time.monotonic()
    with StateLock(state), tempfile.TemporaryDirectory(prefix=".gateway-backup-", dir=state) as scratch:
        stage = Path(scratch)
        source = checked_regular(state / "activity.sqlite3")
        snapshot = stage / source.name
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(snapshot)) as dst:
            binding = check_db(src)
            src.backup(dst)
            check_db(dst)
            if dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_ai_jobs'").fetchone():
                dst.execute("PRAGMA secure_delete=ON")
                carry_job_usage(dst, time.time())
                dst.execute("DELETE FROM site_ai_jobs")
                dst.commit()
                dst.execute("VACUUM")
            if dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automatic_triage_setting'").fetchone():
                dst.execute("UPDATE automatic_triage_setting SET enabled=0, revision=revision+1")
                dst.execute("UPDATE automatic_triage_reports SET state='timeline',reason='restart' WHERE state='processing'")
                dst.commit()
            if dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_video_fallback_setting'").fetchone():
                dst.execute("UPDATE ai_video_fallback_setting SET enabled=0, revision=revision+1")
                dst.commit()
            if dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automatic_frame_review_setting'").fetchone():
                dst.execute("UPDATE automatic_frame_review_setting SET enabled=0, revision=revision+1")
                dst.commit()
        snapshot.chmod(0o600)
        members = {"activity.sqlite3": snapshot}
        for name in SNAPSHOT_FILES - members.keys():
            path = state / name
            if path.exists():
                members[name] = checked_regular(path)
        cache = state / "clips"
        if cache.is_symlink():
            raise ValueError("Use a local clip directory for backups.")
        for catalog in sorted(cache.glob("*.json")):
            if not CLIP.fullmatch("clips/" + catalog.name):
                continue
            content = json.loads(checked_regular(catalog).read_text())
            if content.get("heldUntil", 0) <= time.time():
                continue
            clip = checked_regular(catalog.with_suffix(".mp4"))
            if digest(clip) != content.get("sha256"):
                raise ValueError("A held clip failed its original checksum.")
            members.update({f"clips/{catalog.name}": catalog, f"clips/{clip.name}": clip})
        if len(members) >= MAX_MEMBERS or sum(p.stat().st_size for p in members.values()) > MAX_SNAPSHOT:
            raise ValueError("The snapshot exceeds the supported backup size.")
        manifest = {"version": 1, "binding": binding, "createdAt": time.time(),
                    "files": {name: {"bytes": path.stat().st_size, "sha256": digest(path)} for name, path in sorted(members.items())}}
        (stage / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        archive = stage / "snapshot.tar"
        with tarfile.open(archive, "w") as package:
            for name, path in {**members, "manifest.json": stage / "manifest.json"}.items():
                package.add(path, arcname=name, recursive=False)
        handle, temporary_name = tempfile.mkstemp(prefix=".gateway-encrypted-", dir=destination.parent)
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm", "-stream", "-outform", "DER",
                            "-in", str(archive), "-out", str(temporary), str(recipient)], check=True, capture_output=True, timeout=300)
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        marker = stage / "backup-status.json"
        marker.write_text(json.dumps({"createdAt": manifest["createdAt"], "elapsedSeconds": round(time.monotonic() - started, 2)}))
        os.replace(marker, state / "backup-status.json")
    return {"createdAt": manifest["createdAt"], "files": len(members), "elapsedSeconds": round(time.monotonic() - started, 2)}


def reconcile(db, roster, binding):
    organization = roster.get("organization", {})
    if [organization.get("id"), roster.get("siteId"), organization.get("directoryTenant")] != binding:
        raise ValueError("The approved access roster must match the backed-up organization, site, and directory tenant.")
    operators = roster.get("operators")
    if not isinstance(operators, dict) or not operators:
        raise ValueError("Supply the current approved operator roster.")
    accounts = []
    for subject, member in operators.items():
        if not isinstance(member, dict):
            raise ValueError("The roster contains an invalid account.")
        if member.get("active", True) is False:
            continue
        name, role, cameras = SiteAccounts.details({**member, "subject": subject})
        token_hash = member.get("tokenSha256")
        if token_hash is not None and (not isinstance(token_hash, str) or not re.fullmatch("[a-f0-9]{64}", token_hash)):
            raise ValueError("Use newly issued site operator token hashes or OIDC-only access.")
        accounts.append((subject, name, role, cameras, token_hash))
    if not any(item[2] == "org-admin" for item in accounts):
        raise ValueError("The restored site requires an approved organization administrator.")
    if {item[4] for item in accounts if item[4]} & {row[0] for row in db.execute("SELECT token_hash FROM organization_accounts WHERE token_hash IS NOT NULL")}:
        raise ValueError("Issue new site tokens before restoring the gateway.")
    db.execute("DELETE FROM organization_accounts")
    for account in accounts:
        SiteAccounts.insert(db, *account)
    db.execute("DELETE FROM viewer_links")
    db.execute("DELETE FROM site_viewer_issuers")
    db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
               (timestamp(), "restore-administrator", "gateway-restore", binding[1], f"Reconciled {len(accounts)} approved accounts"))
    return len(accounts)


def restore(source, destination, recipient, private_key, roster_path):
    if destination.exists():
        raise ValueError("Restore into a new gateway state directory.")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=".gateway-restore-", dir=destination.parent) as scratch:
        stage = Path(scratch)
        archive = stage / "snapshot.tar"
        subprocess.run(["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(source),
                        "-recip", str(recipient), "-inkey", str(private_key), "-out", str(archive)],
                       check=True, capture_output=True, timeout=300)
        if archive.stat().st_size > MAX_SNAPSHOT + 2 * 1024**2:
            raise ValueError("The decrypted snapshot exceeds the supported size.")
        restored = stage / "restored"
        restored.mkdir(mode=0o700)
        seen, total = set(), 0
        with tarfile.open(archive, "r:") as package:
            for entry in package:
                if (entry.name not in SNAPSHOT_FILES | {"manifest.json"} and not CLIP.fullmatch(entry.name) or
                        not entry.isfile() or entry.name in seen or len(seen) >= MAX_MEMBERS):
                    raise ValueError("The backup contains an unexpected snapshot entry.")
                total += entry.size
                if total > MAX_SNAPSHOT or entry.name == "manifest.json" and entry.size > 65_536:
                    raise ValueError("The backup entry exceeds the supported size.")
                seen.add(entry.name)
                path = restored / entry.name
                path.parent.mkdir(mode=0o700, exist_ok=True)
                with package.extractfile(entry) as contents, path.open("xb") as output:
                    os.chmod(path, 0o600)
                    shutil.copyfileobj(contents, output)
        manifest_path = restored / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        members = manifest["files"]
        if manifest.get("version") != 1 or set(members) != seen - {"manifest.json"} or "activity.sqlite3" not in members:
            raise ValueError("The backup manifest does not match the restored files.")
        for name, record in members.items():
            path = restored / name
            if record != {"bytes": path.stat().st_size, "sha256": digest(path)}:
                raise ValueError("A restored snapshot file failed verification.")
        with closing(sqlite3.connect(restored / "activity.sqlite3")) as db, db:
            binding = check_db(db)
            if binding != manifest["binding"]:
                raise ValueError("The backup site identity failed verification.")
            count = reconcile(db, json.loads(roster_path.read_text()), binding)
        manifest_path.unlink()
        (restored / "backup-status.json").write_text(json.dumps({"createdAt": manifest["createdAt"], "restoredAt": time.time()}))
        destination.mkdir(mode=0o700)
        try:
            for path in restored.iterdir():
                path.rename(destination / path.name)
        except OSError:
            shutil.rmtree(destination)
            raise
    return {"snapshotCreatedAt": manifest["createdAt"], "accountsRestored": count, "elapsedSeconds": round(time.monotonic() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    save, load = sub.add_parser("backup"), sub.add_parser("restore")
    for mode in (save, load):
        mode.add_argument("--state-dir", required=True, type=Path)
        mode.add_argument("--archive", required=True, type=Path)
        mode.add_argument("--recipient-cert", required=True, type=Path)
    load.add_argument("--recipient-key", required=True, type=Path)
    load.add_argument("--approved-access", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = (backup(args.state_dir, args.archive, args.recipient_cert) if args.operation == "backup" else
                  restore(args.archive, args.state_dir, args.recipient_cert, args.recipient_key, args.approved_access))
    except (OSError, CameraError, ValueError, TypeError, KeyError, sqlite3.Error, tarfile.TarError, subprocess.SubprocessError) as issue:
        parser.exit(1, f"Gateway snapshot failed. {issue}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
