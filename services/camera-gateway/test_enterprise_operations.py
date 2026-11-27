"""Regression contracts for guided response, site evidence, inventory and recovery."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from gateway import CameraGateway
from gateway_backup import backup, restore
from gateway_state import StateLock
from inventory_import import change_import
from onvif import CameraError
from site_collaboration import Conflict
from vms_status import ConnectorState


CAMERA = {"id": "cam", "name": "North camera", "modality": "vision", "position": [0, 0],
          "cameraView": {"bearingDeg": 0, "fovDeg": 50, "rangeM": 100}}
DOOR = {"id": "door", "name": "North door", "modality": "access", "position": [0, 0], "linkedCameraId": "cam"}
AREA = {"id": "north", "name": "North", "kind": "Restricted", "enabled": True, "sensorIds": ["cam", "door"],
        "vertices": [[-.01, -.01], [.01, -.01], [.01, .01], [-.01, .01]]}
PROCEDURE = {"name": "Review forced door", "trigger": "door-forced-open", "dueSeconds": 30, "steps": [
    {"id": "check", "kind": "question", "text": "Is the evidence conclusive?", "yes": "record", "no": "record"},
    {"id": "record", "kind": "outcome", "text": "Record a finding."}]}
SUPERVISOR = {"subject": "admin", "name": "Alex", "cameras": "*", "role": "org-admin"}
OPERATOR = {"subject": "operator", "name": "Blair", "cameras": ["cam"], "role": "operator"}
OUTSIDER = {"subject": "limited", "name": "Casey", "cameras": ["other"], "role": "operator"}


class EnterpriseOperationsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = {"site": "Site", "siteId": "site", "organization": {"id": "organization", "name": "Organization"},
                       "operators": {entry["subject"]: entry for entry in (SUPERVISOR, OPERATOR, OUTSIDER)}}
        self.gateway = CameraGateway(self.root / "state", "ffmpeg", ["127.0.0.0/8"], 12, site_config=self.config)
        self.monitor = self.gateway.monitor

    def configuration(self, operation, **fields):
        current = self.monitor.configuration.detail()
        return self.monitor.configuration.change(SUPERVISOR, {**current, "operation": operation, **fields})

    def publish(self, procedures=(PROCEDURE,)):
        self.configuration("save", configuration={"sensors": [CAMERA, DOOR], "boundaries": [AREA], "procedures": list(procedures)})
        return self.configuration("publish", message="Release")

    def push(self, identifier, source="door", event="door-forced-open"):
        return self.gateway.telemetry.push({"id": identifier, "type": "observation", "sensorId": source, "eventType": event,
            "occurredAt": datetime.now(timezone.utc).isoformat(), "summary": f"Fixture {identifier}"})

    def incident(self):
        return self.monitor.dispatch(SUPERVISOR)["alerts"][0]["id"]

    def act(self, alert, revision, actor, kind, **fields):
        return self.gateway.collaboration.incident(actor, {"incidentId": alert, "cameraId": "cam", "revision": revision,
                                                           "action": {"kind": kind, **fields}})

    def test_procedure_pinned_and_replay_safe_through_handoff(self):
        self.publish()
        self.push("forced")
        identifier = self.incident()
        self.act(identifier, 0, SUPERVISOR, "claim")
        payload = {"actionId": "stable-action", "step": "check", "answer": "no", "text": "Footage gap"}
        first = self.act(identifier, 1, SUPERVISOR, "procedure-step", **payload)
        self.assertEqual(first, self.act(identifier, 1, SUPERVISOR, "procedure-step", **payload))
        with self.assertRaises(Conflict):
            self.act(identifier, 1, SUPERVISOR, "note", text="Stale note")
        with self.assertRaises(Conflict):
            self.act(identifier, 2, SUPERVISOR, "resolve", disposition="inconclusive")
        self.act(identifier, 2, SUPERVISOR, "handoff", nextAction="Write conclusion")
        self.act(identifier, 3, OPERATOR, "accept-handoff")
        self.publish([{**PROCEDURE, "name": "Later procedure"}])
        self.gateway = CameraGateway(self.root / "state", "ffmpeg", ["127.0.0.0/8"], 12, site_config=self.config)
        procedure = self.gateway.monitor.procedures.read(OPERATOR, identifier)["procedure"]
        self.assertEqual((procedure["version"], procedure["definition"]["name"], procedure["step"]), (1, PROCEDURE["name"], "record"))
        with self.assertRaises(PermissionError):
            self.gateway.monitor.procedures.read(OUTSIDER, identifier)
        self.act(identifier, 4, OPERATOR, "procedure-step", actionId="recorded", step="record", answer="recorded", text="Inconclusive")
        self.act(identifier, 5, OPERATOR, "resolve", disposition="inconclusive")
        self.assertEqual(self.gateway.monitor.dispatch(OPERATOR)["alerts"], [])

    def test_procedure_graph_and_single_deadline(self):
        with self.assertRaises(CameraError):
            self.publish([{**PROCEDURE, "steps": [{"id": "cycle", "kind": "finding", "text": "Invalid", "next": "cycle"}]}])
        self.publish()
        self.push("late")
        with self.gateway.activity.connect() as db:
            future = time.time() + 60
            self.monitor.procedures.enqueue_overdue(db, future)
            self.monitor.procedures.enqueue_overdue(db, future)
            notices = [json.loads(row[0]) for row in db.execute("SELECT payload FROM alert_deliveries")]
        self.assertEqual(sum(item["type"] == "incident-procedure-overdue" for item in notices), 1)
        self.act(self.incident(), 0, SUPERVISOR, "procedure-override", actionId="override-1", reason="Manual reassignment")
        self.assertIsNone(self.monitor.procedures.read(OPERATOR, self.incident())["procedure"]["step"])

    def test_paginated_search_case_scope_and_index_retention(self):
        self.publish()
        self.push("searchable")
        self.push("second", "cam", "line-crossing")
        service = self.monitor.cases
        service.index_batch()
        result = service.search(OPERATOR, {"q": ["Fixture"], "limit": ["1"]})
        self.assertEqual(len(result["results"]), 1)
        self.assertTrue(result["cursor"])
        self.push("late-index")
        service.index_batch()
        later = service.search(OPERATOR, {"cursor": [result["cursor"]], "q": ["Fixture"], "limit": ["100"]})
        self.assertFalse(any(row["label"] == "Fixture late-index" for row in later["results"]))
        self.assertEqual(service.search(OUTSIDER, {})["results"], [])
        self.assertIsNone(service.search(OUTSIDER, {})["searchableFrom"])
        event = next(row for row in service.search(OPERATOR, {})["results"] if row["source"] == "event")
        case = service.change(self.gateway, OPERATOR, {"operation": "create", "title": "Review gate", "sourceId": event["id"]})["case"]
        with self.assertRaises(PermissionError):
            service.read(self.gateway, OUTSIDER, case["id"])
        with self.assertRaises(Conflict):
            service.change(self.gateway, OPERATOR, {"operation": "close", "caseId": case["id"], "revision": 0})
        with self.gateway.activity.connect() as db:
            db.execute("DELETE FROM monitored_events")
        service.index_batch()
        self.assertFalse(any(row["source"] == "event" for row in service.search(OPERATOR, {})["results"]))
        self.assertEqual(len(service.read(self.gateway, OPERATOR, case["id"])["case"]["attachments"]), 1)

    def test_inventory_review_is_atomic_and_idempotent(self):
        state = self.publish()
        csv = "id,name,type,longitude,latitude,boundaryIds\nnew,New camera,camera,0,0,north\n"
        base = {"publishedVersion": state["publishedVersion"], "draftRevision": state["draftRevision"], "devicesCsv": csv}
        reviewed = change_import(self.monitor.configuration, SUPERVISOR, {**base, "operation": "preview"})
        self.assertEqual(len(reviewed["review"]["changes"]), 2)
        invalid = change_import(self.monitor.configuration, SUPERVISOR, {**base, "operation": "apply",
            "devicesCsv": csv + "bad,Invalid,camera,200,0,north\n"})
        self.assertTrue(invalid["review"]["errors"])
        self.assertIsNone(self.monitor.configuration.detail()["draft"])
        result = change_import(self.monitor.configuration, SUPERVISOR, {**base, "operation": "apply"})["configuration"]
        with self.assertRaises(Conflict):
            change_import(self.monitor.configuration, SUPERVISOR, {**base, "operation": "apply"})
        repeated = change_import(self.monitor.configuration, SUPERVISOR, {**base, "draftRevision": result["draftRevision"], "operation": "apply"})
        self.assertEqual(repeated["review"]["changes"], [])
        self.assertEqual(repeated["configuration"]["draftRevision"], result["draftRevision"])

    def test_connector_cursor_follows_atomic_acceptance(self):
        self.publish()
        status = ConnectorState(self.gateway.telemetry, "fixture", "https://127.0.0.1")
        row = {"type": "observation", "id": "source-1", "sensorId": "door", "eventType": "door-forced-open", "summary": "Door fixture",
               "occurredAt": datetime.now(timezone.utc).isoformat()}
        with patch.object(self.gateway.telemetry, "write", side_effect=OverflowError("Retry after receiver outage")):
            with self.assertRaises(OverflowError):
                status.ingest("events", 1, row, {"eventId": "source-1"})
        self.assertEqual(status.cursor("events"), 0)
        status.ingest("events", 1, row, {"eventId": "source-1"})
        status.ingest("events", 1, row, {"eventId": "source-1"})
        self.assertEqual(status.cursor("events"), 1)
        self.assertEqual(len(self.monitor.dispatch(SUPERVISOR)["alerts"]), 1)
        self.assertEqual(self.monitor.dispatch(SUPERVISOR)["alerts"][0]["observation"]["vendor"]["eventId"], "source-1")

    def test_coverage_outage_requires_known_unavailability_and_recovers(self):
        self.publish([{**PROCEDURE, "name": "Coverage response", "trigger": "coverage-loss", "boundaryId": "north"}])
        self.gateway.assets.change(SUPERVISOR, {"operation": "check-camera", "mode": "verify", "cameraId": "cam", "version": 1})
        now = time.time()
        with self.gateway.activity.connect() as db:
            for offset, mode in ((0, "receiving"), (1, "unavailable"), (50, "unknown"), (51, "unavailable"), (110, "unavailable")):
                self.monitor.coverage_operations.evaluate(db, {"cam": mode}, now + offset)
            self.assertEqual(db.execute("SELECT count(*) FROM monitored_alerts").fetchone()[0], 0)
            self.monitor.coverage_operations.evaluate(db, {"cam": "unavailable"}, now + 111)
            self.monitor.coverage_operations.evaluate(db, {"cam": "unavailable"}, now + 120)
            self.assertEqual(db.execute("SELECT count(*) FROM monitored_alerts").fetchone()[0], 1)
            self.monitor.coverage_operations.evaluate(db, {"cam": "receiving"}, now + 122)
        alert = self.monitor.dispatch(OPERATOR)["alerts"][0]
        self.assertEqual(alert["observation"]["eventType"], "coverage-loss")
        self.assertTrue(alert["coverageRecoveredAt"])
        self.assertIsNotNone(self.monitor.procedures.read(OPERATOR, alert["id"])["procedure"])

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for encrypted snapshots.")
    def test_backup_restore_reconciles_access_and_preserves_records(self):
        self.publish()
        self.push("persisted")
        cert, key, archive = (self.root / name for name in ("cert.pem", "key.pem", "snapshot.p7m"))
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=Fixture",
                        "-keyout", str(key), "-out", str(cert), "-days", "1"], check=True, capture_output=True)
        with StateLock(self.gateway.state):
            with self.assertRaises(ValueError):
                backup(self.gateway.state, archive, cert)
        receipt = backup(self.gateway.state, archive, cert)
        self.assertGreaterEqual(receipt["files"], 1)
        new = self.root / "restored"
        roster = self.root / "approved.json"
        roster.write_text(json.dumps({**self.config, "operators": {"admin": {**SUPERVISOR, "tokenSha256": sha256(b"new").hexdigest()}}}))
        restored = restore(archive, new, cert, key, roster)
        self.assertEqual(restored["accountsRestored"], 1)
        with sqlite3.connect(new / "activity.sqlite3") as db:
            self.assertEqual(db.execute("SELECT id FROM monitored_alerts").fetchone()[0], self.incident())
            self.assertEqual(db.execute("SELECT subject FROM organization_accounts").fetchall(), [("admin",)])
        self.assertFalse((new / "management-token").exists())
