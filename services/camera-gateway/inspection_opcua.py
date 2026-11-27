"""Subscribe to one enrolled OPC UA inspection station and forward result metadata."""

import argparse
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from inspection_results import OUTCOMES, normalize_inspection
from onvif import CameraError, NoRedirect
from site_activity import valid_event_device

FIELDS = {"resultId", "occurredAt", "recipeId", "componentId", "partId", "lotId", "outcome", "simulated", "partial"}


class Station:
    def __init__(self, config, directory):
        self.camera, self.station = config["cameraId"], config["stationId"]
        self.endpoint, self.gateway = config["endpoint"], config["gateway"]
        self.outcomes, self.fields = config["outcomes"], config["fields"]
        if not all(map(valid_event_device, [self.camera, self.station])):
            raise ValueError("Set the registered camera and station IDs.")
        if (not isinstance(self.fields, dict) or self.fields.keys() - FIELDS
                or not {"resultId", "occurredAt", "recipeId", "componentId", "outcome"} <= self.fields.keys()
                or not ({"partId", "lotId"} & self.fields.keys())
                or not all(isinstance(name, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", name) for name in self.fields.values())
                or not isinstance(self.outcomes, dict) or set(self.outcomes.values()) != set(OUTCOMES)):
            raise ValueError("Map the station's result fields and all three outcomes.")
        target, gateway = urlsplit(self.endpoint), urlsplit(self.gateway)
        if target.scheme != "opc.tcp" or target.username is not None or not target.port or target.query or target.fragment:
            raise ValueError("Choose one explicit OPC UA station endpoint.")
        local = ip_address(target.hostname or "").is_loopback
        gateway_local = gateway.hostname in ("127.0.0.1", "::1", "localhost")
        if (not gateway.hostname or gateway.username is not None or gateway.query or gateway.fragment or gateway.path not in ("", "/")
                or gateway.scheme != "https" and (gateway.scheme != "http" or not gateway_local)):
            raise ValueError("Use a trusted HTTPS gateway or a local loopback HTTP gateway.")
        self.security = config.get("security")
        if not local and not self.security:
            raise ValueError("A physical station requires an enrolled client certificate and pinned server certificate.")
        self.fixture = bool(config.get("loopbackFixture"))
        if self.fixture and (not local or not gateway_local) or not self.security and not self.fixture:
            raise ValueError("Unencrypted sessions are available for explicit local protocol fixtures.")
        self.event_type = config["eventType"]
        self.event_source = config.get("eventSource", "i=2253")
        self.config_dir = directory
        self.token = (directory / config["tokenFile"]).read_text().strip()
        if not re.fullmatch(r"[a-f0-9]{64}", self.token):
            raise ValueError("Use the private enrolled inspection adapter token.")
        self.username = config.get("username")
        self.password = (directory / config["passwordFile"]).read_text().rstrip("\r\n") if self.username else None
        self.pending = directory / config["outbox"]
        self.pending.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with sqlite3.connect(self.pending) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS opc_inspection_outbox(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, rejected INTEGER NOT NULL DEFAULT 0)""")
        self.pending.chmod(0o600)

    def event_notification(self, event):
        try:
            fields = {key: getattr(event, name, None) for key, name in self.fields.items()}
            if fields.pop("partial", False) is True:
                return
            source = fields.pop("occurredAt")
            if not isinstance(source, datetime) or source.utcoffset() is None:
                return
            outcome = self.outcomes.get(str(fields.pop("outcome")))
            simulated = fields.pop("simulated", False)
            if outcome is None or type(simulated) is not bool:
                return
            external = fields.get("resultId")
            if isinstance(external, bytes):
                fields["resultId"] = external.hex()
            inspection = normalize_inspection({**{key: value for key, value in fields.items() if value not in (None, "")},
                "stationId": self.station, "source": "opc-ua", "outcome": outcome, "simulated": self.fixture or simulated})
            identity = json.dumps([self.station, inspection["resultId"]], separators=(",", ":"))
            identifier = "opc-" + sha256(identity.encode()).hexdigest()
            payload = {"type": "observation", "id": identifier, "sensorId": self.camera, "eventType": "inspection-result",
                       "occurredAt": source.astimezone(timezone.utc).isoformat(), "inspection": inspection}
            with sqlite3.connect(self.pending) as db:
                db.execute("INSERT OR IGNORE INTO opc_inspection_outbox(id,payload) VALUES(?,?)", (identifier, json.dumps(payload)))
        except (CameraError, ValueError, TypeError, sqlite3.Error):
            print("Skipped an invalid or unsaved OPC UA inspection result.", file=sys.stderr, flush=True)

    def deliver(self):
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with sqlite3.connect(self.pending) as db:
            rows = db.execute("SELECT seq,payload FROM opc_inspection_outbox WHERE rejected=0 ORDER BY seq LIMIT 16").fetchall()
        for sequence, payload in rows:
            request = Request(self.gateway.rstrip("/") + "/api/site/telemetry", payload.encode(),
                              {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            try:
                with opener.open(request, timeout=5) as response:
                    result = json.load(response)
                    if type(result.get("accepted")) is not bool:
                        return
            except HTTPError as issue:
                if issue.code != 400:
                    return
                with sqlite3.connect(self.pending) as db:
                    db.execute("UPDATE opc_inspection_outbox SET rejected=1 WHERE seq=?", (sequence,))
                print("Gateway rejected an inspection result. Review the station mapping and private outbox.", file=sys.stderr, flush=True)
            except (OSError, ValueError, AttributeError):
                return
            else:
                with sqlite3.connect(self.pending) as db:
                    db.execute("DELETE FROM opc_inspection_outbox WHERE seq=?", (sequence,))

    async def delivery_loop(self):
        while True:
            try:
                await asyncio.to_thread(self.deliver)
            except sqlite3.OperationalError:
                pass
            await asyncio.sleep(.5)

    async def collect(self):
        from asyncua import Client
        from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
        from asyncua.ua import UaError

        delivery = asyncio.create_task(self.delivery_loop())
        try:
            while True:
                try:
                    client = Client(self.endpoint, timeout=4)
                    if self.security:
                        files = [self.config_dir / self.security[key] for key in ("certificate", "privateKey", "serverCertificate")]
                        await client.set_security(SecurityPolicyBasic256Sha256, files[0], files[1], server_certificate=files[2])
                    if self.username:
                        client.set_user(self.username)
                        client.set_password(self.password)
                    async with client:
                        async with await client.create_subscription(200., self) as subscription:
                            await subscription.subscribe_events(self.event_source, self.event_type, queuesize=64)
                            print("OPC UA inspection subscription ready", flush=True)
                            while True:
                                await asyncio.sleep(1)
                                await client.nodes.server_state.read_value()
                except (UaError, OSError, ConnectionError, asyncio.TimeoutError):
                    print("OPC UA inspection connection interrupted. Reconnecting.", file=sys.stderr, flush=True)
                    await asyncio.sleep(1)
        finally:
            delivery.cancel()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        station = Station(json.loads(args.config.read_text()), args.config.absolute().parent)
        asyncio.run(station.collect())
    except KeyboardInterrupt:
        return
    except (ImportError, OSError, TypeError, ValueError, KeyError) as error:
        parser.error(f"Check the private station configuration and optional OPC UA environment ({type(error).__name__}).")


if __name__ == "__main__":
    main()
