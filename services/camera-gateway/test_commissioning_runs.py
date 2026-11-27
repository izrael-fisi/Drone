import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest import TestCase
from unittest.mock import patch

from commissioning_runs import CommissioningRuns, compare_labels
from gateway import CameraGateway
from onvif import CameraError
from site_collaboration import Conflict


class CommissioningTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.gw = CameraGateway(Path(directory.name), "ffmpeg", (), 1, site_config={
            "site": "Commissioning test", "operators": {"site-admin": {"name": "Admin", "role": "org-admin",
                "cameras": "*", "tokenSha256": "a" * 64}}})
        self.operator = {"subject": "site-admin", "name": "Admin", "role": "org-admin", "cameras": "*"}
        self.viewer = {"subject": "tech", "role": "viewer", "cameras": ["camera"]}
        self.service = self.gw.commissioning
        self.service.worker = ("python", {"sha256": "c" * 64, "provider": "CPUExecutionProvider"})
        self.gw.cameras["camera"] = {"key": "a" * 48, "url": "rtsp://127.0.0.1/test"}
        rule = {"kind": "line-crossing", "id": "entry", "direction": "positive", "name": "Entry", "object": "person",
                "boundaryId": "gate", "mode": "observe", "minimumScore": .25, "minimumPixels": 12, "seconds": .5,
                "points": [[.5, .1], [.5, .9]]}
        self.config = {"sensors": [{"id": "camera", "name": "Gate camera", "modality": "vision", "position": [0, 0], "analyticsRules": [rule]}],
                       "boundaries": [{"id": "gate", "name": "Gate", "kind": "Access", "enabled": True, "sensorIds": ["camera"],
                                       "vertices": [[0, 0], [.01, 0], [.01, .01], [0, .01]]}]}
        self.configure("save", configuration=self.config)
        self.configure("publish", message="Publish line")
        rule["minimumScore"] = .8
        self.draft = self.configure("save", configuration=self.config)["draftRevision"]
        self.clip_id = "b" * 32
        archive = self.gw.archive.directory
        archive.mkdir()
        (archive / (self.clip_id + ".mp4")).write_bytes(b"fixture media")
        self.catalog = archive / (self.clip_id + ".json")
        self.catalog.write_text(json.dumps({"cameraKey": "a" * 48, "occurredAt": 1_700_000_000_000, "clipId": self.clip_id,
                                            "durationSeconds": 8, "eventOffsetSeconds": 4, "expiresAt": time.time() + 100}))

    def configure(self, operation, **fields):
        store = self.gw.monitor.configuration
        state = store.detail()
        return store.change(self.operator, {"operation": operation, "publishedVersion": state["publishedVersion"],
                                             "draftRevision": state["draftRevision"], **fields})

    def create(self):
        return self.service.change(self.operator, {"operation": "create", "cameraId": "camera", "ruleId": "entry",
                                                   "clipId": self.clip_id, "draftRevision": self.draft})

    def test_one_to_one_maximizes_matches_and_minimizes_time_error(self):
        self.assertEqual(compare_labels([1000, 2000, 6000], [150, 1400, 4000])["matches"],
                         [{"expectedMs": 1000, "detectedMs": 150}, {"expectedMs": 2000, "detectedMs": 1400}])
        self.assertEqual(compare_labels([1000], [200, 1100])["extras"], [200])
        self.assertEqual(compare_labels([], [100])["extras"], [100])

    def test_replay_recovers_interrupted_work_and_keeps_scope_and_snapshots(self):
        run = self.create()
        self.assertEqual([policy["rules"][0]["minimumScore"] for policy in run["policies"]], [.25, .8])
        annotation = {"operation": "annotate", "id": run["id"], "revision": 0, "startMs": 0, "endMs": 8000, "expectedMs": []}
        with self.assertRaises(PermissionError):
            self.service.change(self.viewer, annotation)
        value = self.service.change(self.operator, annotation)
        with self.assertRaises(Conflict):
            self.service.change(self.operator, annotation)
        self.service.change(self.operator, {"operation": "queue", "id": run["id"], "revision": value["revision"]})
        with self.gw.activity.connect() as db:
            db.execute("UPDATE commissioning_runs SET state='running'")
        restarted = CommissioningRuns(self.gw)
        self.assertEqual(restarted.read(self.viewer, {})["runs"][0]["state"], "queued")
        with patch.object(restarted, "replay", return_value={"published": compare_labels([], [])}) as replay:
            self.assertTrue(restarted.work_once())
        self.assertEqual(replay.call_args.args[0]["annotations"]["expectedMs"], [])
        self.assertEqual(restarted.read(self.viewer, {})["runs"][0]["state"], "completed")
        self.assertEqual(restarted.read({**self.viewer, "cameras": ["outside"]}, {"id": [run["id"]]})["runs"], [])

    def test_expired_clip_is_rejected_and_replay_preserves_annotations(self):
        run = self.create()
        value = self.service.change(self.operator, {"operation": "annotate", "id": run["id"], "revision": 0,
                                                    "startMs": 0, "endMs": 1200, "expectedMs": [500]})
        self.service.change(self.operator, {"operation": "queue", "id": run["id"], "revision": value["revision"]})
        self.catalog.unlink()
        self.assertTrue(self.service.work_once())
        saved = self.service.read(self.viewer, {})["runs"][0]
        self.assertIn("expired", saved["error"])
        self.assertEqual(saved["annotations"]["expectedMs"], [500])
        with self.assertRaises(CameraError):
            self.create()
