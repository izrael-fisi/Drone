from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
from unittest import TestCase
from unittest.mock import Mock, patch

from milestone_connector import MilestoneConnector
from onvif import CameraError
from site_activity import SiteActivity
from site_collaboration import Conflict, SiteCollaboration
from site_monitoring import SiteMonitoring
from site_telemetry import SiteTelemetry


def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class PublishedSiteTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.activity = SiteActivity(self.directory / "site.sqlite3")
        self.admin_token = "1" * 64
        self.access = {"site": "East campus", "siteId": "east-campus", "organization": {"id": "harbor", "name": "Harbor", "directoryTenant": "entra-company"},
                       "operators": {"principal": {"name": "Site admin", "role": "org-admin", "cameras": "*", "tokenSha256": sha256(self.admin_token.encode()).hexdigest()}}}
        self.collaboration = SiteCollaboration(self.activity, self.access, "test-proxy-secret")
        self.monitor = self.collaboration.monitor = SiteMonitoring(self.activity, "harbor", "east-campus")
        self.telemetry = SiteTelemetry(self.activity, monitor=self.monitor)
        self.admin = self.collaboration.authenticate({"Authorization": "Bearer " + self.admin_token})
        self.config = {"sensors": [{"id": "door", "name": "Door", "modality": "access", "position": [3.2, 2.2], "linkedCameraId": "camera"},
                                   {"id": "camera", "name": "Camera", "modality": "vision", "position": [3.2, 2.2], "cameraProvider": "Milestone XProtect",
                                    "cameraChannel": "12345678-1234-1234-1234-123456789012"}],
                       "boundaries": [{"id": "yard", "name": "Yard", "kind": "Restricted", "sensorIds": ["door", "camera"], "enabled": True,
                                       "vertices": [[3, 2], [4, 2], [4, 3], [3, 3]]}],
                       "correlationRule": {"enabled": True, "windowSeconds": 30, "graceSeconds": 120}}

    def change(self, operation, **values):
        before = self.monitor.configuration.detail()
        return self.monitor.configuration.change(self.admin, {"operation": operation, "draftRevision": before["draftRevision"],
                                                               "publishedVersion": before["publishedVersion"], **values})

    def events(self, time):
        return ({"type": "observation", "sensorId": "door", "id": "open", "occurredAt": stamp(time), "eventType": "door-forced-open", "summary": "Door forced open"},
                {"type": "observation", "sensorId": "camera", "id": "cross", "occurredAt": stamp(time + 2), "eventType": "line-crossing", "summary": "Line crossing"})

    def test_site_versions_are_transactional_and_rollback_keeps_the_historical_rule(self):
        now = 1_800_000_000
        with patch("time.time", return_value=now):
            self.change("save", configuration=self.config)
            self.assertEqual(self.monitor.view(self.admin, 0, 0, 0, 0)["publishedVersion"], 0)
            with self.assertRaises(Conflict):
                self.monitor.configuration.change(self.admin, {"operation": "publish", "message": "Conflicting draft", "draftRevision": 0, "publishedVersion": 0})
            self.assertEqual(self.change("publish", message="Pilot")["publishedVersion"], 1)
        door, camera = self.events(now + 5)
        with patch("time.time", return_value=now + 15):
            self.assertTrue(self.telemetry.push(door)["accepted"])
            disabled = {**self.config, "boundaries": [{**self.config["boundaries"][0], "enabled": False}]}
            diff = self.change("save", configuration=disabled)
            self.assertEqual(diff["diff"][0]["fields"], [{"name": "enabled", "before": True, "after": False}])
            self.change("publish", message="Pause yard")
        with patch("time.time", return_value=now + 55):
            self.assertTrue(self.telemetry.push(camera)["accepted"])
            self.assertFalse(self.telemetry.push(camera)["accepted"])
            view = self.monitor.view(self.admin, 0, 0, 0, 0)
            self.assertEqual(view["publishedVersion"], 2)
            self.assertEqual([row["configurationVersion"] for row in view["alerts"]], [1, 1])
            self.assertEqual(view["alerts"][0]["correlation"]["seconds"], 2)
            notice = self.monitor.pending()
            self.assertEqual(json.loads(notice[1])["configurationVersion"], 1)
            self.monitor.delivered(notice[0])
            self.assertIsNone(self.monitor.pending())
            restored = self.change("restore", restoreVersion=1, message="Restore pilot")
            self.assertEqual(restored["publishedVersion"], 3)
            self.assertEqual(restored["history"][0]["restoredFrom"], 1)

    def test_invalid_geometry_and_unassigned_devices_cannot_be_published(self):
        config = {**self.config, "boundaries": [{**self.config["boundaries"][0], "sensorIds": ["other"]}]}
        with self.assertRaises(CameraError):
            self.change("save", configuration=config)
        config["boundaries"][0]["sensorIds"] = ["door"]
        config["boundaries"][0]["vertices"] = [[3, 2], [4, 3], [4, 2], [3, 3]]
        with self.assertRaises(CameraError):
            self.change("save", configuration=config)

    def test_account_scope_token_revocation_and_gateway_organization_binding(self):
        service = self.collaboration.accounts
        with self.assertRaises(CameraError):
            SiteCollaboration(self.activity, {**self.access, "siteId": "different-site"})
        self.assertIsNone(service.authenticate({"Authorization": "Bearer test-proxy-secret", "X-Proxigo-Tenant": "foreign", "X-Proxigo-Identity": "principal"}))
        self.assertEqual(service.authenticate({"Authorization": "Bearer test-proxy-secret", "X-Proxigo-Tenant": "entra-company", "X-Proxigo-Identity": "principal"})["name"], "Site admin")
        service.change(self.admin, {"operation": "create", "subject": "viewer", "name": "Viewer", "role": "viewer", "cameras": ["camera"]})
        first = service.change(self.admin, {"operation": "rotate-token", "subject": "viewer"})["token"]
        header = {"Authorization": "Bearer " + first}
        with self.assertRaises(PermissionError):
            service.list(service.authenticate(header))
        viewer_token = self.activity.issue_viewer("camera", 5, "viewer")["streamPath"].split("/")[1]
        self.assertEqual(self.activity.viewer(viewer_token), "camera")
        second = service.change(self.admin, {"operation": "rotate-token", "subject": "viewer"})["token"]
        self.assertIsNone(service.authenticate(header))
        self.assertIsNone(self.activity.viewer(viewer_token))
        self.assertIsNotNone(service.authenticate({"Authorization": "Bearer " + second}))
        self.assertNotIn(first, json.dumps(service.list(self.admin)))

    def test_xprotect_playback_uses_recorded_time_and_restricts_signaling_to_the_owner(self):
        now = 1_800_000_000
        with patch("time.time", return_value=now):
            self.change("save", configuration=self.config)
            self.change("publish", message="XProtect camera")
        with patch("time.time", return_value=now + 10):
            self.telemetry.push(self.events(now + 3)[0])
        identifier = self.monitor.view(self.admin, 0, 0, 0, 0)["alerts"][0]["id"]
        path = self.directory / "token"
        path.write_text("scoped-test-token")
        connector = MilestoneConnector("https://xprotect.example.test", path, None, self.telemetry)
        connector.request = Mock(return_value={"sessionId": "b4213fac-c2e3-41fb-ac41-e09caec9c866", "offerSDP": "v=0\r\n"})
        session = connector.playback(self.admin, {"operation": "start", "incidentId": identifier})["session"]
        self.assertEqual(connector.request.call_args.kwargs["body"]["playbackTimeNode"]["playbackTime"], stamp(now + 3))
        with self.assertRaises(PermissionError):
            connector.playback({**self.admin, "subject": "another"}, {"operation": "candidates", "session": session})
        answer = connector.playback(self.admin, {"operation": "answer", "session": session, "answer": "v=0\r\n", "candidates": []})
        self.assertTrue(answer["accepted"])
        self.assertEqual(connector.request.call_args.kwargs["method"], "PATCH")

    def test_recovery_waits_for_video_and_uses_the_axis_download_budget(self):
        now = 1_800_000_000
        with patch("time.time", return_value=now):
            self.change("save", configuration=self.config)
            self.change("publish", message="Recorder window")
        with patch("time.time", return_value=now + 4):
            door, _ = self.events(now + 3)
            self.telemetry.push(door)
        gateway = Mock(lock=threading.Lock(), cameras={"camera": {"key": "camera-key"}})
        gateway.video_status.return_value = {"state": "unavailable"}
        with patch("time.time", return_value=now + 25):
            gateway.stopping.wait.side_effect = [False, True]
            self.monitor.recover_recordings(gateway, 4096)
            gateway.archive.retrieve.assert_not_called()
            self.assertEqual(self.monitor.view(self.admin, 0, 0, 0, 0)["recordingCoverage"][0]["state"], "awaiting")
        with patch("time.time", return_value=now + 50):
            gateway.stopping.wait.side_effect = [False, True]
            gateway.video_status.return_value = {"state": "receiving"}
            gateway.archive.retrieve.return_value = {"state": "available"}
            self.monitor.recover_recordings(gateway, 4096)
            gateway.archive.retrieve.assert_called_once_with({"key": "camera-key", "deviceId": "camera"}, (now + 3) * 1000, rate_kbps=4096)
            self.assertEqual(self.monitor.view(self.admin, 0, 0, 0, 0)["recordingCoverage"][0]["state"], "available")

    def test_camera_events_roll_back_when_site_alert_evaluation_fails(self):
        now = 1_800_000_000
        with patch("time.time", return_value=now):
            self.change("save", configuration=self.config)
            self.change("publish", message="Native camera ingress")
        raw = {"id": "camera-native", "occurredAt": stamp(now + 1), "event": "line-crossing"}
        with patch("time.time", return_value=now + 5):
            with patch.object(self.monitor, "record", side_effect=RuntimeError("temporary failure")):
                with self.assertRaises(RuntimeError):
                    self.telemetry.push_camera("camera", raw)
            self.assertEqual(self.activity.events("camera", 0), [])
            self.assertEqual(self.telemetry.read(0)["events"], [])
            self.assertTrue(self.telemetry.push_camera("camera", raw))
            self.assertFalse(self.telemetry.push_camera("camera", raw))
            self.assertEqual(len(self.monitor.view(self.admin, 0, 0, 0, 0)["alerts"]), 1)
