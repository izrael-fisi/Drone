from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from analytics_contract import effective_rule, policy_zone, worker_tokens
from onvif import CameraError
from site_activity import SiteActivity
from site_monitoring import SiteMonitoring
from site_telemetry import SiteTelemetry
from site_configuration import normalize_configuration
from site_collaboration import Conflict
from video_analytics.tracking import Analytics, Detection


def rule(identifier="yard-presence", kind="zone-entry", label="person", mode="alert"):
    return {"id": identifier, "name": "Yard test", "kind": kind, "object": label, "boundaryId": "yard",
            "points": [[.5, .1], [.5, .9]] if kind == "line-crossing" else [[0, 0], [1, 0], [1, 1], [0, 1]],
            "direction": "either", "minimumScore": .35, "seconds": .5, "minimumPixels": 12, "mode": mode}


def box(center, label="person", score=.9):
    return Detection(label, score, (center - .025, .3, center + .025, .5), "fixture-model")


def scheduled(base):
    return {**base, "schedule": {"dayStartsAt": "06:00", "nightStartsAt": "18:00",
                                 "night": {"minimumScore": .95, "seconds": .75, "minimumPixels": 12}}}


class TrackingTests(unittest.TestCase):
    def test_finite_directional_line_and_single_track_incident(self):
        engine = Analytics(1.4)
        wire = {**rule(kind="line-crossing"), "direction": "positive"}
        policy = {"version": 1, "rules": [wire]}
        detections = [.65, .615, .58, .545, .51, .475, .44, .475, .51, .545, .58]
        matches = [event for i, center in enumerate(detections) for event in engine.sample([box(center)], policy, i * .3, (640, 360))]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][1]["policyVersion"], 1)
        wrong_direction = Analytics(1.4)
        self.assertFalse([event for i, center in enumerate(reversed(detections[:7]))
                          for event in wrong_direction.sample([box(center)], policy, i * .3, (640, 360))])
        outside = Analytics(1.4)
        policy["rules"] = [{**wire, "points": [[.5, .62], [.5, .9]]}]
        self.assertFalse([event for i, center in enumerate(detections)
                          for event in outside.sample([box(center)], policy, i * .3, (640, 360))])

    def test_dwell_resets_on_dropout_and_new_version(self):
        engine = Analytics(1.2)
        policy = {"version": 1, "rules": [rule(kind="zone-dwell")]}
        self.assertFalse(engine.sample([box(.2)], policy, 1, (640, 360)))
        self.assertFalse(engine.sample([], policy, 1.25, (640, 360)))
        self.assertFalse(engine.sample([box(.2)], policy, 1.6, (640, 360)))
        self.assertFalse(engine.sample([box(.2)], policy, 1.9, (640, 360)))
        self.assertEqual(len(engine.sample([box(.2)], policy, 2.15, (640, 360))), 1)
        self.assertFalse(engine.sample([box(.2)], {**policy, "version": 2}, 2.45, (640, 360)))

    def test_vehicle_drift_and_drone_pixel_filter(self):
        engine = Analytics(1.2)
        stop = {"version": 1, "rules": [rule("stop", "restricted-stop", "vehicle")]}
        self.assertFalse([event for i, x in enumerate([.2, .23, .26, .29]) for event in engine.sample([box(x, "vehicle")], stop, i * .25, (640, 360))])
        self.assertFalse(engine.sample([box(.29, "vehicle")], stop, 1., (640, 360)))
        self.assertEqual(len(engine.sample([box(.29, "vehicle")], stop, 1.4, (640, 360))), 1)
        drone = {"version": 2, "rules": [rule("sky", "drone-sighting", "drone")]}
        tiny = Detection("drone", .9, (.5, .5, .505, .505), "fixture")
        self.assertFalse([event for i in range(4) for event in engine.sample([tiny], drone, 2 + i * .25, (640, 360))])
        self.assertFalse(engine.sample([box(.52, "drone")], drone, 3., (640, 360)))
        self.assertEqual(len(engine.sample([box(.52, "drone")], drone, 3.6, (640, 360))), 1)

    def test_exclusions_reset_confirmation_and_ignore_thin_mask_crossings(self):
        def sample(rule, centers):
            engine = Analytics(1.4)
            return [item for i, center in enumerate(centers)
                    for _, item in engine.sample([box(center)], {"version": 1, "rules": [rule]}, i * .3, (640, 360))]

        dwell = {**rule(kind="zone-dwell"), "exclusions": [[[.2, .4], [.3, .4], [.3, .6], [.2, .6]]]}
        self.assertFalse(sample(dwell, [.31, .31, .3, .31, .31]))
        self.assertEqual(len(sample(dwell, [.31, .31, .3, .31, .31, .31])), 1)
        wire = rule(kind="line-crossing")
        centers = [.65, .615, .58, .545, .515, .475, .44]
        self.assertEqual(sample(wire, centers)[0]["crossing"], [.5, .5])
        self.assertFalse(sample({**wire, "exclusions": [[[.49, .4], [.51, .4], [.51, .6], [.49, .6]]]}, centers))

    def test_profiles_follow_site_clock_and_reset_confirmation(self):
        configured = scheduled(rule(kind="zone-dwell"))
        zone = policy_zone("America/Los_Angeles")

        def at(stamp):
            return effective_rule(configured, datetime.fromisoformat(stamp).timestamp(), zone)

        self.assertEqual(at("2026-11-01T01:30:00-07:00")["profile"], "night")
        self.assertEqual(at("2026-11-01T01:30:00-08:00")["profile"], "night")
        self.assertEqual(at("2026-11-01T06:00:00-08:00")["profile"], "day")
        self.assertEqual(at("2026-11-01T18:00:00-08:00")["minimumScore"], .95)
        configured["schedule"].update(dayStartsAt="18:00", nightStartsAt="06:00")
        self.assertEqual(at("2026-11-01T00:00:00-07:00")["profile"], "day")
        self.assertEqual(at("2026-11-01T06:00:00-08:00")["profile"], "night")

        engine = Analytics(1.4)
        for elapsed in (0, .3):
            self.assertFalse(engine.sample([box(.31, score=.98)], {"version": 1, "rules": [at("2026-11-01T05:59:59-08:00")]}, elapsed, (640, 360)))
        night = {"version": 1, "rules": [at("2026-11-01T06:00:00-08:00")]}
        for elapsed in (.6, .9, 1.2):
            self.assertFalse(engine.sample([box(.31, score=.98)], night, elapsed, (640, 360)))
        self.assertEqual(engine.sample([box(.31, score=.98)], night, 1.5, (640, 360))[0][1]["profile"], "night")


