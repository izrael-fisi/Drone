"""Same-origin Global View portal for enterprise OpenID Connect and scoped gateway access."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, suppress
from dataclasses import dataclass
from hashlib import sha256
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
from secrets import compare_digest, token_hex, token_urlsafe
import shutil
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote_plus, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
import jwt

from gateway import CLIP, EVENTS, GRID_STREAM, RECORDING, STREAM, VIDEO_STATUS
from supabase_login import SOCIAL_PROVIDERS, SupabaseAuth, user_id

PREFIX = "/api/enterprise"
SESSION_COOKIE = "__Host-proxigo-session"
FLOW_COOKIE = "__Host-proxigo-login"
SUPABASE_COOKIE = "__Host-proxigo-supabase"
SESSION_AGE = 8 * 60 * 60
FLOW_AGE = 300
MFA_AGE = 600
MFA_LEEWAY = 30
MFA_CLAIM = "https://proxigo.ai/claims/mfa_at"
CONNECTION_CLAIM = "https://proxigo.ai/claims/connection"
SIGN_IN_LABELS = {"email": "Continue with email", "google": "Continue with Google", "microsoft": "Continue with Microsoft",
                  "github": "Continue with GitHub", "sso": "Sign in with your organization"}
SLUG = r"[a-z0-9][a-z0-9-]{0,79}"
GUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.IGNORECASE | re.ASCII)
SITE_ROUTE = re.compile(PREFIX + rf"/orgs/({SLUG})/sites/({SLUG})(/.+)")
SITE_READ = {"session", "activity", "monitoring", "configuration", "accounts", "assets", "vms-channels", "telemetry", "analytics-review", "analytics-report", "inspection-results", "commissioning-runs", "dock-schedules", "door-passages", "camera-native"}
SITE_READ.update(("response-procedure", "evidence-search", "cases", "vms-status", "operations", "dispatch",
                  "incident-history", "ai-jobs", "ai-quota", "ai-review-settings", "automatic-triage", "automatic-frame-review",
                  "device-degradation", "sensor-fusion"))
AI_JOB = re.compile(r"ai-jobs/[a-f0-9]{32}")
SITE_WRITE = {"incident", "maintenance", "configuration", "configuration/preview", "accounts", "assets",
              "commissioning-test", "vms-playback", "analytics-review", "commissioning-runs", "dock-schedules",
              "cases", "case-export", "configuration/import", "evidence-recording", "ai-jobs", "ai-quota", "ai-review-settings",
              "automatic-triage", "automatic-frame-review", "sensor-fusion", "retry-notification"}
ASSET = re.compile(r"/assets/[a-zA-Z0-9_.-]+")
MAX_JSON = 1_048_576
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
ENTERPRISE_HTML = b'<html data-enterprise="true" '


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


HTTP = build_opener(ProxyHandler({}), NoRedirect())


def digest(value):
    return sha256(value.encode()).hexdigest()


def endpoint(value, *, origin=False, gateway=False):
    parsed = urlsplit(value)
    loopback = parsed.hostname in LOOPBACK_HOSTS
    if (parsed.scheme != "https" and not (gateway and loopback and parsed.scheme == "http")
            or not parsed.hostname or parsed.username is not None or parsed.fragment or parsed.query
            or parsed.port == 0 or origin and parsed.path not in ("", "/")
            or re.search(r"[\x00-\x20\x7f\\]", value)):
        raise ValueError("Use a trusted HTTPS endpoint or a loopback HTTP gateway.")
    return value.rstrip("/") if origin else value


def read_json(url, **kwargs):
    with HTTP.open(Request(url, **kwargs), timeout=6) as response:
        data = response.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise ValueError("Identity provider response is too large.")
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object.")
    return payload


@dataclass(frozen=True)
class Site:
    origin: str
    secret: str


@dataclass(frozen=True)
class ClientCertificate:
    key: RSAPrivateKey
    thumbprint: str

    @classmethod
    def load(cls, certificate_file, key_file):
        key_path = Path(key_file)
        if key_path.stat().st_mode & 0o077:
            raise ValueError("Restrict the OIDC client private key to the portal user.")
        certificate = x509.load_pem_x509_certificate(Path(certificate_file).read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if not isinstance(key, RSAPrivateKey) or key.key_size < 2048 or key.public_key() != certificate.public_key():
            raise ValueError("Use a matching RSA client certificate and private key of at least 2048 bits.")
        thumbprint = base64.urlsafe_b64encode(certificate.fingerprint(hashes.SHA256())).rstrip(b"=").decode()
        return cls(key, thumbprint)

    def assertion(self, client_id, token_endpoint, now):
        return jwt.encode({"iss": client_id, "sub": client_id, "aud": token_endpoint, "jti": str(uuid4()),
                           "iat": now, "nbf": now, "exp": now + 120}, self.key, algorithm="PS256",
                          headers={"x5t#S256": self.thumbprint})


@dataclass(frozen=True)
class Organization:
    issuer: str
    client_id: str
    client_secret: str | None
    tenant: str
    tenant_claim: str | None
    token_auth: str
    sites: dict[str, Site]
    subject_claim: str
    certificate: ClientCertificate | None
    connections: dict[str, str]
    supabase: SupabaseAuth | None

    @property
    def binding(self):
        if self.supabase:
            return digest(json.dumps(["supabase", self.issuer, self.tenant, sorted(self.supabase.methods)]))
        return digest(json.dumps([self.issuer, self.client_id, self.tenant, self.tenant_claim,
                                  self.subject_claim, self.connections], sort_keys=True))


class Portal:
    def __init__(self, config, frontend, state):
        self.origin = endpoint(config["publicOrigin"], origin=True)
        production = config.get("production", False)
        if type(production) is not bool:
            raise ValueError("Set production to true or false.")
        public_origin = urlsplit(self.origin).hostname not in LOOPBACK_HOSTS
        if public_origin and not production:
            raise ValueError("Non-loopback portal origins require production mode with verified MFA.")
        self.frontend = frontend.resolve(strict=True)
        self.index = (self.frontend / "index.html").read_bytes()
        if ENTERPRISE_HTML not in self.index:
            if public_origin:
                raise ValueError("Non-loopback portals require the Global View production frontend build.")
            self.index = self.index.replace(b"<html ", ENTERPRISE_HTML, 1)
        self.state = state
        self.organizations = {}
        self.invitation_orgs = {}
        supabase = None
        if settings := config.get("supabaseAuth"):
            supabase = SupabaseAuth(endpoint(settings["origin"], origin=True), settings["publishableKey"],
                                    settings.get("methods", ["email", "google", "microsoft"]), read_json)
        issuer_tenants, gateways, secrets = {}, set(), set()
        for org_id, raw in config["organizations"].items():
            if not re.fullmatch(SLUG, org_id):
                raise ValueError("Use stable lowercase organization IDs.")
            provider = raw.get("identityProvider", "oidc")
            if provider not in ("oidc", "supabase") or provider == "supabase" and not supabase:
                raise ValueError("Configure the selected organization identity provider.")
            hosted = supabase if provider == "supabase" else None
            issuer = hosted.issuer if hosted else endpoint(raw["issuer"])
            tenant = raw["directoryTenant"]
            client = "authenticated" if hosted else raw["clientId"]
            secret = raw.get("clientSecret")
            tenant_claim = raw.get("tenantClaim")
            token_auth = raw.get("tokenEndpointAuthMethod", "client_secret_basic")
            subject_claim = raw.get("subjectClaim", "sub")
            connections = raw.get("auth0Connections", {})
            if (not isinstance(connections, dict)
                    or any(method not in SIGN_IN_LABELS or not isinstance(name, str)
                           or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) for method, name in connections.items())
                    or len(set(connections.values())) != len(connections)):
                raise ValueError("Configure a distinct Auth0 connection name for each enabled sign-in method.")
            if hosted and (connections or tenant_claim is not None or subject_claim != "sub" or secret is not None
                           or "tokenEndpointAuthMethod" in raw or "clientId" in raw or "issuer" in raw):
                raise ValueError("Supabase uses the project issuer and the user's ID from Supabase Auth.")
            if production and not (connections or hosted):
                raise ValueError("Production organizations require hosted sign-in with verified MFA.")
            if connections:
                if (tenant_claim not in (None, "org_id") or subject_claim != "sub"
                        or not re.fullmatch(r"org_[A-Za-z0-9]{1,90}", tenant) or tenant in self.invitation_orgs):
                    raise ValueError("Hosted login requires an Auth0 organization ID and the OIDC subject.")
                tenant_claim = "org_id"
                self.invitation_orgs[tenant] = org_id
            if (not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", tenant)
                    or not isinstance(client, str) or not client
                    or tenant_claim is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_claim)
                    or token_auth not in ("client_secret_basic", "client_secret_post", "private_key_jwt")
                    or subject_claim not in ("sub", "oid")
                    or subject_claim == "oid" and (tenant_claim != "tid" or not GUID.fullmatch(tenant))):
                raise ValueError("Configure a distinct organization issuer, client, and directory tenant.")
            certificate = None
            if not hosted and token_auth == "private_key_jwt":
                if secret is not None:
                    raise ValueError("Configure only one OIDC client credential.")
                certificate = ClientCertificate.load(raw["clientCertificate"], raw["clientPrivateKey"])
            elif not hosted and (not isinstance(secret, str) or not secret):
                raise ValueError("Configure the OIDC client secret.")
            issuer_key = issuer.rstrip("/")
            tenants = issuer_tenants.setdefault(issuer_key, set())
            if not hosted:
                if (supabase and issuer_key == supabase.issuer
                        or tenants and (not connections or None in tenants or tenant in tenants)):
                    raise ValueError("Only separately bound Auth0 organizations may share an OIDC issuer.")
                tenants.add(tenant if connections else None)
            elif tenant in tenants:
                raise ValueError("Each Supabase organization requires a distinct gateway directory binding.")
            else:
                tenants.add(tenant)
            sites = {}
            if not isinstance(raw["sites"], dict) or not 1 <= len(raw["sites"]) <= 64:
                raise ValueError("Configure between one and 64 sites per organization.")
            for site_id, site in raw["sites"].items():
                address, token = endpoint(site["origin"], origin=True, gateway=True), site["proxyToken"]
                if not re.fullmatch(SLUG, site_id) or address in gateways or token in secrets or not re.fullmatch(r"[0-9a-f]{64}", token):
                    raise ValueError("Each site needs a stable ID, unique gateway origin, and independent proxy token.")
                gateways.add(address)
                secrets.add(token)
                sites[site_id] = Site(address, token)
            self.organizations[org_id] = Organization(issuer, client, secret, tenant, tenant_claim, token_auth, sites,
                                                      subject_claim, certificate, connections, hosted)
        if not self.organizations:
            raise ValueError("Configure at least one organization.")
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS login_flows (
                    id TEXT PRIMARY KEY, browser TEXT NOT NULL, org TEXT NOT NULL, nonce TEXT NOT NULL,
                    verifier TEXT NOT NULL, token_endpoint TEXT NOT NULL, jwks_uri TEXT NOT NULL, expires INTEGER NOT NULL,
                    auth_binding TEXT, connection TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, org TEXT NOT NULL, subject TEXT NOT NULL, expires INTEGER NOT NULL, auth_binding TEXT
                );
                CREATE TABLE IF NOT EXISTS supabase_flows (
                    id TEXT PRIMARY KEY, org TEXT NOT NULL, auth_binding TEXT NOT NULL, expires INTEGER NOT NULL,
                    verifier TEXT, token TEXT, subject TEXT, provider_session TEXT, factor TEXT, attempts INTEGER NOT NULL DEFAULT 0
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            for table, name in (("login_flows", "auth_binding"), ("login_flows", "connection"), ("sessions", "auth_binding")):
                if name not in {column[1] for column in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} TEXT")
        self.state.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.state)

    def callback_url(self, org_id):
        return f"{self.origin}{PREFIX}/callback/{org_id}"

    def login_options(self, org_id):
        org = self.organizations.get(org_id)
        if not org:
            raise ValueError("Unknown organization.")
        if org.supabase:
            return [{"id": method, "label": label, **({"passwordForm": True} if method == "email" else {})}
                    for method, label in SIGN_IN_LABELS.items() if method in org.supabase.methods]
        if not org.connections:
            return [{"id": "", "label": SIGN_IN_LABELS["sso"]}]
        return [{"id": method, "label": label} for method, label in SIGN_IN_LABELS.items() if method in org.connections]

    def start_login(self, org_id, method="", *, invitation=None):
        org = self.organizations.get(org_id)
        if not org:
            raise ValueError("Unknown organization.")
        if invitation is not None:
            if method or not org.connections or not re.fullmatch(r"[A-Za-z0-9_.~-]{1,1024}", invitation):
                raise ValueError("Invalid organization invitation.")
        elif method not in (org.connections or {""}):
            raise ValueError("Unknown sign-in method for this organization.")
        connection = org.connections.get(method)
        metadata = read_json(org.issuer.rstrip("/") + "/.well-known/openid-configuration")
        if metadata.get("issuer") != org.issuer:
            raise ValueError("Unexpected identity issuer.")
        methods = metadata.get("code_challenge_methods_supported")
        if methods is not None and "S256" not in methods:
            raise ValueError("Identity provider must accept S256 PKCE.")
        authorize, token_endpoint, jwks_uri = (endpoint(metadata[key]) for key in
                                                ("authorization_endpoint", "token_endpoint", "jwks_uri"))
        state, browser, nonce, verifier = (token_urlsafe(32) for _ in range(4))
        challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        now = int(time.time())
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM login_flows WHERE expires<=?", (now,))
            db.execute("""INSERT INTO login_flows(id,browser,org,nonce,verifier,token_endpoint,jwks_uri,expires,auth_binding,connection)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""",
                       (digest(state), digest(browser), org_id, nonce, verifier, token_endpoint, jwks_uri, now + FLOW_AGE,
                        org.binding, connection))
        scope = "openid profile" if org.subject_claim == "oid" else "openid"
        query = {"client_id": org.client_id, "redirect_uri": self.callback_url(org_id), "response_type": "code",
                 "scope": scope, "state": state, "nonce": nonce, "code_challenge": challenge,
                 "code_challenge_method": "S256", "prompt": "login"}
        if org.connections:
            query["organization"] = org.tenant
        if connection:
            query["connection"] = connection
        if invitation:
            query["invitation"] = invitation
        return authorize + "?" + urlencode(query), browser

    def start_supabase_social(self, org_id, method):
        org = self.organizations[org_id]
        if not org.supabase or method not in org.supabase.methods or method not in SOCIAL_PROVIDERS:
            raise ValueError("Unknown social sign-in method.")
        browser, verifier = token_urlsafe(32), token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        callback = f"{self.origin}{PREFIX}/supabase/callback/{org_id}"
        self.save_supabase_flow(org_id, browser, int(time.time()) + FLOW_AGE, verifier=verifier)
        return org.supabase.authorize(method, callback, challenge), browser

    def save_supabase_flow(self, org_id, browser, expires, *, verifier=None, token=None, claims=None):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM supabase_flows WHERE expires<=?", (time.time(),))
            db.execute("""INSERT INTO supabase_flows(id,org,auth_binding,expires,verifier,token,subject,provider_session)
                          VALUES(?,?,?,?,?,?,?,?)""",
                       (digest(browser), org_id, self.organizations[org_id].binding, expires, verifier, token,
                        claims and claims["sub"], claims and claims["session_id"]))

    def start_supabase_mfa(self, org_id, payload):
        supabase = self.organizations[org_id].supabase
        token = payload["access_token"]
        claims = supabase.claims(token)
        if claims["aal"] != "aal1" or not self.member_sites(org_id, claims["sub"]):
            raise ValueError("This account has no site membership for this sign-in.")
        supabase.factors(token, claims["sub"])
        browser = token_urlsafe(32)
        self.save_supabase_flow(org_id, browser, min(claims["exp"], int(time.time()) + MFA_AGE), token=token, claims=claims)
        return browser

    def finish_supabase_social(self, org_id, browser, query):
        org = self.organizations[org_id]
        code = one(query, "code")
        if not org.supabase or not browser or not code or "error" in query:
            raise ValueError("Supabase did not authorize this sign-in.")
        with closing(self.connect()) as db, db:
            row = db.execute("""DELETE FROM supabase_flows WHERE id=? AND org=? AND auth_binding=? AND expires>?
                                 AND verifier IS NOT NULL RETURNING verifier""",
                             (digest(browser), org_id, org.binding, time.time())).fetchone()
        if not row:
            raise ValueError("Sign-in has expired.")
        return self.start_supabase_mfa(org_id, org.supabase.request("/token?grant_type=pkce", {
            "auth_code": code, "code_verifier": row[0],
        }))

    def pending_mfa(self, browser, *, verify=False):
        order = "UPDATE supabase_flows SET attempts=attempts+1" if verify else (
            "SELECT org,auth_binding,token,subject,provider_session,factor FROM supabase_flows")
        returning = " AND attempts<5 RETURNING org,auth_binding,token,subject,provider_session,factor" if verify else ""
        with closing(self.connect()) as db, db:
            row = db.execute(order + " WHERE id=? AND expires>? AND token IS NOT NULL" + returning,
                             (digest(browser), time.time())).fetchone()
        if not row or (org := self.organizations.get(row[0])) is None or not org.supabase or row[1] != org.binding:
            raise ValueError("Sign-in attempt expired. Start sign-in again.")
        return row[0], org.supabase, *row[2:]

    def enroll_supabase_factor(self, browser):
        _, supabase, token, subject, _, factor = self.pending_mfa(browser)
        if factor or supabase.factors(token, subject):
            raise ValueError("An authenticator is already available.")
        enrollment = supabase.enroll(token)
        with closing(self.connect()) as db, db:
            row = db.execute("""UPDATE supabase_flows SET factor=? WHERE id=? AND expires>? AND factor IS NULL RETURNING id""",
                             (enrollment["id"], digest(browser), time.time())).fetchone()
        if not row:
            raise ValueError("Sign-in attempt expired. Start sign-in again.")
        return enrollment

    def verify_supabase_factor(self, browser, factor, code, old):
        org_id, supabase, token, subject, provider_session, enrollment = self.pending_mfa(browser, verify=True)
        if not re.fullmatch(r"[0-9]{6}", code) or (user_id(factor) != enrollment and not any(
                existing["id"] == factor for existing in supabase.factors(token, subject))):
            raise ValueError("Enter the six-digit code from your authenticator.")
        claims = supabase.verify(token, factor, code)
        if claims["aal"] != "aal2" or claims["sub"] != subject or claims["session_id"] != provider_session:
            raise ValueError("Supabase did not verify MFA for this sign-in.")
        if not self.member_sites(org_id, subject) or not self.cancel_supabase_flow(browser, active=True):
            raise ValueError("This sign-in has expired or site access has changed.")
        return self.issue_session(org_id, subject, old)

    def cancel_supabase_flow(self, browser, *, active=False):
        if browser:
            with closing(self.connect()) as db, db:
                return db.execute("DELETE FROM supabase_flows WHERE id=? AND (NOT ? OR expires>?) RETURNING id",
                                  (digest(browser), active, time.time())).fetchone()

    def finish_login(self, org_id, state, browser, query):
        now = int(time.time())
        org = self.organizations[org_id]
        with closing(self.connect()) as db, db:
            flow = db.execute("""DELETE FROM login_flows WHERE id=? AND browser=? AND org=? AND expires>? AND auth_binding=?
                                 RETURNING nonce,verifier,token_endpoint,jwks_uri,expires,connection""",
                              (digest(state), digest(browser), org_id, now, org.binding)).fetchone()
        if not flow:
            raise ValueError("Sign-in has expired.")
        code = one(query, "code")
        if not code or "error" in query or ("iss" in query and one(query, "iss") != org.issuer):
            raise ValueError("Identity provider did not authorize this sign-in.")
        nonce, verifier, token_endpoint, jwks_uri, expires, connection = flow
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.callback_url(org_id),
                "code_verifier": verifier}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if org.certificate:
            form.update(client_id=org.client_id, client_assertion=org.certificate.assertion(org.client_id, token_endpoint, now),
                        client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer")
        elif org.token_auth == "client_secret_post":
            form.update(client_id=org.client_id, client_secret=org.client_secret)
        else:
            encoded = f"{quote_plus(org.client_id)}:{quote_plus(org.client_secret)}"
            headers["Authorization"] = "Basic " + base64.b64encode(encoded.encode()).decode()
        token = read_json(token_endpoint, data=urlencode(form).encode(), headers=headers)["id_token"]
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Unexpected identity signing algorithm or key.")
        keys = read_json(jwks_uri)["keys"]
        candidates = [key for key in keys if key.get("kid") == header["kid"] and key.get("kty") == "RSA"
                      and key.get("use", "sig") == "sig" and key.get("alg", "RS256") == "RS256"]
        if len(candidates) != 1:
            raise ValueError("Identity signing key is unavailable.")
        key = jwt.PyJWK.from_dict(candidates[0], algorithm="RS256").key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=org.issuer, audience=org.client_id,
                            options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce"]}, leeway=30)
        if (claims.get("azp", org.client_id) != org.client_id
                or isinstance(claims["aud"], list) and len(claims["aud"]) != 1
                or not isinstance(claims["nonce"], str) or not compare_digest(claims["nonce"].encode(), nonce.encode())
                or org.tenant_claim and claims.get(org.tenant_claim) != org.tenant
                or not re.fullmatch(r"[!-~]{1,200}", claims["sub"])):
            raise ValueError("Identity claims did not match this organization.")
        if org.connections:
            verified_at = claims.get(MFA_CLAIM)
            expected_connections = (connection,) if connection else org.connections.values()
            if (claims.get(CONNECTION_CLAIM) not in expected_connections or type(verified_at) is not int
                    or not expires - FLOW_AGE - MFA_LEEWAY <= verified_at <= now + MFA_LEEWAY):
                raise ValueError("Sign-in requires proof of MFA for the selected connection.")
        if org.subject_claim == "oid":
            subject = claims.get("oid")
            if not isinstance(subject, str) or not GUID.fullmatch(subject):
                raise ValueError("Identity provider did not return a directory user object ID.")
            return subject.lower()
        return claims["sub"]

    def issue_session(self, org_id, subject, old):
        session = token_hex(32)
        now = int(time.time())
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM sessions WHERE expires<=? OR id=?", (now, digest(old)))
            db.execute("INSERT INTO sessions(id,org,subject,expires,auth_binding) VALUES(?,?,?,?,?)",
                       (digest(session), org_id, subject, now + SESSION_AGE, self.organizations[org_id].binding))
        return session

    def session(self, session):
        if not session:
            return None
        with closing(self.connect()) as db:
            row = db.execute("SELECT org,subject,auth_binding FROM sessions WHERE id=? AND expires>?",
                             (digest(session), time.time())).fetchone()
        if row and (org := self.organizations.get(row[0])) and row[2] == org.binding:
            return row[:2]

    def revoke(self, session):
        if session:
            with closing(self.connect()) as db, db:
                db.execute("DELETE FROM sessions WHERE id=?", (digest(session),))

    def gateway_request(self, org_id, site_id, subject, path, data=None, range_header=None):
        org = self.organizations[org_id]
        site = org.sites[site_id]
        headers = {"Authorization": "Bearer " + site.secret, "X-Proxigo-Identity": subject, "X-Proxigo-Tenant": org.tenant,
                   "X-Proxigo-Organization": org_id, "X-Proxigo-Site": site_id}
        if range_header:
            headers["Range"] = range_header
        if data is not None:
            headers["Content-Type"] = "application/json"
        return Request(site.origin + path, headers=headers, data=data)

    def member_sites(self, org_id, subject):
        def member(site_id):
            try:
                request = self.gateway_request(org_id, site_id, subject, "/api/site/session")
                with HTTP.open(request, timeout=3) as response:
                    account = json.loads(response.read(65_536))
                if account["siteId"] == site_id and account["organization"]["id"] == org_id and account["subject"] == subject:
                    return account
            except (OSError, ValueError, KeyError):
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            return [account for account in pool.map(member, self.organizations[org_id].sites) if account]


def one(query, key):
    values = query.get(key, [])
    return values[0] if len(values) == 1 else ""


def cookie(name, value="", age=0):
    return f"{name}={value}; Max-Age={age}; Path=/; HttpOnly; Secure; SameSite=Lax"


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(25)
        super().setup()

    def log_message(self, *_args):
        # Callback URLs contain single-use codes and must stay out of access logs.
        pass

    def reply(self, status, data=b"", content_type="application/json", headers=()):
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin" if content_type.startswith("text/html") else "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def error(self, status, message):
        self.reply(status, json.dumps({"error": message}).encode())

    def redirect(self, url, *cookies):
        self.reply(303, headers=[("Location", url), *(("Set-Cookie", value) for value in cookies)])

    def read_cookie(self, name):
        try:
            item = SimpleCookie(self.headers.get("Cookie", "")).get(name)
            return item.value if item else ""
        except CookieError:
            return ""

    def body(self, limit, content_type):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= limit or self.headers.get_content_type() != content_type:
            raise ValueError("Invalid request body.")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Incomplete request body.")
        return body

    def do_GET(self):
        with suppress(BrokenPipeError, ConnectionResetError):
            self.get()

    def get(self):
        portal = self.server.portal
        target = urlsplit(self.path)
        if callback := re.fullmatch(PREFIX + rf"/supabase/callback/({SLUG})", target.path):
            try:
                query = parse_qs(target.query, max_num_fields=10)
                browser = portal.finish_supabase_social(callback[1], self.read_cookie(SUPABASE_COOKIE), query)
                self.redirect("/?sign-in=mfa", cookie(SUPABASE_COOKIE, browser, MFA_AGE))
            except (OSError, ValueError, KeyError, TypeError, jwt.PyJWTError):
                self.redirect("/?sign-in=failed", cookie(SUPABASE_COOKIE))
            return
        if target.path == PREFIX + "/supabase/mfa":
            try:
                _, supabase, token, subject, _, enrollment = portal.pending_mfa(self.read_cookie(SUPABASE_COOKIE))
                if enrollment:
                    raise ValueError("Restart interrupted enrollment.")
                self.reply(200, json.dumps({"factors": supabase.factors(token, subject)}).encode())
            except (OSError, ValueError, KeyError, TypeError):
                self.error(401, "Sign-in attempt expired. Start sign-in again.")
            return
        callback = re.fullmatch(PREFIX + rf"/callback/({SLUG})", target.path)
        if callback:
            try:
                query = parse_qs(target.query, max_num_fields=10)
                org_id = callback[1]
                subject = portal.finish_login(org_id, one(query, "state"), self.read_cookie(FLOW_COOKIE), query)
                session = portal.issue_session(org_id, subject, self.read_cookie(SESSION_COOKIE))
                self.redirect("/", cookie(FLOW_COOKIE), cookie(SESSION_COOKIE, session, SESSION_AGE))
            except (OSError, ValueError, KeyError, TypeError, jwt.PyJWTError):
                self.redirect("/?sign-in=failed", cookie(FLOW_COOKIE))
            return
        if target.path == PREFIX + "/invite":
            try:
                query = parse_qs(target.query, max_num_fields=3)
                org_id = portal.invitation_orgs[one(query, "organization")]
                url, browser = portal.start_login(org_id, invitation=one(query, "invitation"))
                self.redirect(url, cookie(FLOW_COOKIE, browser, FLOW_AGE))
            except (OSError, ValueError, KeyError, TypeError):
                self.redirect("/?sign-in=failed", cookie(FLOW_COOKIE))
            return
        if target.path == PREFIX + "/session":
            session = self.authorized()
            if session:
                org_id, subject = session
                sites = portal.member_sites(org_id, subject)
                self.reply(200, json.dumps({"organizationId": org_id, "sites": sites}).encode())
            return
        if target.path == PREFIX + "/login-options":
            try:
                methods = portal.login_options(one(parse_qs(target.query, max_num_fields=1), "organization"))
                self.reply(200, json.dumps({"methods": methods}).encode())
            except ValueError:
                self.error(404, "Sign-in unavailable for this organization.")
            return
        match = SITE_ROUTE.fullmatch(target.path)
        if match:
            self.relay(match, target.query)
        elif target.path in ("/", "/monitoring"):
            self.reply(200, portal.index, "text/html; charset=utf-8")
        elif target.path in ("/proxigo-mark.png", "/drone-vision-nav.svg") or ASSET.fullmatch(target.path):
            asset = (portal.frontend / target.path[1:]).resolve()
            if asset.is_relative_to(portal.frontend) and asset.is_file():
                self.reply(200, asset.read_bytes(), mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
            else:
                self.error(404, "Page unavailable.")
        else:
            self.error(404, "Page unavailable.")

    def do_POST(self):
        with suppress(BrokenPipeError, ConnectionResetError):
            self.post()

    def post(self):
        portal = self.server.portal
        if self.headers.get("Origin") != portal.origin:
            self.error(403, "Use the Global View portal to submit this request.")
            return
        if self.path == PREFIX + "/login":
            try:
                query = parse_qs(self.body(4096, "application/x-www-form-urlencoded").decode(), max_num_fields=2)
                org_id, method = one(query, "organization"), one(query, "method")
                portal.cancel_supabase_flow(self.read_cookie(SUPABASE_COOKIE))
                if portal.organizations[org_id].supabase:
                    url, browser = portal.start_supabase_social(org_id, method)
                    self.redirect(url, cookie(FLOW_COOKIE), cookie(SUPABASE_COOKIE, browser, FLOW_AGE))
                else:
                    url, browser = portal.start_login(org_id, method)
                    self.redirect(url, cookie(FLOW_COOKIE, browser, FLOW_AGE), cookie(SUPABASE_COOKIE))
            except (OSError, ValueError, KeyError, TypeError):
                self.redirect("/?sign-in=failed", cookie(FLOW_COOKIE))
        elif self.path == PREFIX + "/supabase/password":
            try:
                query = parse_qs(self.body(16_384, "application/x-www-form-urlencoded").decode(), max_num_fields=3)
                org_id, email, password = (one(query, key) for key in ("organization", "email", "password"))
                supabase = portal.organizations[org_id].supabase
                if not supabase or "email" not in supabase.methods or not 1 <= len(email) <= 254 or not 1 <= len(password) <= 512:
                    raise ValueError("Enter your email and password.")
                portal.cancel_supabase_flow(self.read_cookie(SUPABASE_COOKIE))
                tokens = supabase.request("/token?grant_type=password", {"email": email, "password": password})
                browser = portal.start_supabase_mfa(org_id, tokens)
                self.redirect("/?sign-in=mfa", cookie(FLOW_COOKIE), cookie(SUPABASE_COOKIE, browser, MFA_AGE))
            except (OSError, ValueError, KeyError, TypeError, jwt.PyJWTError):
                self.redirect("/?sign-in=failed", cookie(SUPABASE_COOKIE))
        elif self.path == PREFIX + "/supabase/mfa/enroll":
            try:
                enrollment = portal.enroll_supabase_factor(self.read_cookie(SUPABASE_COOKIE))
                self.reply(200, json.dumps(enrollment).encode())
            except (OSError, ValueError, KeyError, TypeError):
                self.error(400, "Authenticator setup could not be started. Start sign-in again.")
        elif self.path == PREFIX + "/supabase/mfa/verify":
            try:
                data = json.loads(self.body(1024, "application/json"))
                session = portal.verify_supabase_factor(self.read_cookie(SUPABASE_COOKIE), data["factor"], data["code"],
                                                        self.read_cookie(SESSION_COOKIE))
                self.reply(204, headers=[("Set-Cookie", cookie(SUPABASE_COOKIE)),
                                         ("Set-Cookie", cookie(SESSION_COOKIE, session, SESSION_AGE))])
            except (OSError, ValueError, KeyError, TypeError, jwt.PyJWTError):
                self.error(400, "Verification failed. Check the code or start sign-in again.")
        elif self.path == PREFIX + "/logout":
            portal.revoke(self.read_cookie(SESSION_COOKIE))
            portal.cancel_supabase_flow(self.read_cookie(SUPABASE_COOKIE))
            self.reply(204, headers=[("Set-Cookie", cookie(SESSION_COOKIE)), ("Set-Cookie", cookie(FLOW_COOKIE)),
                                     ("Set-Cookie", cookie(SUPABASE_COOKIE))])
        elif match := SITE_ROUTE.fullmatch(self.path):
            self.relay(match)
        else:
            self.error(404, "Page unavailable.")

    def authorized(self):
        portal = self.server.portal
        session = portal.session(self.read_cookie(SESSION_COOKIE))
        if session and session[0] in portal.organizations:
            return session
        self.error(401, "Sign in with your organization account.")

    def relay(self, match, query=""):
        if not (session := self.authorized()):
            return
        portal = self.server.portal
        org_id, site_id, path = match.groups()
        writes = self.command == "POST"
        api_path = path.removeprefix("/api/site/") if path.startswith("/api/site/") else None
        allowed = (api_path in SITE_WRITE or path in ("/api/clips/hold", "/api/clips/package")) if writes else (
            api_path in SITE_READ or (api_path is not None and AI_JOB.fullmatch(api_path)) or any(pattern.fullmatch(path) for pattern in (STREAM, GRID_STREAM, VIDEO_STATUS, EVENTS, RECORDING, CLIP)))
        if org_id != session[0] or site_id not in portal.organizations[session[0]].sites or not allowed:
            self.error(404, "Site resource unavailable.")
            return
        try:
            body = self.body(MAX_JSON, "application/json") if writes else None
        except (OSError, ValueError):
            self.error(400, "Send a valid site request.")
            return
        request = portal.gateway_request(org_id, site_id, session[1], path + ("?" + query if query else ""),
                                         body, self.headers.get("Range"))
        try:
            timeout = 120 if path in ("/api/clips/package", "/api/site/case-export", "/api/site/cases", "/api/site/evidence-recording") or RECORDING.fullmatch(path) else 25
            try:
                upstream = HTTP.open(request, timeout=timeout)
            except HTTPError as error:
                upstream = error
        except (OSError, ValueError):
            self.error(502, "The site gateway is unavailable.")
            return
        with upstream:
            if 300 <= upstream.status < 400:
                self.error(502, "Unexpected site gateway response.")
                return
            self.send_response(upstream.status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Content-Disposition"):
                if value := upstream.headers.get(name):
                    self.send_header(name, value)
            self.end_headers()
            shutil.copyfileobj(upstream, self.wfile, length=65_536)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8775)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(args.config.read_text())
    if config.get("production") is not True and args.host not in LOOPBACK_HOSTS:
        parser.error("A development portal must listen on a loopback address.")
    portal = Portal(config, args.frontend, args.state)
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.portal = portal
        print(f"Enterprise portal listening on {server.server_name}:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
