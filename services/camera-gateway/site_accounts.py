"""Organization identity and account membership for an isolated site gateway."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from secrets import compare_digest, token_hex

from onvif import CameraError
from site_activity import valid_event_device

ROLES = ("viewer", "operator", "supervisor", "org-admin")


def is_admin(actor):
    return actor["role"] == "org-admin" and actor["cameras"] == "*"


def manages_site(actor):
    return actor["role"] in ("supervisor", "org-admin") and actor["cameras"] == "*"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80] or "site"


def scope(role, cameras):
    if role not in ROLES or not (cameras == "*" or isinstance(cameras, list) and len(cameras) <= 512 and all(map(valid_event_device, cameras))):
        raise CameraError("Choose a supported role and an explicit camera scope.")
    if role == "org-admin" and cameras != "*":
        raise CameraError("An organization admin requires site-wide scope.")
    return cameras if cameras == "*" else sorted(set(cameras))


class SiteAccounts:
    def __init__(self, activity, config, site, proxy_secret):
        self.activity, self.proxy_secret = activity, proxy_secret
        organization = config.get("organization", {"id": slug(site), "name": site})
        site_id = config.get("siteId", slug(site))
        if not isinstance(organization, dict) or not valid_event_device(organization.get("id")) or not valid_event_device(site_id):
            raise CameraError("Set stable organization and site IDs.")
        name, tenant = organization.get("name"), organization.get("directoryTenant")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or (tenant is not None and not valid_event_device(tenant)):
            raise CameraError("Set an organization name and optional directory tenant ID.")
        self.organization = {"id": organization["id"], "name": name.strip()}
        self.site_id, self.tenant = site_id, tenant
        members = config.get("operators")
        if not isinstance(members, dict) or not members:
            raise CameraError("Configure at least one named site operator.")
        with activity.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS organization_binding (
                    singleton INTEGER PRIMARY KEY CHECK (singleton=1), organization TEXT NOT NULL, site TEXT NOT NULL, tenant TEXT
                );
                CREATE TABLE IF NOT EXISTS organization_accounts (
                    subject TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, role TEXT NOT NULL, cameras TEXT NOT NULL,
                    token_hash TEXT UNIQUE, active INTEGER NOT NULL, changed_at TEXT NOT NULL
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            binding = db.execute("SELECT organization,site,tenant FROM organization_binding").fetchone()
            expected = (self.organization["id"], site_id, tenant)
            if binding and binding != expected:
                raise CameraError("This gateway state directory belongs to a different organization, site, or directory tenant.")
            if not binding:
                db.execute("INSERT INTO organization_binding VALUES(1,?,?,?)", expected)
                for subject, member in members.items():
                    if not isinstance(member, dict):
                        raise CameraError("Invalid account record.")
                    name, role, cameras = self.details({**member, "subject": subject})
                    digest = member.get("tokenSha256")
                    if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
                        raise CameraError("Operator token hashes must be SHA-256 hex strings.")
                    self.insert(db, subject, name, role, cameras, digest)

    @staticmethod
    def details(payload):
        subject, name, role = (payload.get(field) for field in ("subject", "name", "role"))
        if not isinstance(subject, str) or not 1 <= len(subject) <= 200 or subject != subject.strip() or any(ord(char) < 32 for char in subject):
            raise CameraError("Enter the stable account subject.")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise CameraError("Enter a unique operator name.")
        return name.strip(), role, scope(role, payload.get("cameras"))

    @staticmethod
    def insert(db, subject, name, role, cameras, digest=None):
        if db.execute("SELECT 1 FROM organization_accounts WHERE subject=? OR lower(name)=lower(?) OR token_hash=?", (subject, name, digest)).fetchone():
            raise CameraError("An account with that subject, operator name, or token already exists.")
        db.execute("INSERT INTO organization_accounts VALUES(?,?,?,?,?,1,?)", (subject, name, role, json.dumps(cameras), digest, timestamp()))

    @staticmethod
    def public_entry(row):
        subject, name, role, cameras, token_hash, active, changed = row
        return {"subject": subject, "name": name, "role": role, "cameras": json.loads(cameras), "active": bool(active),
                "hasToken": token_hash is not None, "changedAt": changed}

    def by_subject(self, subject):
        with self.activity.connect() as db:
            row = db.execute("SELECT * FROM organization_accounts WHERE subject=? AND active=1", (subject,)).fetchone()
        return self.public_entry(row) if row else None

    def authenticate(self, headers):
        supplied = headers.get("Authorization", "")
        if not supplied.startswith("Bearer "):
            return None
        token = supplied.removeprefix("Bearer ")
        if self.proxy_secret and compare_digest(token.encode(), self.proxy_secret.encode()):
            if self.tenant is not None and headers.get("X-Proxigo-Tenant") != self.tenant:
                return None
            return self.by_subject(headers.get("X-Proxigo-Identity", ""))
        digest = sha256(token.encode()).hexdigest()
        with self.activity.connect() as db:
            row = db.execute("SELECT * FROM organization_accounts WHERE token_hash=? AND active=1", (digest,)).fetchone()
        return self.public_entry(row) if row else None

    def list(self, actor):
        if not is_admin(actor):
            raise PermissionError("Organization account management requires an organization admin.")
        with self.activity.connect() as db:
            return {"organization": self.organization, "accounts": [self.public_entry(row) for row in db.execute(
                "SELECT * FROM organization_accounts ORDER BY name COLLATE NOCASE,subject")]}

    def change(self, actor, payload):
        if not is_admin(actor):
            raise PermissionError("Organization account management requires an organization admin.")
        operation, subject = payload.get("operation"), payload.get("subject")
        if operation not in ("create", "access", "disable", "enable", "rotate-token", "revoke-token") or not isinstance(subject, str) or not 1 <= len(subject) <= 200:
            raise CameraError("Choose an organization account and valid operation.")
        token = None
        with self.activity.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current_actor = db.execute("SELECT * FROM organization_accounts WHERE subject=? AND active=1", (actor["subject"],)).fetchone()
            if not current_actor or not is_admin(self.public_entry(current_actor)):
                raise PermissionError("Your organization admin access has changed.")
            if operation == "create":
                name, role, cameras = self.details(payload)
                self.insert(db, subject, name, role, cameras)
            else:
                row = db.execute("SELECT role,active FROM organization_accounts WHERE subject=?", (subject,)).fetchone()
                if not row:
                    raise CameraError("The account no longer exists.")
                if subject == actor["subject"] and operation in ("disable", "access", "rotate-token", "revoke-token"):
                    raise CameraError("Have another organization admin update your account or token.")
                changed = timestamp()
                if operation == "access":
                    role = payload.get("role")
                    cameras = scope(role, payload.get("cameras"))
                    db.execute("UPDATE organization_accounts SET role=?,cameras=?,changed_at=? WHERE subject=?", (role, json.dumps(cameras), changed, subject))
                elif operation in ("enable", "disable"):
                    db.execute("UPDATE organization_accounts SET active=?,changed_at=? WHERE subject=?", (operation == "enable", changed, subject))
                else:
                    if operation == "rotate-token" and not row[1]:
                        raise CameraError("Enable the account before generating a token.")
                    token = token_hex(32) if operation == "rotate-token" else None
                    db.execute("UPDATE organization_accounts SET token_hash=?,changed_at=? WHERE subject=?",
                               (sha256(token.encode()).hexdigest() if token else None, changed, subject))
            if operation in ("access", "disable", "rotate-token", "revoke-token"):
                db.execute("DELETE FROM viewer_links WHERE digest IN (SELECT digest FROM site_viewer_issuers WHERE subject=?)", (subject,))
                db.execute("DELETE FROM site_viewer_issuers WHERE subject=?", (subject,))
            db.execute("INSERT INTO activity_audit(occurred_at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
                       (timestamp(), actor["subject"], "account-" + operation, subject, self.organization["id"]))
            current = self.public_entry(db.execute("SELECT * FROM organization_accounts WHERE subject=?", (subject,)).fetchone())
        return {"account": current, **({"token": token} if token else {})}