class GatewayAnalyticsTests(unittest.TestCase):
    def setUp(self):
        data = TemporaryDirectory()
        self.addCleanup(data.cleanup)
        activity = SiteActivity(Path(data.name) / "analytics.sqlite3")
        self.monitor = SiteMonitoring(activity, "example-org", "example-site")
        self.telemetry = SiteTelemetry(activity, monitor=self.monitor)
        self.snapshot = {"sensors": [{ "id": "camera", "name": "Camera", "modality": "vision", "position": [-122, 37],
                         "analyticsRules": [rule(), rule("sky", "drone-sighting", "drone", "observe")]}], "boundaries": [{
                         "id": key, "name": key, "kind": "Restricted", "enabled": True, "sensorIds": ["camera"],
                         "vertices": [[-122.1, 36.9], [-121.9, 36.9], [-121.9, 37.1], [-122.1, 37.1]]}
                         for key in ("yard", "neighbor")]}
        with activity.connect() as db:
            db.execute("CREATE TABLE shared_incidents(id TEXT,owner TEXT,closed INTEGER)")
            db.execute("INSERT INTO configuration_versions(version,published_at,actor,message,body) VALUES(1,?,?,?,?)",
                       (time.time() - 10, "admin", "Test", json.dumps(normalize_configuration(self.snapshot))))

    def event(self, identifier, rule_id="yard-presence", kind="zone-entry", label="person", source="live", version=1):
        return {"id": identifier, "type": "observation", "sensorId": "camera", "eventType": kind, "summary": "Worker summary",
                "occurredAt": datetime.now(timezone.utc).isoformat(), "analytics": {"policyVersion": version, "ruleId": rule_id,
                "object": label, "score": .9, "box": [.2, .3, .4, .5], "frame": [640, 360], "source": source,
                "trackId": "site-track", "model": "fixture-model"}}

    def test_alert_only_targets_configured_boundary_and_replay_is_observe(self):
        event = self.event("new")
        self.assertTrue(self.telemetry.push(event)["accepted"])
        self.assertFalse(self.telemetry.push(event)["accepted"])
        self.telemetry.push(self.event("watching", "sky", "drone-sighting", "drone"))
        self.telemetry.push(self.event("replay", source="replay"))
        view = self.monitor.view({"cameras": ["camera"]}, 0, 0, 0, 0)
        self.assertEqual([(alert["zoneId"], alert["observation"]["id"]) for alert in view["alerts"]], [("yard", "new")])
        self.assertEqual([event["observation"]["analytics"]["mode"] for event in view["siteEvents"]], ["observe", "observe", "alert"])
        self.assertIn("Person entered zone", json.loads(self.monitor.pending()[1])["summary"])
        outsider = self.monitor.view({"cameras": ["foreign"]}, 0, 0, 0, 0)
        self.assertEqual(outsider["siteEvents"], [])
        self.assertEqual(outsider["alerts"], [])

    def test_invalid_version_or_geometry_cannot_create_analytic_incidents(self):
        with self.assertRaises(CameraError):
            self.telemetry.push(self.event("stale", version=2))
        with self.assertRaises(CameraError):
            self.telemetry.push({**self.event("with-world-location"), "position": [-122, 37]})
        self.assertEqual(self.telemetry.read(0)["events"], [])
        self.snapshot["sensors"][0]["analyticsRules"][0]["points"] = [[0, 0], [1, 1], [0, 1], [1, 0]]
        with self.assertRaises(CameraError):
            normalize_configuration(self.snapshot)

    def test_analytics_status_is_independent_and_camera_scoped(self):
        self.monitor.analytics_heartbeat("camera", {"state": "running", "version": 1, "models": ["fixture-model"],
                                          "pending": 0, "fps": 5, "frameAgeSeconds": .2})
        private = self.monitor.view({"cameras": []}, 0, 0, 0, 0)
        self.assertEqual(private["analyticsWorkers"], {})
        supervisor = self.monitor.view({"cameras": "*"}, 0, 0, 0, 0)
        self.assertEqual(supervisor["analyticsWorkers"]["camera"]["fps"], 5)
        self.assertEqual(supervisor["health"], [])

    def test_default_cloud_and_historical_placement_authorization(self):
        store = self.monitor.configuration
        actor = {"subject": "officer"}
        snapshot = json.loads(json.dumps(self.snapshot))
        camera = snapshot["sensors"][0]
        snapshot["sensors"].append({**camera, "id": "new-camera", "name": "New camera"})
        snapshot["boundaries"][0]["sensorIds"].append("new-camera")
        draft = store.change(actor, {"operation": "save", "publishedVersion": 1, "draftRevision": 0, "configuration": snapshot})
        existing, added = draft["draft"]["sensors"]
        self.assertNotIn("analyticsPlacement", existing)
        self.assertEqual(added["analyticsPlacement"], "cloud")
        store.change(actor, {"operation": "publish", "publishedVersion": 1, "draftRevision": draft["draftRevision"], "message": "Add cloud camera"})
        queued = {**self.event("queued-cloud", version=2), "sensorId": "new-camera"}
        with self.monitor.activity.connect() as db:
            with self.assertRaises(PermissionError):
                self.monitor.analytics_policy(db, "new-camera")
            self.assertEqual(self.monitor.analytics_policy(db, "new-camera", "cloud")["placement"], "cloud")
        camera["analyticsPlacement"] = "on-premises"
        snapshot["sensors"][1]["analyticsPlacement"] = "on-premises"
        draft = store.change(actor, {"operation": "save", "publishedVersion": 2, "draftRevision": store.detail()["draftRevision"], "configuration": snapshot})
        store.change(actor, {"operation": "publish", "publishedVersion": 2, "draftRevision": draft["draftRevision"], "message": "Local analytics"})
        with self.monitor.activity.connect() as db:
            with self.assertRaises(PermissionError):
                self.monitor.analytics_policy(db, "new-camera", "cloud")
            self.assertEqual(self.monitor.analytics_policy(db, "new-camera", "on-premises")["version"], 3)
        self.assertTrue(self.telemetry.push(queued, analytics_placement="cloud")["accepted"])
        current = self.event("local", version=3)
        current["analytics"]["placement"] = "on-premises"
        for denied in (None, "cloud"):
            with self.assertRaises(CameraError):
                self.telemetry.push(current, analytics_placement=denied)
        self.assertTrue(self.telemetry.push(current, analytics_placement="on-premises")["accepted"])
        alerts = self.monitor.view({"cameras": "*"}, 0, 0, 0, 0)["alerts"]
        self.assertEqual({item["observation"]["id"]: item["observation"]["analytics"]["placement"] for item in alerts},
                         {"queued-cloud": "cloud", "local": "on-premises"})

    def test_worker_enrollment_requires_unique_camera_and_placement_tokens(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "workers.json"
            first, second = "1" * 64, "2" * 64
            enrolled = {"camera": {"cloud": first, "on-premises": second}}
            file.write_text(json.dumps(enrolled))
            self.assertEqual(worker_tokens(file), enrolled)
            for rejected in ({"camera": {"cloud": first}, "other-camera": {"on-premises": first}},
                             {"camera": {"local": first}}, {"camera": {"on-premises": "shared"}}):
                file.write_text(json.dumps(rejected))
                with self.assertRaises(CameraError):
                    worker_tokens(file)

    def test_review_separates_replays_and_rejects_blind_overwrites(self):
        self.telemetry.push(self.event("observe", "sky", "drone-sighting", "drone"))
        self.telemetry.push(self.event("replay", source="replay"))
        self.telemetry.push(self.event("alert"))
        reviews = self.monitor.analytics_review
        actor = {"cameras": ["camera"], "name": "Operator A", "subject": "operator-a", "role": "operator"}
        live = reviews.read(actor, {"version": ["1"]})
        sequence = live["entries"][0]["sequence"]
        self.assertEqual(live["counts"]["pending"], 1)
        self.assertEqual(reviews.read(actor, {"source": ["replay"]})["entries"][0]["event"]["observation"]["id"], "replay")
        self.assertEqual(reviews.read({**actor, "cameras": []}, {})["entries"], [])
        change = {"sequence": sequence, "revision": 0, "outcome": "false-detection", "note": "Bird silhouette"}
        for denied in ({**actor, "role": "viewer"}, {**actor, "cameras": ["other"]}):
            with self.assertRaises(PermissionError):
                reviews.change(denied, change)
        stored = reviews.change(actor, change)
        self.assertEqual(stored["revision"], 1)
        with self.assertRaises(Conflict):
            reviews.change(actor, {**change, "outcome": "confirmed"})
        pending = reviews.read(actor, {})
        self.assertEqual(pending["entries"], [])
        self.assertEqual(pending["counts"]["false-detection"], 1)
        self.assertEqual(reviews.read(actor, {"status": ["false-detection"]})["entries"][0]["review"], stored)
        self.assertEqual(reviews.read(actor, {"version": ["2"]})["counts"]["false-detection"], 0)
        with self.monitor.activity.connect() as db:
            recorded = db.execute("SELECT actor,detail FROM activity_audit WHERE action='cctv-review'").fetchone()
            self.assertEqual(recorded[0], "operator-a")
            self.assertEqual(json.loads(recorded[1])["note"], "Bird silhouette")

    def test_review_pages_with_monotonic_event_cursors(self):
        for index in range(26):
            self.telemetry.push(self.event(f"observe-{index}", "sky", "drone-sighting", "drone"))
        inbox = self.monitor.analytics_review
        actor = {"cameras": "*"}
        page = inbox.read(actor, {})
        self.assertEqual(len(page["entries"]), 25)
        older = inbox.read(actor, {"before": [str(page["before"])]})
        self.assertEqual([entry["event"]["observation"]["id"] for entry in older["entries"]], ["observe-0"])
        self.assertIsNone(older["before"])

    def test_report_uses_historical_profile_scope_and_observe_source(self):
        yard = scheduled({**rule(mode="observe"), "exclusions": [[[.25, .4], [.35, .4], [.35, .6], [.25, .6]]]})
        self.snapshot["sensors"][0]["analyticsRules"] = [yard]
        snapshot = {**normalize_configuration(self.snapshot), "analyticsTimeZone": "America/Los_Angeles"}
        stamp = datetime(2025, 1, 1, 14, 0, tzinfo=timezone.utc)
        night = stamp.replace(hour=10)
        with self.monitor.activity.connect() as db:
            db.execute("UPDATE configuration_versions SET published_at=?,body=? WHERE version=1",
                       (night.timestamp() - 1, json.dumps(snapshot)))
            self.assertEqual(self.monitor.analytics_policy(db, "camera")["timeZone"], "America/Los_Angeles")

        def candidate(identifier, profile="day", source="live", box_x=(.4, .6), when=stamp, score=.9):
            event = self.event(identifier, source=source)
            event["occurredAt"] = when.isoformat()
            event["analytics"].update(profile=profile, box=[box_x[0], .3, box_x[1], .5], score=score)
            return event

        for rejected in (candidate("masked", box_x=(.2, .4)), candidate("wrong-profile", profile="night"),
                         candidate("below-night-threshold", profile="night", when=night)):
            with self.assertRaises(CameraError):
                self.telemetry.push(rejected)
        self.telemetry.push(candidate("day"))
        self.telemetry.push(candidate("night", profile="night", when=night, score=.99))
        self.telemetry.push(candidate("replay", source="replay"))
        reviews = self.monitor.analytics_review
        actor = {"cameras": ["camera"], "role": "operator", "subject": "officer", "name": "Officer"}
        entry = next(entry for entry in reviews.read(actor, {})["entries"] if entry["event"]["observation"]["id"] == "day")
        reviews.change(actor, {"sequence": entry["sequence"], "revision": 0, "outcome": "confirmed", "note": ""})
        live_day, live_night = reviews.report(actor, {})["rules"][0]["profiles"]
        self.assertEqual((live_day["profile"], live_day["counts"]["confirmed"], live_day["latencySamples"]), ("day", 1, 1))
        self.assertGreater(live_day["meanGatewaySeconds"], 0)
        self.assertEqual((live_night["profile"], live_night["counts"]["pending"]), ("night", 1))
        replay_day = reviews.report(actor, {"source": ["replay"], "version": ["1"]})["rules"][0]["profiles"][0]
        self.assertEqual((replay_day["counts"]["pending"], replay_day["latencySamples"]), (1, 0))
        self.assertIsNone(replay_day["meanGatewaySeconds"])
        self.assertEqual(reviews.report({**actor, "cameras": ["other"]}, {})["rules"], [])
