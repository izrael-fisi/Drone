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

import jwt

from gateway import CLIP, EVENTS, GRID_STREAM, RECORDING, STREAM, VIDEO_STATUS

PREFIX = "/api/enterprise"
SESSION_COOKIE = "__Host-proxigo-session"
FLOW_COOKIE = "__Host-proxigo-login"
SESSION_AGE = 8 * 60 * 60
FLOW_AGE = 300
SLUG = r"[a-z0-9][a-z0-9-]{0,79}"
SITE_ROUTE = re.compile(PREFIX + rf"/orgs/({SLUG})/sites/({SLUG})(/.+)")
SITE_READ = {"session", "activity", "monitoring", "configuration", "accounts", "assets", "vms-channels", "telemetry", "analytics-review", "analytics-report", "inspection-results", "commissioning-runs", "dock-schedules", "door-passages", "camera-native"}
SITE_READ.update(("response-procedure", "evidence-search", "cases", "vms-status", "operations", "dispatch", "incident-history", "ai-jobs", "automatic-triage", "device-degradation", "sensor-fusion"))
AI_JOB = re.compile(r"ai-jobs/[a-f0-9]{32}")
SITE_WRITE = {"incident", "maintenance", "configuration", "configuration/preview", "accounts", "assets",
              "commissioning-test", "vms-playback", "analytics-review", "commissioning-runs", "dock-schedules",
              "cases", "case-export", "configuration/import", "evidence-recording", "ai-jobs", "automatic-triage", "sensor-fusion"}
ASSET = re.compile(r"/assets/[a-zA-Z0-9_.-]+")
MAX_JSON = 1_048_576


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


HTTP = build_opener(ProxyHandler({}), NoRedirect())


def digest(value):
    return sha256(value.encode()).hexdigest()


def endpoint(value, *, origin=False, gateway=False):
    parsed = urlsplit(value)
    loopback = parsed.hostname in ("127.0.0.1", "localhost", "::1")
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
class Organization:
    issuer: str
    client_id: str
    client_secret: str
    tenant: str
    tenant_claim: str | None
    token_auth: str
    sites: dict[str, Site]


class Portal:
    def __init__(self, config, frontend, state):
        self.origin = endpoint(config["publicOrigin"], origin=True)
        self.frontend = frontend.resolve(strict=True)
        self.index = (self.frontend / "index.html").read_bytes().replace(b"<html ", b'<html data-enterprise="true" ', 1)
        self.state = state
        self.organizations = {}
        issuers, gateways, secrets = set(), set(), set()
        for org_id, raw in config["organizations"].items():
            if not re.fullmatch(SLUG, org_id):
                raise ValueError("Use stable lowercase organization IDs.")
            issuer = endpoint(raw["issuer"])
            tenant, client, secret = (raw[key] for key in ("directoryTenant", "clientId", "clientSecret"))
            tenant_claim = raw.get("tenantClaim")
            token_auth = raw.get("tokenEndpointAuthMethod", "client_secret_basic")
            if (issuer.rstrip("/") in issuers or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", tenant)
                    or not isinstance(client, str) or not client or not isinstance(secret, str) or not secret
                    or tenant_claim is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_claim)
                    or token_auth not in ("client_secret_basic", "client_secret_post")):
                raise ValueError("Configure a distinct organization issuer, client, and directory tenant.")
            issuers.add(issuer.rstrip("/"))
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
            self.organizations[org_id] = Organization(issuer, client, secret, tenant, tenant_claim, token_auth, sites)
        if not self.organizations:
            raise ValueError("Configure at least one organization.")
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS login_flows (
                    id TEXT PRIMARY KEY, browser TEXT NOT NULL, org TEXT NOT NULL, nonce TEXT NOT NULL,
                    verifier TEXT NOT NULL, token_endpoint TEXT NOT NULL, jwks_uri TEXT NOT NULL, expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, org TEXT NOT NULL, subject TEXT NOT NULL, expires INTEGER NOT NULL
                );
            """)
        self.state.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.state)

    def callback_url(self, org_id):
        return f"{self.origin}{PREFIX}/callback/{org_id}"

    def start_login(self, org_id):
        org = self.organizations.get(org_id)
        if not org:
            raise ValueError("Unknown organization.")
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
            db.execute("INSERT INTO login_flows VALUES(?,?,?,?,?,?,?,?)",
                       (digest(state), digest(browser), org_id, nonce, verifier, token_endpoint, jwks_uri, now + FLOW_AGE))
        query = urlencode({"client_id": org.client_id, "redirect_uri": self.callback_url(org_id), "response_type": "code",
                           "scope": "openid", "state": state, "nonce": nonce, "code_challenge": challenge,
                           "code_challenge_method": "S256", "prompt": "login"})
        return authorize + "?" + query, browser

    def finish_login(self, org_id, state, browser, query):
        now = int(time.time())
        with closing(self.connect()) as db, db:
            flow = db.execute("""DELETE FROM login_flows WHERE id=? AND browser=? AND org=? AND expires>?
                                 RETURNING nonce,verifier,token_endpoint,jwks_uri""",
                              (digest(state), digest(browser), org_id, now)).fetchone()
        if not flow:
            raise ValueError("Sign-in has expired.")
        org = self.organizations[org_id]
        code = one(query, "code")
        if not code or "error" in query or ("iss" in query and one(query, "iss") != org.issuer):
            raise ValueError("Identity provider did not authorize this sign-in.")
        nonce, verifier, token_endpoint, jwks_uri = flow
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.callback_url(org_id),
                "code_verifier": verifier}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if org.token_auth == "client_secret_post":
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
        return claims["sub"]

    def issue_session(self, org_id, subject, old):
        session = token_hex(32)
        now = int(time.time())
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM sessions WHERE expires<=? OR id=?", (now, digest(old)))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(session), org_id, subject, now + SESSION_AGE))
        return session

    def session(self, session):
        if not session:
            return None
        with closing(self.connect()) as db:
            return db.execute("SELECT org,subject FROM sessions WHERE id=? AND expires>?", (digest(session), time.time())).fetchone()

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
        if target.path == PREFIX + "/session":
            session = self.authorized()
            if session:
                org_id, subject = session
                sites = portal.member_sites(org_id, subject)
                self.reply(200, json.dumps({"organizationId": org_id, "sites": sites}).encode())
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
                url, browser = portal.start_login(one(query, "organization"))
                self.redirect(url, cookie(FLOW_COOKIE, browser, FLOW_AGE))
            except (OSError, ValueError, KeyError, TypeError):
                self.redirect("/?sign-in=failed", cookie(FLOW_COOKIE))
        elif self.path == PREFIX + "/logout":
            portal.revoke(self.read_cookie(SESSION_COOKIE))
            self.reply(204, headers=[("Set-Cookie", cookie(SESSION_COOKIE)), ("Set-Cookie", cookie(FLOW_COOKIE))])
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
    portal = Portal(json.loads(args.config.read_text()), args.frontend, args.state)
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.portal = portal
        print(f"Enterprise portal listening on {server.server_name}:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
