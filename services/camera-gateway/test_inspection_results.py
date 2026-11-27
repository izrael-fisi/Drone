from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest import TestCase
from unittest.mock import patch

from onvif import CameraError
from site_activity import SiteActivity
from site_monitoring import SiteMonitoring
from site_telemetry import SiteTelemetry


class InspectionResultsTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.activity = SiteActivity(Path(directory.name) / "site.sqlite3")
        self.monitor = SiteMonitoring(self.activity, "factory", "test-site")
        self.telemetry = SiteTelemetry(self.activity, upstream="https://unused.example.test", monitor=self.monitor)
        self.config = {"sensors": [
            {"id": "inspection-camera", "name": "Station CCTV", "modality": "vision", "position": [3.2, 2.2],
             "inspectionStationId": "assembly-line-2"},
            {"id": "other-camera", "name": "Other camera", "modality": "vision", "position": [3.2, 2.2]},
        ], "boundaries": [{"id": "assembly", "name": "Assembly", "enabled": True, "kind": "Restricted",
                           "sensorIds": ["inspection-camera"], "vertices": [[3, 2], [4, 2], [4, 3], [3, 3]]}]}
        self.operator = {"cameras": ["inspection-camera"]}

    def publish(self):
        configuration = self.monitor.configuration
        actor = {"subject": "test"}
        current = configuration.detail()
        revision = {"publishedVersion": current["publishedVersion"], "draftRevision": current["draftRevision"]}
        draft = configuration.change(actor, {**revision, "operation": "save", "configuration": self.config})
        configuration.change(actor, {**revision, "draftRevision": draft["draftRevision"], "operation": "publish", "message": "Inspection pilot"})

    def result(self, identifier="station-result", outcome="fail"):
        return {"type": "observation", "sensorId": "inspection-camera", "id": identifier,
                "occurredAt": datetime.now(timezone.utc).isoformat(), "eventType": "inspection-result",
                "inspection": {"stationId": "assembly-line-2", "source": "simulated", "outcome": outcome,
                               "recipeId": "bracket-v3", "componentId": "retaining-clip", "partId": "frame-002", "lotId": "lot-a"}}

    def test_inspection_results_are_durable_camera_scoped_and_observe_only(self):
        self.publish()
        failed, passed = self.result(), self.result("pass-result", "pass")
        accepted = self.telemetry.push(failed)
        self.assertTrue(accepted["accepted"])
        self.assertTrue(self.telemetry.push(passed)["accepted"])
        restarted = SiteMonitoring(self.activity, "factory", "test-site")
        retried = SiteTelemetry(self.activity, monitor=restarted).push(failed)
        self.assertEqual(retried, {**accepted, "accepted": False})
        [result] = restarted.inspections.read(self.operator, {"outcome": ["fail"]})["entries"]
        self.assertEqual(result["event"]["configurationVersion"], 1)
        self.assertEqual(result["event"]["boundaries"], [{"id": "assembly", "name": "Assembly"}])
        self.assertEqual(result["event"]["observation"]["inspection"], failed["inspection"])
        self.assertEqual(restarted.inspections.read({"cameras": ["other-camera"]}, {"camera": ["inspection-camera"]})["entries"], [])
        self.assertEqual(self.telemetry.read(0)["events"], [])
        self.assertIsNone(self.monitor.pending())
        self.assertIsNone(self.monitor.next_recovery())

    def test_only_complete_results_for_the_published_station_are_accepted(self):
        with self.assertRaises(CameraError):
            self.telemetry.push(self.result())
        self.publish()
        valid = self.result()
        with self.assertRaises(CameraError):
            self.telemetry.push(valid, local=False)
        changed = lambda **values: {**valid, "inspection": {**valid["inspection"], **values}}
        for invalid in ({**valid, "sensorId": "other-camera"}, {**valid, "position": [3, 2]},
                        changed(stationId="another-station"), changed(outcome="incomplete"), changed(source="device"),
                        changed(partId=None), changed(recordingUrl="https://untrusted.example.test/binary")):
            with self.subTest(result=invalid), self.assertRaises(CameraError):
                self.telemetry.push(invalid)
        self.assertEqual(self.monitor.inspections.read(self.operator, {})["entries"], [])

    def test_release_version_and_cursor_are_stable_after_new_results(self):
        now = time.time()
        with patch("time.time", return_value=now - 10):
            self.publish()
        historical = {**self.result(), "occurredAt": datetime.fromtimestamp(now - 5, timezone.utc).isoformat()}
        self.config["sensors"][0]["inspectionStationId"] = "replacement-station"
        self.publish()
        self.assertTrue(self.telemetry.push(historical)["accepted"])
        with self.assertRaises(CameraError):
            self.telemetry.push(self.result("wrong-station"))
        for index in range(29):
            result = self.result(f"result-{index}")
            result["inspection"]["stationId"] = "replacement-station"
            self.telemetry.push(result)
        first = self.monitor.inspections.read(self.operator, {})
        self.assertEqual(len(first["entries"]), 25)
        second = self.monitor.inspections.read(self.operator, {"before": [str(first["before"])]})
        self.assertEqual(len(second["entries"]), 5)
        self.assertEqual(second["entries"][-1]["event"]["configurationVersion"], 1)
        self.assertIsNone(second["before"])

    def test_opc_results_deduplicate_by_station_and_export_under_camera_scope(self):
        self.publish()
        result = self.result("arbitrary-source-id", "incomplete")
        result["inspection"].update(source="opc-ua", simulated=True, resultId="machine/123", recipeId="-fixture")
        first = self.telemetry.push(result)
        self.assertTrue(first["accepted"])
        self.assertEqual(self.telemetry.push({**result, "id": "different-client-id"}), {**first, "accepted": False})
        filters = {"outcome": ["incomplete"], "lot": ["lot-a"], "recipe": ["-fixture"]}
        [value] = self.monitor.inspections.read(self.operator, filters)["entries"]
        self.assertEqual(value["event"]["observation"]["inspection"]["resultId"], "machine/123")
        csv = self.monitor.inspections.read(self.operator, {**filters, "export": ["csv"]})
        self.assertEqual(csv["count"], 1)
        self.assertIn("machine/123,'-fixture", csv["csv"])
        outsider = self.monitor.inspections.read({"cameras": ["other-camera"]}, {"export": ["csv"]})
        self.assertEqual(outsider["count"], 0)
        self.assertNotIn("machine/123", outsider["csv"])
