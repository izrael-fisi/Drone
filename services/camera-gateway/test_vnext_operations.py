from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from gateway import CameraGateway
from onvif import CameraError
from site_collaboration import Conflict
from site_monitoring import SiteMonitoring


def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def tripwire():
    return {"kind": "line-crossing", "id": "entry", "direction": "negative", "name": "Inbound", "object": "person",
            "boundaryId": "bay", "mode": "observe", "minimumScore": .25, "minimumPixels": 12, "seconds": .5,
            "points": [[.5, .1], [.5, .9]]}


class OperationsTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.gateway = CameraGateway(Path(directory.name), "ffmpeg", (), 1, site_config={
            "site": "Fixture", "operators": {"admin": {"name": "Admin", "role": "org-admin", "cameras": "*",
                                                       "tokenSha256": "a" * 64}}})
        self.monitor = self.gateway.monitor
        self.admin = {"role": "org-admin", "subject": "admin", "name": "Admin", "cameras": "*"}
        self.operator = {"role": "operator", "subject": "tech", "name": "Tech", "cameras": ["camera"]}
        camera = {"id": "camera", "name": "Camera", "modality": "vision", "position": [0, 0], "analyticsPlacement": "on-premises",
                  "analyticsRules": [tripwire(), {**tripwire(), "id": "dock", "kind": "zone-entry", "direction": "either",
                    "object": "vehicle", "trackOccupancy": True, "points": [[.2, .2], [.8, .2], [.8, .8], [.2, .8]]}]}
        door = {"id": "door", "name": "Door", "modality": "access", "position": [0, 0], "linkedCameraId": "camera", "passageRuleId": "entry"}
        bay = {"id": "bay", "name": "Bay", "kind": "Access", "enabled": True, "sensorIds": ["camera", "door"],
               "vertices": [[0, 0], [.01, 0], [.01, .01], [0, .01]]}
        self.start = time.time() - 45
        with patch("time.time", return_value=self.start - 5):
            draft = self.monitor.configuration.change(self.admin, {"operation": "save", "publishedVersion": 0, "draftRevision": 0,
                "configuration": {"sensors": [camera, door], "boundaries": [bay]}})
            self.monitor.configuration.change(self.admin, {"operation": "publish", "publishedVersion": 0,
                "draftRevision": draft["draftRevision"], "message": "Pilot"})

    def event(self, identifier, kind, at, track=None, dock=False, received=None):
        data = {"type": "observation", "id": identifier, "sensorId": "camera" if track else "door",
                "occurredAt": utc(at), "eventType": kind, "summary": kind}
        if track:
            data["analytics"] = {"policyVersion": 1, "ruleId": "dock" if dock else "entry", "object": "vehicle" if dock else "person",
                                 "score": .95, "box": [.3, .25, .6, .65], "frame": [640, 480], "source": "live", "model": "fixture", "trackId": track}
        with patch("time.time", return_value=at + .1 if received is None else received):
            return self.gateway.telemetry.push(data, analytics_placement="on-premises" if track else None)

    def heart(self, at, running=True):
        with patch("time.time", return_value=at):
            self.monitor.analytics_heartbeat("camera", {"state": "running" if running else "no-video", "version": 1, "models": ["fixture"],
                                                        "pending": 0, "fps": 4 if running else 0, "frameAgeSeconds": .1 if running else None}, "on-premises")

    def test_dock_revisions_dst_scope_and_unassigned_history(self):
        service = self.monitor.dock_schedules
        service.zone = ZoneInfo("America/Los_Angeles")
        self.assertEqual(service.day_range("2026-03-08")[2] - service.day_range("2026-03-08")[1], 23 * 3600)
        day, lower, _, _ = service.day_range(None)
        arrival = max(lower + .002, self.start + 10)
        csv = "appointmentId,boundaryId,startsAt,endsAt\nload,bay," + utc(lower) + "," + utc(arrival + 10)
        draft = service.change(self.admin, {"operation": "preview", "day": day, "csv": csv})
        other = service.change(self.admin, {"operation": "preview", "day": day, "csv": csv.replace("load,", "other,")})
        self.assertEqual(service.change(self.admin, {"operation": "preview", "day": day, "csv": csv})["revision"], draft["revision"])
        service.change(self.admin, {"operation": "publish", "day": day, **draft})
        with self.assertRaises(Conflict):
            service.change(self.admin, {"operation": "publish", "day": day, **other})
        before = self.event("arrive", "zone-entry", arrival, "vehicle", dock=True)
        self.assertTrue(before["accepted"])
        outside = self.event("unassigned", "zone-entry", arrival + 11, "vehicle-2", dock=True)
        self.assertTrue(outside["accepted"])
        rows = service.read(self.operator, {"day": [day]})
        self.assertEqual(rows["appointments"][0]["status"], "Arrived")
        self.assertEqual([item["id"] for item in rows["unassigned"]], ["unassigned"])
        self.assertEqual(rows["bays"][0]["occupancy"], "Unknown")
        self.assertEqual(service.read({**self.operator, "cameras": []}, {"day": [day]})["appointments"], [])
        with self.assertRaises(CameraError):
            service.change(self.admin, {"operation": "preview", "day": day, "csv": csv.replace(",bay,", ",unknown,")})

    def episode(self, when, index, crossing_count=2, extra_grant=False, outage=False, delay=0):
        prefix = str(index) + "-"
        self.heart(when - 1)
        self.event(prefix + "grant", "access-granted", when - .5)
        self.event(prefix + "door-open", "door-opened", when)
        self.event(prefix + "door-close", "door-closed", when + 3)
        if extra_grant:
            self.event(prefix + "grant-extra", "access-granted", when + .25)
        if outage:
            self.heart(when + 2, False)
        for number in range(crossing_count):
            item = prefix + str(number)
            self.assertTrue(self.event(item, "line-crossing", when + .5 + number * .5, item, received=when + 4 + delay)["accepted"])
            self.assertFalse(self.event(item, "line-crossing", when + .5 + number * .5, item)["accepted"])
        self.heart(when + 4)

    def test_door_episode_restarts_and_classifies_in_order(self):
        when = self.start + 4
        self.episode(when, 1)
        self.monitor.door_passages.run_due(when + 17)
        self.assertEqual(self.monitor.door_passages.read(self.operator, {})["episodes"], [])
        restarted = SiteMonitoring(self.monitor.activity, "fixture", "fixture")
        restarted.door_passages.run_due(when + 19)
        [result] = restarted.door_passages.read(self.operator, {})["episodes"]
        self.assertEqual(result["state"], "possible-extra")
        self.assertEqual(result["inboundTracks"], 2)
        self.assertEqual([item["observation"]["eventType"] for item in result["events"]],
                         ["access-granted", "door-opened", "line-crossing", "line-crossing", "door-closed"])
        self.assertEqual(restarted.door_passages.read({**self.operator, "cameras": ["foreign"]}, {})["episodes"], [])
        self.assertIsNone(restarted.pending())

    def test_door_uncertainty_and_normal_results(self):
        start = self.start + 4
        for index, options in enumerate([{"crossing_count": 1}, {"extra_grant": True}, {"outage": True}, {"delay": 20}]):
            when = start + 65 * index
            self.episode(when, index, **options)
            self.monitor.door_passages.run_due(when + 26)
        self.event("missing-state", "access-granted", start + 285)
        self.monitor.door_passages.run_due(start + 347)
        results = self.monitor.door_passages.read(self.operator, {"status": ["all"]})["episodes"]
        self.assertEqual([item["state"] for item in reversed(results)], ["normal", "insufficient", "insufficient", "insufficient", "insufficient"])

    def test_native_events_require_local_connector_and_released_native_rule(self):
        clock = time.time() - 1
        with patch("time.time", return_value=clock - .5):
            config = self.monitor.configuration
            state = config.detail()
            sensors = state["published"]["sensors"]
            sensors[0]["analyticsRules"][0]["cameraNative"] = True
            sensors[1].pop("passageRuleId")
            draft = config.change(self.admin, {"operation": "save", "publishedVersion": 1, "draftRevision": state["draftRevision"],
                "configuration": state["published"]})
            config.change(self.admin, {"operation": "publish", "publishedVersion": 1, "draftRevision": draft["draftRevision"],
                "message": "Use native observations"})
        event = {"type": "observation", "id": "native-fixture", "sensorId": "camera", "occurredAt": utc(clock), "eventType": "line-crossing",
                 "summary": "Camera", "nativeAnalytics": {"source": "onvif-profile-m", "ruleId": "entry", "objectId": "71"}}
        with self.assertRaises(PermissionError):
            self.gateway.telemetry.push(event)
        self.assertTrue(self.gateway.telemetry.push(event, native_camera="camera")["accepted"])
        self.assertFalse(self.gateway.telemetry.push(event, native_camera="camera")["accepted"])
        self.assertEqual(len(self.monitor.native_events.read(self.operator)["events"]), 1)
        self.assertEqual(self.monitor.native_events.read({**self.operator, "cameras": ["outside"]})["events"], [])
        self.assertIsNone(self.monitor.pending())
        with self.monitor.activity.connect() as db:
            self.assertEqual([row["id"] for row in self.monitor.analytics_policy(db, "camera", "on-premises")["rules"]], ["dock"])

    def test_additive_schemas_open_existing_site_evidence(self):
        when = self.start + 4
        self.episode(when, 1)
        with self.monitor.activity.connect() as db:
            for table in ("dock_schedules", "dock_schedule_heads", "dock_checkins", "door_passage_episodes",
                          "door_passage_heartbeat", "native_camera_status", "inspection_opc_results"):
                db.execute("DROP TABLE " + table)
        restarted = SiteMonitoring(self.monitor.activity, "fixture", "fixture")
        self.assertEqual(restarted.view(self.operator, 0, 0, 0, 0)["publishedVersion"], 1)
        self.assertEqual(len(restarted.view(self.operator, 0, 0, 0, 0)["siteEvents"]), 5)
        self.assertEqual(restarted.dock_schedules.read(self.operator, {})["revision"], 0)
        self.assertEqual(restarted.native_events.read(self.operator)["events"], [])
