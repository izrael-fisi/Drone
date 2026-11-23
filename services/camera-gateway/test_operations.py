from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.etree.ElementTree import fromstring

from onvif import CameraError
from onvif_archive import OnvifArchive, REPLAY, SEARCH
from onvif_capture import NTP_EPOCH, replay_timestamp
from site_activity import SiteActivity
from site_collaboration import Conflict, SiteCollaboration
from site_telemetry import SiteTelemetry
from video_integrity import FRAME_BYTES, inspect_sample


class OperationsTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.database = Path(scratch.name) / "activity.sqlite3"
        self.activity = SiteActivity(self.database)

    def test_video_samples_distinguish_dark_static_and_obstructed(self):
        self.assertEqual(inspect_sample(bytes(FRAME_BYTES))[0], "dark")
        self.assertEqual(inspect_sample(bytes([96]) * FRAME_BYTES)[0], "obstructed")
        frame = bytes([50, 200]) * (FRAME_BYTES // 2)
        state, digest = inspect_sample(frame)
        self.assertEqual(state, "clear")
        self.assertEqual(inspect_sample(frame * 2, digest)[0], "frozen")

    def test_onvif_replay_requires_the_original_timestamp_extension(self):
        event = 2_200_000_000.5
        packet = bytes([0x90]) + bytes(11) + struct.pack("!HHIIB3x", 0xABAC, 3, int(event + NTP_EPOCH) % 2**32, 2**31, 0x80)
        self.assertEqual(replay_timestamp(packet, event), (event, 0x80))
        self.assertIsNone(replay_timestamp(packet[:16], event))

    def test_profile_g_capture_only_requests_the_assigned_camera_recording(self):
        capture = OnvifArchive({"camera-01": "recording-01"}, lambda url, _schemes: url, "gst-python")
        stream = {"deviceId": "camera-01", "onvifAddress": "http://10.0.0.2/device", "url": "rtsp://user:pass@10.0.0.2/live"}
        event = datetime.fromisoformat("2026-11-22T18:40:00+00:00").timestamp()
        device = Mock()
        device.services.return_value = {SEARCH: "http://10.0.0.2/search", REPLAY: "http://10.0.0.2/replay"}
        device.request.side_effect = [
            fromstring("<Information><EarliestRecording>2026-11-22T18:39:00Z</EarliestRecording>"
                       "<LatestRecording>2026-11-22T18:41:00Z</LatestRecording></Information>"),
            fromstring("<Replay><Uri>rtsp://10.0.0.2/archive</Uri></Replay>"),
        ]
        with patch("onvif_archive.OnvifDevice", return_value=device), patch("onvif_archive.subprocess.run") as worker:
            worker.return_value.stdout = json.dumps({"start": event - 8, "end": event + 8})
            self.assertEqual(capture.capture(stream, event, self.database.parent / "clip.mkv"), (event - 8, event + 8))
            request = json.loads(worker.call_args.kwargs["input"])
            self.assertEqual(request["uri"], "rtsp://10.0.0.2/archive")
            self.assertLess(request["start"], event)
            self.assertLess(event, request["end"])
            self.assertEqual([call.args[2] for call in device.request.call_args_list], ["GetRecordingInformation", "GetReplayUri"])
            worker.reset_mock()
            device.services.return_value[REPLAY] = "http://10.0.0.3/replay"
            with self.assertRaises(CameraError):
                capture.capture(stream, event, self.database.parent / "clip.mkv")
            worker.assert_not_called()

    def test_telemetry_survives_restart_duplicate_and_failed_forward(self):
        first = SiteTelemetry(self.activity, "https://upstream.example.test/api/site/telemetry", "test-secret")
        event = {"type": "observation", "sensorId": "gate-01", "id": "1", "eventType": "door-forced-open", "summary": "Gate forced open",
                 "occurredAt": datetime.now(timezone.utc).isoformat()}
        self.assertTrue(first.push(event)["accepted"])
        restored = SiteTelemetry(SiteActivity(self.database), first.upstream, first.secret)
        self.assertFalse(restored.push(event)["accepted"])
        self.assertEqual(restored.read(0)["backlogCount"], 1)
        opening = Mock()
        opening.open.side_effect = OSError("unreachable")
        with patch("site_telemetry.build_opener", return_value=opening):
            restored.forward(Mock(wait=Mock(side_effect=[False, False, True])))
        self.assertEqual(restored.read(0)["backlogCount"], 1)
        opening.open.side_effect = None
        opening.open.return_value.__enter__ = Mock(return_value=Mock(status=200))
        opening.open.return_value.__exit__ = Mock(return_value=False)
        with patch("site_telemetry.build_opener", return_value=opening):
            restored.forward(Mock(wait=Mock(side_effect=[False, True])))
        rows = restored.read(0)
        self.assertEqual(rows["backlogCount"], 0)
        sent = json.loads(opening.open.call_args[0][0].data)
        self.assertEqual(sent["gatewayReceivedAt"], rows["events"][0]["gatewayReceivedAt"])
        self.assertEqual(restored.read(rows["events"][0]["sequence"])["events"], [])

    def test_shared_incident_enforces_camera_scope_owner_revision_and_handoff(self):
        token = "a" * 64
        config = {"site": "Gate pilot", "operators": {
            "alex": {"name": "Alex", "role": "operator", "cameras": ["dock"], "tokenSha256": sha256(token.encode()).hexdigest()},
            "sam": {"name": "Sam", "role": "supervisor", "cameras": "*"},
            "camera-viewer": {"name": "Viewer", "role": "viewer", "cameras": ["other"]},
        }}
        service = SiteCollaboration(self.activity, config, "proxy-key")
        alex = service.authenticate({"Authorization": "Bearer " + token})
        sam = service.authenticate({"Authorization": "Bearer proxy-key", "X-Proxigo-Identity": "sam"})
        self.assertIsNone(service.authenticate({"X-Proxigo-Identity": "sam"}))
        base = {"cameraId": "dock", "incidentId": "alarm", "priority": "High"}
        claim = service.incident(alex, {**base, "revision": 0, "action": {"kind": "claim", "name": "Imposter"}})
        self.assertEqual(claim["action"]["name"], "Alex")
        with self.assertRaises(Conflict):
            service.incident(sam, {**base, "revision": 0, "action": {"kind": "claim"}})
        with self.assertRaises(PermissionError):
            service.incident(service.accounts.by_subject("camera-viewer"), {**base, "revision": 1, "action": {"kind": "acknowledge"}})
        handoff = {"kind": "handoff", "nextAction": "Check west gate", "dueAt": datetime.now(timezone.utc).isoformat()}
        service.incident(alex, {**base, "revision": 1, "action": handoff})
        service.enqueue_overdue()
        service.enqueue_overdue()
        notice = self.activity.next_notification()
        self.assertEqual(json.loads(notice[1])["type"], "incident-handoff-overdue")
        self.activity.sent(notice[0])
        self.assertIsNone(self.activity.next_notification())
        service.incident(sam, {**base, "revision": 2, "action": {"kind": "accept-handoff"}})
        self.assertEqual(service.page(alex, 0)["events"][-1]["actor"], "Sam")
        self.assertEqual(service.page(service.accounts.by_subject("camera-viewer"), 0)["events"], [])
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        window = {"sensorId": "dock", "mode": "schedule", "startsAt": start.isoformat(),
                  "endsAt": (start + timedelta(hours=1)).isoformat(), "reason": "Maintenance test"}
        service.maintenance(sam, window)
        with self.assertRaises(Conflict):
            service.maintenance(sam, window)
        service.maintenance(sam, {**window, "mode": "stop"})
