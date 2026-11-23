"""Resolve RTSP media profiles on an explicitly addressed ONVIF device."""

from base64 import b64encode
from datetime import datetime, timezone
from hashlib import sha1
from secrets import token_bytes
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm, HTTPRedirectHandler,
    ProxyHandler, Request, build_opener,
)
from xml.etree import ElementTree as ET

DEVICE = "http://www.onvif.org/ver10/device/wsdl"
MEDIA1 = "http://www.onvif.org/ver10/media/wsdl"
MEDIA2 = "http://www.onvif.org/ver20/media/wsdl"
SCHEMA = "http://www.onvif.org/ver10/schema"
SOAP = "http://www.w3.org/2003/05/soap-envelope"
WSSE_ROOT = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-"
WSSE = WSSE_ROOT + "wssecurity-secext-1.0.xsd"
WSU = WSSE_ROOT + "wssecurity-utility-1.0.xsd"
MAX_XML = 256 * 1024


class CameraError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tag(namespace, name):
    return f"{{{namespace}}}{name}"


def add(parent, namespace, name, text=None, **attributes):
    child = ET.SubElement(parent, tag(namespace, name), attributes)
    child.text = text
    return child


class OnvifDevice:
    def __init__(self, address, username, password, validate_url):
        self.address = address
        self.username = username
        self.password = password
        self.validate_url = validate_url
        self.host = urlsplit(address).hostname

    def request(self, url, namespace, operation, fill):
        envelope = ET.Element(tag(SOAP, "Envelope"))
        auth = HTTPPasswordMgrWithDefaultRealm()
        if self.username:
            auth.add_password(None, url, self.username, self.password)
            security = add(add(envelope, SOAP, "Header"), WSSE, "Security")
            token = add(security, WSSE, "UsernameToken")
            created = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            nonce = token_bytes(16)
            # UsernameToken PasswordDigest requires SHA-1 over nonce, timestamp, and password.
            digest = b64encode(sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
            add(token, WSSE, "Username", self.username)
            add(token, WSSE, "Password", digest, Type=WSSE_ROOT + "username-token-profile-1.0#PasswordDigest")
            add(token, WSSE, "Nonce", b64encode(nonce).decode(), EncodingType=WSSE_ROOT + "soap-message-security-1.0#Base64Binary")
            add(token, WSU, "Created", created)
        fill(add(add(envelope, SOAP, "Body"), namespace, operation))
        req = Request(url, ET.tostring(envelope, encoding="utf-8", xml_declaration=True), {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{namespace}/{operation}"',
        })
        try:
            opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPDigestAuthHandler(auth))
            with opener.open(req, timeout=4) as response:
                xml = response.read(MAX_XML + 1)
            if len(xml) > MAX_XML or b"<!DOCTYPE" in xml.upper():
                raise CameraError("The ONVIF response exceeded the supported format.")
            body = ET.fromstring(xml).find(tag(SOAP, "Body"))
        except (URLError, OSError, ValueError, ET.ParseError) as exc:
            raise CameraError(f"ONVIF {operation} failed. Check the device address, credentials, and clock.") from exc
        result = body.find(tag(namespace, operation + "Response")) if body is not None else None
        if result is None:
            raise CameraError(f"The camera did not return an ONVIF {operation} response.")
        return result

    def services(self):
        services = {}
        try:
            result = self.request(self.address, DEVICE, "GetServices", lambda op: add(op, DEVICE, "IncludeCapability", "false"))
            for service in result.findall(tag(DEVICE, "Service")):
                services[service.findtext(tag(DEVICE, "Namespace"))] = service.findtext(tag(DEVICE, "XAddr"))
        except CameraError:
            result = self.request(self.address, DEVICE, "GetCapabilities", lambda op: add(op, DEVICE, "Category", "Media"))
            services[MEDIA1] = result.findtext(f".//{tag(SCHEMA, 'Media')}/{tag(SCHEMA, 'XAddr')}")
        return services

    def service(self):
        services = self.services()
        for namespace in (MEDIA2, MEDIA1):
            if services.get(namespace):
                url = self.validate_url(services[namespace], ("http", "https"))
                if urlsplit(url).hostname != self.host:
                    raise CameraError("The ONVIF media service must be on the same camera address.")
                yield namespace, url

    def media(self):
        last_error = CameraError("No ONVIF Media or Media2 service was advertised.")
        for namespace, url in self.service():
            try:
                result = self.request(url, namespace, "GetProfiles", lambda op: None)
                profiles = [
                    {"token": token, "name": (profile.findtext(f"{{*}}Name") or token)[:120]}
                    for profile in result.findall(tag(namespace, "Profiles"))
                    if (token := profile.get("token")) and len(token) <= 200
                ]
                if profiles:
                    return namespace, url, profiles
                last_error = CameraError("The camera returned no selectable ONVIF profiles.")
            except CameraError as exc:
                last_error = exc
        raise last_error

    def stream(self, requested_profile):
        namespace, url, profiles = self.media()
        token = requested_profile or profiles[0]["token"]
        if not any(profile["token"] == token for profile in profiles):
            raise CameraError("Select a profile returned by this ONVIF camera.")

        def fill(op):
            if namespace == MEDIA2:
                add(op, namespace, "Protocol", "RTSP")
            else:
                setup = add(op, namespace, "StreamSetup")
                add(setup, SCHEMA, "Stream", "RTP-Unicast")
                add(add(setup, SCHEMA, "Transport"), SCHEMA, "Protocol", "RTSP")
            add(op, namespace, "ProfileToken", token)

        result = self.request(url, namespace, "GetStreamUri", fill)
        uri = self.validate_url(result.findtext(".//{*}Uri"), ("rtsp",))
        if urlsplit(uri).hostname != self.host:
            raise CameraError("The ONVIF stream must be on the same camera address.")
        return uri

    def clock(self):
        start = time.time()
        try:
            result = self.request(self.address, DEVICE, "GetSystemDateAndTime", lambda op: None)
            end = time.time()
            utc = result.find(f".//{tag(SCHEMA, 'UTCDateTime')}")
            if utc is None or end - start > 3:
                return None
            date, clock = (utc.find(tag(SCHEMA, field)) for field in ("Date", "Time"))
            if date is None or clock is None:
                return None
            parts = [int(path.findtext(tag(SCHEMA, field))) for path, fields in (
                (date, ("Year", "Month", "Day")), (clock, ("Hour", "Minute", "Second")),
            ) for field in fields]
            stamp = datetime(*parts, tzinfo=timezone.utc).timestamp()
            return {"clockOffsetSeconds": round(stamp - (start + end) / 2, 1), "clockCheckedAt": int(end * 1000)}
        except (CameraError, ValueError, TypeError, OverflowError):
            return None
