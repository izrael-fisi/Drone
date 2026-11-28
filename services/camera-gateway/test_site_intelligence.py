"""Contracts for site-local draft, degradation, and corroboration workflows."""

from contextlib import contextmanager
from datetime import datetime, timezone
from ipaddress import ip_network
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from ai_jobs import AIJobs, clip_result, prepare_video, review_frames, sample_frames, video_result
from automatic_triage import AutomaticTriage, SETTLE_SECONDS
from axis_archive import utc
from device_degradation import BUCKET, DAY, DeviceDegradation, regime
from gateway import CameraGateway
from native_camera_events import WSN
from onvif import CameraError, SCHEMA, add
from sensor_fusion import SensorFusion
from site_collaboration import Conflict
from site_monitoring import SiteMonitoring
from test_enterprise_operations import AREA, CAMERA, DOOR, OPERATOR, OUTSIDER, PROCEDURE, SUPERVISOR

RADAR = {"id": "radar", "name": "North radar", "modality": "rf", "position": [0, 0]}
GATE = {"sensorId": "radar", "cameraId": "cam", "doorId": "door", "boundaryId": "north",
        "address": "http://127.0.0.1/onvif/device_service", "axisProfile": "Channel0Profile1", "doorClockVerified": True}


class ModelFixture:
    name, model = "on-premises", "fixture-v1"

    def __init__(self):
        self.calls = []

    def generate(self, instructions, text, frames, _schema):
        self.calls.append((text, len(frames)))
        if frames:
            event = next(frame["reference"]["ref"] for frame in frames if frame["reference"]["eventFrame"])
            return {"outcome": "observed", "explanation": "Fixture observation.", "evidenceRefs": [event]}, {}
        return {"summary": [{"text": "A forced-door event was recorded.", "evidenceRefs": ["incident"]}],
                "nextAction": {"text": "Review evidence.", "evidenceRefs": ["current-step"]}}, {"inputTokens": 100, "outputTokens": 16}


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        roster = {entry["subject"]: entry for entry in (SUPERVISOR, OPERATOR, OUTSIDER)}
        self.gateway = CameraGateway(self.state, "ffmpeg", [ip_network("127.0.0.0/8")], 12, site_config={"site": "North", "operators": roster})
        self.collaboration = self.gateway.collaboration
        self.fixture = ModelFixture()
        self.service = self.collaboration.ai_jobs = AIJobs(self.gateway, {"provider": "on-premises", "handoff": True,
                               "clipReview": True, "clipBoundaries": ["north"], "dailyQuota": 3}, self.fixture)
        self.monitor = self.gateway.monitor
        self.actor = self.collaboration.accounts.by_subject(OPERATOR["subject"])
        self.outsider = self.collaboration.accounts.by_subject(OUTSIDER["subject"])
        self.admin = self.collaboration.accounts.by_subject(SUPERVISOR["subject"])
        site = {"sensors": [CAMERA, DOOR, RADAR], "boundaries": [{**AREA, "sensorIds": ["cam", "door", "radar"]}], "procedures": [PROCEDURE]}
        self.configure("save", configuration=site)
        self.configure("publish", message="Intelligence fixture")

    def configure(self, operation, **fields):
        return self.monitor.configuration.change(self.admin, {**self.monitor.configuration.detail(), "operation": operation, **fields})

    def push(self, event_id, when, event="door-forced-open", sensor="door", **private):
        return self.gateway.telemetry.push({"type": "observation", "id": event_id, "sensorId": sensor, "eventType": event,
                                            "occurredAt": datetime.fromtimestamp(when, timezone.utc).isoformat(), "summary": "Fixture " + event}, **private)

    def incident(self):
        return self.monitor.dispatch(self.actor)["alerts"][0]["id"]

    def action(self, incident, revision, kind, **fields):
        return self.collaboration.incident(self.actor, {"incidentId": incident, "cameraId": "cam", "revision": revision,
                                                       "action": {"kind": kind, **fields}})

    def draft(self):
        self.push("forced", time.time())
        incident = self.incident()
        self.action(incident, 0, "claim")
        return incident, {"type": "handoff", "incidentId": incident}

    def run_queued(self):
        self.service.work(self.service.take())

    def test_handoff_cites_selected_sources_and_audits_only_approved_text(self):
        incident, request = self.draft()
        chosen = self.action(incident, 1, "note", text="Selected note")
        self.action(incident, 2, "note", text="Private note")
        request["noteSequences"] = [chosen["sequence"]]
        submitted = self.service.create(self.actor, request)
        self.assertEqual(self.service.create(self.actor, request), submitted)
        with self.assertRaises(PermissionError):
            self.service.read(self.outsider, submitted["id"])
        self.run_queued()
        result = self.service.read(self.actor, submitted["id"])
        self.assertEqual(result["state"], "ready")
        self.assertEqual({item["ref"] for item in result["sources"]}, {"incident", "current-step"})
        self.assertIn("Selected note", self.fixture.calls[0][0])
        self.assertNotIn("Private note", self.fixture.calls[0][0])
        with self.assertRaises(Conflict):
            self.action(incident, 2, "handoff", nextAction="Stale next action", aiJobId=result["id"])
        saved = self.action(incident, 3, "handoff", nextAction="Approved work", note="Reviewed text", aiJobId=result["id"])
        self.assertEqual(saved["action"]["note"], "Reviewed text")
        self.assertEqual(saved["action"]["aiDraft"]["model"], "fixture-v1")
        self.assertEqual(self.service.read(self.actor, result["id"])["state"], "approved")
        with self.assertRaises(Conflict):
            self.action(incident, 4, "handoff", nextAction="Replay", aiJobId=result["id"])

    def test_handoff_includes_new_boundary_timeline_evidence_in_its_own_scope(self):
        incident, request = self.draft()
        before = self.service.create(self.actor, request)
        self.run_queued()
        self.push("linked-crossing", time.time(), "line-crossing", "cam")
        after = self.service.create(self.actor, request)
        self.assertNotEqual(before["id"], after["id"])
        self.run_queued()
        self.assertIn("linked-crossing", self.fixture.calls[-1][0])
        self.assertEqual(self.service.read(self.actor, after["id"])["state"], "ready")

    def test_automatic_triage_summarizes_scoped_new_incidents_and_shares_the_site_quota(self):
        automatic = self.service.automatic
        when = time.time()
        self.push("before-enable", when)
        previous = self.incident()
        self.assertFalse(automatic.read(self.admin)["enabled"])
        automatic.run_one(when + SETTLE_SECONDS + 2)
        self.assertEqual(self.fixture.calls, [])
        with self.assertRaises(PermissionError):
            automatic.change(self.actor, {"enabled": True, "revision": 0})
        automatic.change(self.admin, {"enabled": True, "revision": 0})
        with self.assertRaises(Conflict):
            automatic.change(self.admin, {"enabled": False, "revision": 0})
        self.assertIsNone(automatic.read(self.actor, previous)["review"])
        automatic.limit = 1
        when = time.time()
        self.push("new-forced-door", when)
        incident = self.incident()
        offset = when + 1
        self.push("camera-evidence", offset, "motion", "cam")
        self.assertEqual(automatic.read(self.actor, incident)["review"]["state"], "collecting")
        automatic.run_one(when + SETTLE_SECONDS - 1)
        self.assertEqual(self.fixture.calls, [])

        def generate(_, content, frames, schema):
            self.assertEqual(frames, [])
            rows = json.loads(content)
            self.assertEqual([row["eventId"] for row in rows], ["before-enable", "new-forced-door", "camera-evidence"])
            reference = rows[-1]["ref"]
            self.assertEqual(set(schema["properties"]), {"keyEvents"})
            return {"keyEvents": [reference]}, {}

        with patch.object(self.fixture, "generate", side_effect=generate) as model:
            automatic.run_one(when + SETTLE_SECONDS + 2)
            automatic.run_one(when + SETTLE_SECONDS + 3)
            self.assertEqual(model.call_count, 1)
        report = automatic.read(self.actor, incident)["review"]
        self.assertEqual(report["state"], "ready")
        self.assertEqual([event["eventId"] for event in report["events"]], ["new-forced-door", "camera-evidence"])
        self.assertEqual([fact["text"] for fact in report["summary"]], ["Forced-open door reported", "Camera motion reported"])
        self.assertEqual(report["events"][-1]["occurredAt"], datetime.fromtimestamp(offset, timezone.utc).isoformat())
        with self.assertRaises(PermissionError):
            automatic.read(self.outsider, incident)

        later = time.time() + .002
        self.push("over-quota", later)
        other = self.incident()
        automatic.run_one(later + SETTLE_SECONDS + 5)
        self.assertEqual(automatic.read(self.admin, other)["review"]["reason"], "limit")
        automatic.change(self.admin, {"enabled": False, "revision": 1})
        self.assertEqual(automatic.read(self.actor, incident)["review"]["state"], "ready")

    def test_automatic_triage_preserves_timestamps_during_provider_errors_and_restart(self):
        automatic = self.service.automatic
        automatic.change(self.admin, {"enabled": True, "revision": 0})
        now = time.time()
        self.push("bad-output", now)
        incident = self.incident()
        with patch.object(self.fixture, "generate", return_value=({"keyEvents": ["unrecognized"]}, {})):
            automatic.run_one(now + SETTLE_SECONDS + 2)
        fallback = automatic.read(self.actor, incident)["review"]
        self.assertEqual((fallback["state"], fallback["reason"]), ("timeline", "provider"))
        self.assertEqual(fallback["events"][0]["eventId"], "bad-output")
        now = time.time()
        self.push("restart-job", now)
        incident = self.incident()
        self.assertIsNotNone(automatic.take(now + SETTLE_SECONDS + 2))
        recovered = AutomaticTriage(self.service, 2, 3)
        self.assertTrue(recovered.read(self.admin)["enabled"])
        self.assertEqual(recovered.read(self.actor, incident)["review"]["reason"], "restart")
        with patch.object(self.fixture, "generate") as model:
            recovered.run_one(now + SETTLE_SECONDS + 4)
            model.assert_not_called()

    def test_automatic_triage_ignores_contradictory_prose_from_new_and_saved_reviews(self):
        automatic = self.service.automatic
        automatic.change(self.admin, {"enabled": True, "revision": 0})
        now = time.time()
        self.push("forced-door", now)
        incident = self.incident()
        contradictory = [{"text": "The door stayed closed.", "evidenceRefs": ["incident"]}]
        with patch.object(self.fixture, "generate", return_value=({"summary": contradictory, "keyEvents": ["incident"]}, {})):
            automatic.run_one(now + SETTLE_SECONDS + 1)
        fallback = automatic.read(self.actor, incident)["review"]
        self.assertEqual((fallback["state"], fallback["summary"], fallback["reason"]), ("timeline", [], "provider"))
        self.assertEqual(fallback["events"][0]["eventId"], "forced-door")
        with self.gateway.activity.connect() as db:
            db.execute("UPDATE automatic_triage_reports SET state='ready',result=?,reason=NULL WHERE incident=?",
                       (json.dumps({"events": fallback["events"], "summary": contradictory}), incident))
        legacy = automatic.read(self.actor, incident)["review"]
        self.assertEqual(legacy["summary"], [{"text": "Forced-open door reported", "evidenceRefs": ["incident"]}])

    def test_automatic_triage_recovers_result_write_failure_without_another_request(self):
        automatic = self.service.automatic
        automatic.change(self.admin, {"enabled": True, "revision": 0})
        now = time.time()
        self.push("interrupted-result", now)
        incident = self.incident()
        activity = self.gateway.activity
        original = activity.connect
        fail_next_write = False

        def model(*_):
            nonlocal fail_next_write
            fail_next_write = True
            return {"keyEvents": ["incident"]}, {}

        @contextmanager
        def connect():
            nonlocal fail_next_write
            if fail_next_write:
                fail_next_write = False
                raise sqlite3.OperationalError("Fixture: database unavailable when saving a result")
            with original() as db:
                yield db

        with patch.object(self.fixture, "generate", side_effect=model) as provider, patch.object(activity, "connect", connect):
            with self.assertRaises(sqlite3.OperationalError):
                automatic.run_one(now + SETTLE_SECONDS + 2)
            self.assertEqual(automatic.read(self.actor, incident)["review"]["state"], "processing")
            automatic.run_one(now + SETTLE_SECONDS + 4)
            provider.assert_called_once()
        result = automatic.read(self.actor, incident)["review"]
        self.assertEqual((result["state"], result["reason"]), ("timeline", "interrupted"))
        self.assertEqual(result["events"][0]["eventId"], "interrupted-result")

    def test_timeline_uses_indexed_camera_and_time_range_for_existing_events(self):
        now = time.time()
        self.push("timeline-anchor", now)
        identifier = self.incident()
        activity = self.gateway.activity
        with activity.connect() as db:
            db.execute("DROP INDEX monitored_event_camera_time")
            template = json.loads(db.execute("SELECT body FROM monitored_events WHERE source_id='timeline-anchor'").fetchone()[0])
            for event_id, offset, camera in (("older", -31, "cam"), ("inside", -12, "cam"), ("upper", 30, "cam"),
                                              ("newer", 31, "cam"), ("other-camera", 1, "other")):
                event = {"id": event_id, "occurredAt": datetime.fromtimestamp(now + offset, timezone.utc).isoformat()}
                body = {**template, "cameraId": camera, "observation": {**template["observation"], **event}}
                db.execute("INSERT INTO monitored_events(sensor,source_id,camera,body) VALUES('door',?,?,?)",
                           (event_id, camera, json.dumps(body)))
        SiteMonitoring(activity, self.monitor.organization, self.monitor.site)
        with activity.connect() as db:
            root = db.execute("SELECT body FROM monitored_alerts WHERE id=?", (identifier,)).fetchone()[0]
            calls = []
            db.set_trace_callback(calls.append)
            sources = self.service.timeline_sources(db, identifier, root, "cam", triage=True)
            db.set_trace_callback(None)
            self.assertEqual({event["eventId"] for event in sources}, {"timeline-anchor", "inside", "upper"})
            lookup = next(sql for sql in calls if "monitored_event_camera_time" in sql)
            explain = " ".join(row[3] for row in db.execute("EXPLAIN QUERY PLAN " + lookup))
            self.assertIn("monitored_event_camera_time (camera=? AND <expr>>? AND <expr><?)", explain)

    def test_invalid_citations_and_quota_fall_back_to_template(self):
        _, request = self.draft()
        self.service.quota.read(self.admin, {"dailyLimit": 1, "operatorReserve": 1, "revision": 0})
        first = self.service.create(self.actor, request)
        with patch.object(self.fixture, "generate", return_value=({"summary": [{"text": "Unknown", "evidenceRefs": ["invented"]}]}, {})):
            self.run_queued()
        self.assertTrue(self.service.read(self.actor, first["id"])["template"])
        note = self.action(request["incidentId"], 1, "note", text="New evidence")
        second = self.service.create(self.actor, {**request, "noteSequences": [note["sequence"]]})
        self.assertEqual((second["state"], second["template"]), ("ready", True))
        self.assertIsNone(self.service.take())

    def test_revoked_job_does_not_dispatch(self):
        _, request = self.draft()
        submitted = self.service.create(self.actor, request)
        self.collaboration.accounts.change(self.admin, {"operation": "disable", "subject": self.actor["subject"]})
        self.run_queued()
        self.assertEqual(self.fixture.calls, [])
        with self.gateway.activity.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM site_ai_jobs WHERE id=?", (submitted["id"],)).fetchone()[0], "failed")

    def test_cloud_media_opt_in_and_missing_capture(self):
        with self.assertRaisesRegex(CameraError, "explicit site approval"):
            AIJobs(self.gateway, {"clipReview": True, "clipBoundaries": ["north"]}, self.fixture)
        with self.assertRaisesRegex(CameraError, "explicit site approval for video"):
            AIJobs(self.gateway, {"videoReview": True, "cloudClips": True, "clipBoundaries": ["north"]}, self.fixture)
        self.push("recording", time.time())
        request = {"type": "clip-review", "incidentId": self.incident(), "event": {"sensorId": "door", "id": "recording"}}
        with self.assertRaises(PermissionError):
            self.service.create(self.outsider, request)
        job = self.service.create(self.actor, request)
        self.run_queued()
        result = self.service.read(self.actor, job["id"])
        self.assertEqual(result["outcome"], "unable-to-verify")
        self.assertEqual(self.fixture.calls, [])

    def test_video_segments_translate_excerpt_offsets_and_validate_results(self):
        stamp = 1700000000
        recording = {"eventOffsetSeconds": 10, "durationSeconds": 22}
        segment = {"start_sec": 4.5, "end_sec": 7, "occupancy": "observed", "summary": "Person on walkway."}
        result = video_result([segment], 4, 12, stamp, recording)["segments"][0]
        self.assertEqual((result["startSeconds"], result["endSeconds"]), (8.5, 11))
        self.assertEqual((result["startAt"], result["endAt"]), (utc(stamp - 1.5), utc(stamp + 1)))
        self.assertEqual(result["outcome"], "observed")
        for invalid in ({"end_sec": 12.1}, {"start_sec": 12.01, "end_sec": 12.02}, {"start_sec": True},
                        {"occupancy": "confirmed"}):
            with self.subTest(invalid=invalid), self.assertRaises(CameraError):
                video_result([{**segment, **invalid}], 4, 12, stamp, recording)

    def test_cloud_frame_review_pins_the_frame_supplied_to_nova(self):
        provider = Mock()
        provider.name = "bedrock"
        provider.generate.return_value = {"outcome": "observed", "explanation": "A person is visible."}, {}
        earlier = {"reference": {"ref": "earlier", "eventFrame": False}}
        event = {"reference": {"ref": "event-time", "eventFrame": True}}
        result, _ = review_frames(provider, [earlier, event])
        self.assertEqual(provider.generate.call_args.args[2], [event])
        self.assertEqual(result["sources"], [event["reference"]])

    def test_site_can_enable_fallback_and_one_uncertain_frame_reserves_one_video_call(self):
        provider = Mock(name="bedrock-provider")
        provider.name, provider.model = "bedrock", "us.amazon.nova-2-lite-v1:0"
        provider.generate.return_value = {"outcome": "unable-to-verify", "explanation": "Unclear frame."}, {
            "inputTokens": 1100, "outputTokens": 20}
        provider.review_video.return_value = ([{"start_sec": .5, "end_sec": 2, "occupancy": "observed",
                                               "summary": "Person by the door."}], {"inputVideoSeconds": 4, "outputTokens": 30})
        settings = {"clipReview": True, "cloudClips": True, "videoReview": True, "cloudVideo": True,
                    "clipBoundaries": ["north"], "dailyQuota": 3}
        self.service = AIJobs(self.gateway, settings, provider)
        self.push("candidate", time.time())
        request = {"type": "clip-review", "incidentId": self.incident(), "event": {"sensorId": "door", "id": "candidate"}}
        frame = {"reference": {"ref": "event-time", "eventFrame": True}}
        recording = {"eventOffsetSeconds": 2, "durationSeconds": 4}
        self.assertFalse(self.service.review_settings(self.admin)["enabled"])
        with self.assertRaises(PermissionError):
            self.service.review_settings(self.actor, {"enabled": True, "revision": 0})
        with patch.object(self.service, "recording_frames", return_value=([frame], recording)):
            baseline = self.service.create(self.actor, request)
            self.run_queued()
            self.assertNotIn("videoFallback", self.service.read(self.actor, baseline["id"]))
        provider.review_video.assert_not_called()
        self.assertTrue(self.service.review_settings(self.admin, {"enabled": True, "revision": 0})["enabled"])
        with self.assertRaises(Conflict):
            self.service.review_settings(self.admin, {"enabled": False, "revision": 0})
        with patch.object(self.service, "recording_frames", return_value=([frame], recording)), \
                patch.object(self.service, "recording", return_value=(self.state / "private.mp4", {}, recording)), \
                patch("ai_jobs.prepare_video", return_value=(b"masked clip", 0, 4)):
            job = self.service.create(self.actor, request)
            self.run_queued()
        result = self.service.read(self.actor, job["id"])
        self.assertEqual(result["outcome"], "unable-to-verify")
        self.assertEqual(result["videoFallback"]["segments"][0]["startSeconds"], .5)
        provider.review_video.assert_called_once_with(b"masked clip")
        self.assertEqual(self.service.create(self.actor, request)["id"], job["id"])
        with self.gateway.activity.connect() as db:
            audit = json.loads(db.execute("SELECT audit FROM site_ai_jobs WHERE id=?", (job["id"],)).fetchone()[0])
            self.assertEqual(audit["inputTokens"], 1100)
            self.assertEqual(audit["videoFallback"]["inputVideoSeconds"], 4)
            self.assertIsInstance(audit["videoFallback"]["submittedAt"], float)
            self.assertEqual(self.service.quota.usage(db, time.time()), {"operator": 2, "fallback": 1, "triage": 0, "frame": 0})
        self.assertTrue(AIJobs(self.gateway, settings, provider).review_settings(self.admin)["enabled"])

    def test_video_fallback_respects_quota_and_skips_definitive_frames(self):
        for index, outcome in enumerate(("unable-to-verify", "not-observed", "observed")):
            with self.subTest(outcome=outcome):
                provider = Mock()
                provider.name, provider.model = "bedrock", "nova"
                provider.generate.return_value = {"outcome": outcome, "explanation": "Frame observation."}, {}
                self.service = AIJobs(self.gateway, {"clipReview": True, "cloudClips": True, "videoReview": True,
                    "cloudVideo": True, "clipBoundaries": ["north"], "dailyQuota": index + 1}, provider)
                if not self.service.review_settings(self.admin)["enabled"]:
                    self.service.review_settings(self.admin, {"enabled": True, "revision": 0})
                event_id = f"candidate-{index}"
                self.push(event_id, time.time(), "motion", "cam")
                incident = next(alert["id"] for alert in self.monitor.dispatch(self.actor)["alerts"]
                                if alert["observation"]["id"] == event_id)
                frame = {"reference": {"ref": "event-time", "eventFrame": True}}
                recording = {"eventOffsetSeconds": 2, "durationSeconds": 4}
                with patch.object(self.service, "recording_frames", return_value=([frame], recording)), \
                        patch.object(self.service, "recording", return_value=(self.state / "clip.mp4", {}, recording)), \
                        patch("ai_jobs.prepare_video", return_value=(b"masked clip", 0, 4)):
                    job = self.service.create(self.actor, {"type": "clip-review", "incidentId": incident,
                                                           "event": {"sensorId": "cam", "id": event_id}})
                    self.run_queued()
                result = self.service.read(self.actor, job["id"])
                if outcome == "unable-to-verify":
                    self.assertIn("limit", result["videoFallback"]["unavailable"])
                else:
                    self.assertNotIn("videoFallback", result)
                provider.review_video.assert_not_called()

    def test_automatic_frames_claim_only_new_camera_incidents_and_share_review(self):
        provider = Mock()
        provider.name, provider.model = "bedrock", "nova"
        provider.generate.return_value = {"outcome": "unable-to-verify", "explanation": "Unclear frame."}, {
            "inputTokens": 1100, "outputTokens": 20}
        provider.review_video.return_value = ([{"start_sec": .5, "end_sec": 2, "occupancy": "observed",
                                               "summary": "Person by the door."}], {"inputVideoSeconds": 4, "outputTokens": 30})
        settings = {"clipReview": True, "cloudClips": True, "videoReview": True, "cloudVideo": True,
                    "clipBoundaries": ["north"], "dailyQuota": 3}
        service = AIJobs(self.gateway, settings, provider)
        automatic = service.automatic_frames
        self.assertFalse(automatic.read(self.admin)["enabled"])
        now = time.time()
        self.push("before-enable", now, "motion", "cam")
        older = self.incident()
        with self.assertRaises(PermissionError):
            automatic.change(self.actor, {"enabled": True, "revision": 0})
        self.assertTrue(automatic.change(self.admin, {"enabled": True, "revision": 0})["enabled"])
        with self.assertRaises(Conflict):
            automatic.change(self.admin, {"enabled": False, "revision": 0})
        service.review_settings(self.admin, {"enabled": True, "revision": 0})
        self.assertIsNone(automatic.read(self.actor, older)["review"])
        self.push("door-with-linked-camera", now + 1)
        self.assertIsNone(automatic.read(self.actor, self.incident())["review"])
        self.assertIsNone(automatic.take(now + 12))
        self.push("camera-incident", now + 2, "motion", "cam")
        incident = self.incident()
        self.assertEqual(automatic.read(self.actor, incident)["review"]["state"], "collecting")
        self.assertIsNone(automatic.take(now + 3))
        job = automatic.take(now + 13)
        self.assertEqual(job[3], incident)
        with self.assertRaises(PermissionError):
            automatic.read(self.outsider, incident)
        frame = {"reference": {"ref": "event-time", "eventFrame": True}}
        recording = {"eventOffsetSeconds": 2, "durationSeconds": 4}
        with patch.object(service, "recording_frames", return_value=([frame], recording)), \
                patch.object(service, "recording", return_value=(self.state / "clip.mp4", {}, recording)), \
                patch("ai_jobs.prepare_video", return_value=(b"masked clip", 0, 4)):
            service.work(job)
        result = automatic.read(self.actor, incident)["review"]
        self.assertEqual(result["outcome"], "unable-to-verify")
        self.assertEqual(result["videoFallback"]["segments"][0]["startSeconds"], .5)
        self.assertEqual(result, automatic.read(self.admin, incident)["review"])
        with self.assertRaises(PermissionError):
            service.read(self.actor, job[0])
        provider.review_video.assert_called_once_with(b"masked clip")
        restarted = AIJobs(self.gateway, settings, provider)
        self.assertIsNone(restarted.automatic_frames.take(now + 15))
        self.push("over-quota", now + 5, "motion", "cam")
        self.assertIsNone(restarted.automatic_frames.take(now + 16))
        self.assertIn("automatic AI review limit", restarted.automatic_frames.read(self.actor, self.incident())["review"]["message"])
        provider.generate.assert_called_once()

    def test_automatic_frame_review_rechecks_the_setting_before_sending_media(self):
        provider = Mock()
        provider.name, provider.model = "bedrock", "nova"
        service = AIJobs(self.gateway, {"clipReview": True, "cloudClips": True, "clipBoundaries": ["north"]}, provider)
        automatic = service.automatic_frames
        automatic.change(self.admin, {"enabled": True, "revision": 0})
        when = time.time()
        self.push("motion", when, "motion", "cam")
        job = automatic.take(when + 12)
        automatic.change(self.admin, {"enabled": False, "revision": 1})
        service.work(job)
        provider.generate.assert_not_called()
        self.assertEqual(automatic.read(self.actor, self.incident())["review"]["state"], "failed")
        self.assertEqual(service.quota.read(self.actor)["automaticRemaining"], 20)

    def test_waiting_operator_work_leaves_automatic_incident_unclaimed(self):
        provider = Mock()
        provider.name, provider.model = "bedrock", "nova"
        service = AIJobs(self.gateway, {"clipReview": True, "cloudClips": True, "clipBoundaries": ["north"]}, provider)
        automatic = service.automatic_frames
        automatic.change(self.admin, {"enabled": True, "revision": 0})
        when = time.time()
        self.push("motion", when, "motion", "cam")
        incident = self.incident()
        operator = service.create(self.actor, {"type": "clip-review", "incidentId": incident,
                                               "event": {"sensorId": "cam", "id": "motion"}})
        self.assertIsNone(automatic.take(when + 12))
        self.assertEqual(automatic.read(self.actor, incident)["review"]["state"], "collecting")
        manual = service.take()
        self.assertEqual(manual[0], operator["id"])
        service.work(manual)
        self.assertEqual(automatic.take(when + 13)[3], incident)

    def test_site_quota_combines_automation_and_preserves_operator_capacity_after_restart(self):
        quota = self.service.quota
        self.assertEqual((quota.read(self.actor)["dailyLimit"], quota.read(self.actor)["operatorReserve"]), (3, 1))
        policy = {"dailyLimit": 6, "operatorReserve": 3, "revision": 0}
        with self.assertRaises(PermissionError):
            quota.read(self.actor, policy)
        with self.assertRaises(CameraError):
            quota.read(self.admin, {**policy, "operatorReserve": 7})
        self.assertEqual(quota.read(self.admin, policy)["revision"], 1)
        with self.assertRaises(Conflict):
            quota.read(self.admin, policy)

        automatic = self.service.automatic
        automatic.change(self.admin, {"enabled": True, "revision": 0})
        now = time.time()
        settle = now + SETTLE_SECONDS + 2
        self.push("first-door", now)
        with patch.object(self.fixture, "generate", return_value=({"keyEvents": ["incident"]}, {})):
            automatic.run_one(settle)
        self.push("forced-door", now + 1)
        with self.gateway.activity.connect() as db:
            # Triage, a frame review and its fallback fill the shared allowance.
            db.execute("INSERT INTO site_ai_jobs VALUES('fixture','','','camera-incident','cam','automatic-frame-review',"
                       "'{}','ready',?,'',NULL,?)",
                       (settle, json.dumps({"provider": "bedrock", "videoFallback": {"submittedAt": settle}})))
        self.assertIsNone(automatic.take(settle + 1))
        self.assertEqual(automatic.read(self.actor, self.incident())["review"]["reason"], "limit")
        restarted = AIJobs(self.gateway, {"provider": "on-premises", "handoff": True, "allowAutomatedTriage": False}, self.fixture)
        with self.gateway.activity.connect() as db:
            budget = restarted.quota.snapshot(db, settle)
            self.assertEqual(budget["usage"], {"operator": 0, "frame": 1, "fallback": 1, "triage": 1})
            self.assertEqual((budget["dailyLimit"], budget["operatorReserve"], budget["revision"]), (6, 3, 1))
            self.assertEqual((budget["automaticRemaining"], budget["operatorRemaining"], budget["protectedRemaining"]), (0, 3, 3))
            tomorrow = restarted.quota.snapshot(db, settle + 86400)
            self.assertEqual((tomorrow["automaticRemaining"], tomorrow["operatorRemaining"], tomorrow["protectedRemaining"]), (3, 6, 3))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Requires gateway media tools")
    def test_video_excerpt_applies_privacy_mask(self):
        fixture = Path(__file__).resolve().parents[2] / "desktop-app/tests/e2e/fixtures/gate-recording.mp4"
        clip = {"eventOffsetSeconds": 2.2, "durationSeconds": 6}
        plain, start, duration = prepare_video("ffmpeg", "ffprobe", fixture, clip, [])
        masked, _, _ = prepare_video("ffmpeg", "ffprobe", fixture, clip, [[0, 0, 100, 100]])
        self.assertEqual((start, duration), (0, 6))
        self.assertNotEqual(plain, masked)

    @unittest.skipUnless(shutil.which("ffmpeg"), "Requires the existing FFmpeg gateway dependency")
    def test_clip_frame_uses_media_timestamp_and_applies_mask(self):
        file = Path(__file__).resolve().parents[2] / "desktop-app/tests/e2e/fixtures/gate-recording.mp4"
        stamp = 1700000000012
        clip = {"eventOffsetSeconds": 2.07, "durationSeconds": 6, "occurredAt": stamp}
        plain = sample_frames("ffmpeg", file, clip, [])
        masked = sample_frames("ffmpeg", file, clip, [[0, 0, 100, 100]])
        point = next(frame for frame in plain if frame["reference"]["eventFrame"])
        self.assertAlmostEqual(point["reference"]["offsetSeconds"], 2.2)
        self.assertEqual(point["reference"]["occurredAt"], utc(stamp / 1000 + .13))
        self.assertNotEqual(point["jpeg"], next(frame["jpeg"] for frame in masked if frame["reference"]["eventFrame"]))
        answer = clip_result({"outcome": "observed", "explanation": "Visible", "evidenceRefs": [plain[0]["reference"]["ref"]]}, plain)
        self.assertEqual(answer["outcome"], "unable-to-verify")

    def test_health_uses_consecutive_buckets_then_recovers(self):
        service = DeviceDegradation(self.gateway)
        current = int(time.time() // BUCKET) * BUCKET - 7 * BUCKET
        period = regime(timezone.utc, current)
        with self.gateway.activity.connect() as db:
            for day in range(1, 8):
                for index in range(8):
                    stamp = current - day * DAY + index * BUCKET
                    db.execute("INSERT INTO camera_drift_buckets VALUES('cam','fp','frame-age',?,?,1,2,1)", (stamp, period))
            for i, elapsed in enumerate([0, 1, 3, 4, 5, 6, 7, 8]):
                stamp = current + elapsed * BUCKET
                total = 25 if i < 5 else 1
                db.execute("INSERT INTO camera_drift_buckets VALUES('cam','fp','frame-age',?,?,?,2,1)", (stamp, period, total))
                service.evaluate(db, "cam", "fp", "frame-age", stamp, period)
                opened = db.execute("SELECT count(*) FROM camera_drift_warnings").fetchone()[0]
                self.assertEqual(opened, int(i >= 4))
            issue = db.execute("SELECT opened,recovered,reason FROM camera_drift_warnings").fetchone()
            self.assertEqual(issue, (current + 6 * BUCKET, current + 9 * BUCKET, "normal"))
        self.assertEqual(len(service.read(self.actor)["cameras"]), 1)
        self.assertEqual(service.read(self.outsider)["cameras"], [])
        service.collect(time.time())

    def notification(self, radar, stamp, operation, value, topic=None):
        packet = ET.Element("fixture")
        note = add(packet, WSN, "NotificationMessage")
        add(note, WSN, "Topic", topic or "axis:" + radar.topic)
        body = add(add(note, WSN, "Message"), SCHEMA, "Message", UtcTime=utc(stamp), PropertyOperation=operation)
        add(add(body, SCHEMA, "Data"), SCHEMA, "SimpleItem", Name="active", Value=value)
        radar.notifications(packet, stamp)

    def test_radar_observe_groups_once_and_reviews_with_camera_scope(self):
        fusion = self.gateway.sensor_fusion = self.monitor.sensor_fusion = SensorFusion(self.gateway, [GATE])
        radar = fusion.gates[0]
        base = time.time() + .04
        self.push("forced", base)
        incident = self.incident()
        self.assertEqual(fusion.read(self.actor, incident)["episode"]["state"], "collecting")
        self.assertIsNone(fusion.read(self.outsider, incident)["episode"])
        self.notification(radar, base + .01, "Initialized", "true")
        self.notification(radar, base + .04, "Changed", "false")
        self.notification(radar, base + .06, "Changed", "true", "axis:Other")
        self.notification(radar, base + .08, "Changed", "true")
        self.notification(radar, base + .08, "Changed", "true")
        self.assertEqual(len(self.monitor.dispatch(self.actor)["alerts"]), 1)
        fusion.complete(base + 45)
        finished = fusion.read(self.actor, incident)["episode"]
        self.assertEqual(finished["state"], "corroborated")
        self.assertEqual([item["source"] for item in finished["evidence"]], ["door", "radar"])
        reviewed = fusion.review(self.actor, {"incidentId": incident, "outcome": "confirmed"})["episode"]
        self.assertEqual(reviewed["review"]["outcome"], "confirmed")
        with self.assertRaises(PermissionError):
            fusion.review(self.outsider, {"incidentId": incident, "outcome": "false-corroboration"})

    def test_competing_doors_are_ambiguous_and_unverified_clock_abstains(self):
        fusion = SensorFusion(self.gateway, [{**GATE, "doorClockVerified": False}])
        self.monitor.sensor_fusion = fusion
        base = time.time()
        self.push("a", base)
        incident = self.incident()
        fusion.complete(base + 45)
        self.assertEqual(fusion.read(self.actor, incident)["episode"]["state"], "insufficient-evidence")
        with patch("sensor_fusion.time.time", return_value=base + 22):
            self.push("b", base + 20)
            self.push("c", base + 22)
        recent = self.incident()
        fusion.complete(base + 67)
        self.assertEqual(fusion.read(self.actor, recent)["episode"]["state"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
