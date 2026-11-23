from pathlib import Path
from hashlib import sha256
import hmac
from ipaddress import ip_network
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import json

from gateway import CameraGateway
from site_activity import SiteActivity


class SiteActivityTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.activity = SiteActivity(self.directory / "activity.sqlite3")

    def take_notification(self):
        sequence, payload = self.activity.next_notification()
        self.activity.sent(sequence)
        return json.loads(payload)

    def test_video_outage_groups_cameras_and_waits_for_confirmed_recovery(self):
        self.activity.group_outage({"one"}, set())
        started = self.take_notification()
        self.assertEqual(started["state"], "started")

        self.activity.group_outage({"one", "two"}, set())
        updated = self.take_notification()
        self.assertEqual(updated["incidentId"], started["incidentId"])
        self.assertEqual(updated["deviceIds"], ["one", "two"])

        self.activity.group_outage(set(), {"one"})
        self.assertIsNone(self.activity.next_notification())
        self.activity.group_outage(set(), {"one", "two"})
        recovered = self.take_notification()
        self.assertEqual(recovered["incidentId"], started["incidentId"])
        self.assertEqual(recovered["state"], "recovered")

    def test_viewer_link_expires_and_only_returns_its_own_camera(self):
        with patch("site_activity.time.time", return_value=1000):
            issued = self.activity.issue_viewer("camera-key-a", 1)
            token = issued["streamPath"].split("/")[1]
            self.assertEqual(self.activity.viewer(token), "camera-key-a")
            self.assertIsNone(self.activity.viewer("camera-key-b"))
        with patch("site_activity.time.time", return_value=1060):
            self.assertIsNone(self.activity.viewer(token))

    def test_notification_delivery_signs_and_drains_the_durable_outbox(self):
        gateway = CameraGateway(self.directory, "test-ffmpeg", [ip_network("127.0.0.1/32")], 1)
        gateway.cameras = {"fixture-camera": {"key": "fixture-key"}}
        gateway.video_checks = {"fixture-key": {"state": "unavailable", "checkedAt": 1_000_000}}
        gateway.stopping = Mock()
        gateway.stopping.wait.side_effect = [False, True]
        destination = "https://fixture.example/delivery"
        secret = b"test-webhook-key"
        with patch("gateway.build_opener") as open_handler, patch("gateway.time.time", return_value=1000):
            delivery = open_handler.return_value.open
            gateway.notifications(destination, secret)
            request = delivery.call_args.args[0]
        self.assertEqual(request.full_url, destination)
        self.assertEqual(request.get_header("X-proxigo-signature"), hmac.new(secret, request.data, sha256).hexdigest())
        self.assertEqual(json.loads(request.data)["deviceIds"], ["fixture-camera"])
        self.assertIsNone(gateway.activity.next_notification())
