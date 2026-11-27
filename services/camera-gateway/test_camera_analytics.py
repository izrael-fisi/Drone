from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from analytics_contract import effective_rule, evidence, matches_evidence, policy_zone, worker_tokens
from onvif import CameraError
from site_activity import SiteActivity
from site_monitoring import SiteMonitoring
from site_telemetry import SiteTelemetry
from site_configuration import normalize_configuration
from site_collaboration import Conflict
from video_analytics.tracking import Analytics, Detection, Track, Tracker


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
    def test_dock_tracks_state_across_vehicles_misses_and_worker_restarts(self):
        dock = {**rule("dock", label="vehicle", mode="observe"), "trackOccupancy": True, "seconds": 2,
                "points": [[.3, .2], [.8, .2], [.8, .75], [.3, .75]]}
        policy = {"version": 1, "rules": [dock]}
        engine = Analytics(1.2, .5)
        events = []

        def sample(first, last, *detections, missed=False):
            for quarter in range(first, last + 1):
                events.extend(engine.sample(detections, policy, quarter / 4, (640, 480), missed_sample=missed))
            return engine.dock_occupancy["dock"].state

        self.assertEqual(sample(0, 20, box(.15, "vehicle"), box(.55)), "vacant")
        self.assertEqual(sample(21, 25, box(.45, "vehicle")), "vacant")  # A pass-through.
        self.assertEqual(sample(26, 27), "vacant")
        self.assertEqual(sample(28, 36, box(.45, "vehicle")), "occupied")
        [(observed_rule, arrival)] = events
        self.assertEqual(observed_rule["id"], "dock")
        self.assertEqual(arrival["box"], list(box(.45, "vehicle").box))
        engine.dock_occupancy["dock"].arrival_event_id = "cctv-" + "1" * 32
        self.assertEqual(sample(37, 41, box(.69, "vehicle")), "occupied")  # A different vehicle.
        self.assertEqual(sample(42, 44), "occupied")
        self.assertEqual(sample(45, 45, box(.69, "vehicle")), "occupied")
        self.assertEqual(sample(46, 63), "occupied")
        self.assertEqual(sample(64, 64, missed=True), "occupied")
        self.assertEqual(sample(65, 84), "vacant")
        self.assertIsNone(engine.dock_occupancy["dock"].arrival_event_id)
        self.assertEqual(sample(85, 93, box(.45, "vehicle")), "occupied")
        self.assertEqual(len(events), 2)
        self.assertEqual(sample(100, 100, box(.45, "vehicle")), "unknown")
        self.assertEqual(sample(101, 108, box(.45, "vehicle")), "occupied")
        self.assertEqual(len(events), 2)
        engine = Analytics(1.2)
        self.assertEqual(sample(109, 117, box(.45, "vehicle")), "occupied")
        policy["version"] = 2
        self.assertEqual(sample(118, 118, box(.45, "vehicle")), "unknown")
        self.assertEqual(len(events), 2)

    def test_factory_safety_fires_once_and_rejects_short_or_moving_events(self):
        walkway = [[.2, .25], [.8, .25], [.8, .75], [.2, .75]]
        lane = {**rule("lane", mode="observe"), "points": walkway, "seconds": 1}
        stop = {**rule("walkway", "restricted-stop", "vehicle", "observe"), "points": walkway, "seconds": 6}
        for policy_rule, samples, expected in [
            (lane, [box(.1)] * 16, []),
            (lane, [box(.4)] * 4, []),
            (lane, [box(.4)] * 25, [1]),
            (stop, [box(.4, "vehicle")] * 24, []),
            (stop, [box(.25 + (n % 15) * .03, "vehicle") for n in range(60)], []),
            (stop, [box(.4, "vehicle")] * 42, [6]),
        ]:
            with self.subTest(rule=policy_rule["id"], expected=expected, frames=len(samples)):
                engine = Analytics(1.2, .5)
                policy = {"version": 1, "rules": [policy_rule]}
                actual = [quarter / 4 for quarter, detection in enumerate(samples)
                          if engine.sample([detection], policy, quarter / 4, (640, 480))]
                self.assertEqual(actual, expected)

    def test_person_model_transition_cannot_create_a_crossing_from_shifted_boxes(self):
        policy = {"version": 1, "rules": [rule(kind="line-crossing"), rule("vehicle", "line-crossing", "vehicle")]}
        engine = Analytics(1.2)
        for stamp in (0., .25):
            self.assertFalse(engine.sample([box(.48), box(.48, "vehicle")], policy, stamp, (640, 480)))
        [(vehicle_rule, _)] = engine.sample([box(.52), box(.52, "vehicle")], policy, .50, (640, 480),
                                            person_model_changed=True)
        self.assertEqual(vehicle_rule["object"], "vehicle")
        engine.sample([box(.52)], policy, .75, (640, 480))
        [(person_rule, _)] = engine.sample([box(.48)], policy, 1., (640, 480))
        self.assertEqual(person_rule["object"], "person")

    def test_person_motion_keeps_overlapping_walkers_on_their_own_tracks(self):
        tracker = Tracker(1.2)
        # The trailing walker's new box overlaps the leading walker's previous box.
        leading = Detection("person", .73, (.2579, .5074, .2720, .5761), "yolo11:test")
        trailing = Detection("person", .72, (.2521, .4959, .2643, .5681), "yolo11:test")
        tracker.tracks = {"leading": Track("leading", leading, 0., 1., (.022, -.0007), 8),
                          "trailing": Track("trailing", trailing, 0., 1., (.0275, -.0124), 8)}
        new_leading = Detection("person", .68, (.2670, .5066, .2771, .5756), "yolo11:test")
        new_trailing = Detection("person", .69, (.2570, .4961, .2704, .5743), "yolo11:test")

        tracker.update([new_leading, new_trailing], 1.25, person_motion_association=True)

        self.assertEqual(tracker.tracks["leading"].detection, new_leading)
        self.assertEqual(tracker.tracks["trailing"].detection, new_trailing)

    def test_person_track_support_preserves_reported_entry_but_allows_a_second_person(self):
        engine = Analytics(1.2, person_track_support=True)
        policy = {"version": 1, "rules": [rule()]}

        def person(x, bottom, score=.9):
            return Detection("person", score, (x - .007, bottom - .07, x + .007, bottom), "fixture")

        frame = (1920, 1080)
        first, weak, recovered, other = person(.40, .56), person(.402, .5654, .12), person(.411, .5747), person(.46, .575)
        for stamp in (0, .25):
            self.assertFalse(engine.sample([first], policy, stamp, frame))
        [(_, initial)] = engine.sample([first], policy, .5, frame)
        for stamp in (.75, 1., 1.25, 1.5, 1.75, 2., 2.25):
            self.assertFalse(engine.sample([], policy, stamp, frame, weak=[weak] if stamp == 1.25 else []))
        for stamp in (2.5, 2.75):
            self.assertFalse(engine.sample([recovered, other], policy, stamp, frame))
        [(_, second)] = engine.sample([recovered, other], policy, 3., frame)
        self.assertNotEqual(initial["trackId"], second["trackId"])
        self.assertEqual(second["box"], list(other.box))
        self.assertEqual(engine.tracker.tracks[initial["trackId"]].detection, recovered)

    def test_person_color_resolves_two_close_spatial_matches(self):
        import numpy as np
        from video_analytics.detectors import person_color_signature

        initial = [box(.496), box(.504)]
        red = np.zeros((256, 256, 3), dtype=np.uint8)
        red[:, :, 0] = 240
        blue = np.zeros_like(red)
        blue[:, :, 2] = 240
        colors = {i: person_color_signature(image, detection.box)
                  for i, (image, detection) in enumerate(zip((red, blue), initial))}
        crossing = [box(.503), box(.497)]
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                tracker = Tracker(1.2)
                earlier = tracker.update(initial, 0., colors=colors if enabled else None)
                left = earlier[0].id
                tracker.update(crossing, .25, colors=colors if enabled else None)
                self.assertIs(tracker.tracks[left].detection, crossing[0 if enabled else 1])

    def test_person_track_support_yields_to_ambiguity_and_refuses_masks_or_stream_gaps(self):
        def person(x, bottom, score=.9):
            return Detection("person", score, (x - .007, bottom - .07, x + .007, bottom), "fixture")

        frame = (1920, 1080)
        first, weak, recovered = person(.40, .56), person(.402, .5654, .12), person(.411, .5747)
        for reason in ("ambiguity", "exclusion", "stream gap", "expired", "no support", "below rule threshold"):
            with self.subTest(reason=reason):
                engine = Analytics(1.2, person_track_support=True)
                entry = rule()
                if reason == "exclusion":
                    entry["exclusions"] = [[[.405, .5], [.407, .5], [.407, .6], [.405, .6]]]
                policy = {"version": 1, "rules": [entry]}
                for stamp in (0, .25):
                    self.assertFalse(engine.sample([first], policy, stamp, frame))
                [(_, initial)] = engine.sample([first], policy, .5, frame)
                gaps = [.75, 1., 1.25, 1.5, 1.75, 2., 2.25]
                if reason == "expired":
                    gaps.append(2.5)
                for stamp in gaps:
                    self.assertFalse(engine.sample([], policy, stamp, frame,
                                                   weak=[weak] if stamp == 1.25 and reason != "no support" else []))
                stamp = 2.75 if reason == "expired" else 2.5
                detections = [recovered, person(.401, .575)] if reason == "ambiguity" else [recovered]
                if reason == "below rule threshold":
                    detections = [person(.411, .5747, .30)]
                self.assertFalse(engine.sample(detections, policy, stamp, frame, missed_sample=reason == "stream gap"))
                self.assertNotIn(initial["trackId"], {track.id for track in engine.tracker.tracks.values() if track.last == stamp})
                self.assertFalse(engine.sample([recovered], policy, stamp + .25, frame))
                matches = engine.sample([recovered], policy, stamp + .5, frame)
                if reason == "below rule threshold":
                    self.assertFalse(matches)
                    matches = engine.sample([recovered], policy, stamp + .75, frame)
                [(_, new)] = matches
                self.assertNotEqual(new["trackId"], initial["trackId"])

    def test_zone_entry_rearms_only_after_a_clear_exit(self):
        engine = Analytics(1.2)
        entry = {**rule(kind="zone-entry"), "points": [[.4, .32], [.7, .32], [.7, .56], [.4, .56]]}
        policy = {"version": 1, "rules": [entry]}
        bottoms = [.563, .563, .554, .554, .554, .563, .563, .563, .554, .554, .554,
                   .59, .554, .554, .554]
        events = []
        for index, bottom in enumerate(bottoms):
            detection = Detection("person", .9, (.53, bottom - .12, .57, bottom), "fixture-model")
            events.extend((index * .25, event["trackId"])
                          for _, event in engine.sample([detection], policy, index * .25, (1920, 1072)))
        self.assertEqual([time for time, _ in events], [1., 3.5])
        self.assertEqual(events[0][1], events[1][1])

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

    def test_scaled_tripwire_buffer_recovers_a_small_person_after_reacquisition(self):
        def observations(centers, scale):
            policy = {"version": 1, "rules": [{**rule(kind="line-crossing"), "direction": "negative",
                                               "scaleCrossingBuffer": scale}]}
            engine = Analytics(1.2)
            for i, center in enumerate(centers):
                sightings = [] if center is None else [Detection("person", .9, (center - .006, .4, center + .006, .5), "fixture-model")]
                yield from engine.sample(sightings, policy, i * .25, (1920, 1080))

        crossing = [.480, None, .491, .497, .503, .508]
        self.assertEqual(list(observations(crossing, False)), [])
        [(_, evidence)] = list(observations(crossing, True))
        self.assertEqual(evidence["crossing"], [.5, .5])
        self.assertEqual(list(observations([.497, .503, .498, .502, .499], True)), [])
        self.assertEqual(list(observations([.480, .491, None, .510, .515], True)), [])

    def test_tripwire_endpoint_uses_the_observed_path_through_the_buffer(self):
        def crossings(points):
            wire = {**rule(kind="line-crossing"), "points": [[.5, .3], [.5, .65]], "direction": "negative"}
            policy = {"version": 1, "rules": [wire]}
            engine = Analytics(1.2)
            for i, (x, y) in enumerate(points):
                detection = Detection("person", .9, (x - .025, y - .20, x + .025, y), "camera")
                yield from engine.sample([detection], policy, i * .25, (1280, 720))

        # Two outer observations alone would put the crossing beyond the endpoint.
        inside = [(.47, .66), (.48, .66), (.494, .633), (.506, .637), (.521, .674)]
        [(_, evidence)] = list(crossings(inside))
        self.assertEqual(evidence["crossing"][0], .50)
        self.assertAlmostEqual(evidence["crossing"][1], .635)
        outside = [(.47, .62), (.48, .62), (.494, .663), (.506, .667), (.521, .632)]
        self.assertEqual(list(crossings(outside)), [])
        returned = [(.47, .62), (.48, .62), (.494, .63), (.506, .64), (.498, .655), (.506, .667), (.521, .632)]
        self.assertEqual(list(crossings(returned)), [])

    def test_tripwire_recovery_requires_established_motion_and_a_unique_observed_destination(self):
        frame = (1280, 720)
        for reason in ("confirmed", "disabled", "insufficient history", "transport", "expired", "ambiguous", "excluded",
                       "profile", "model change", "low score"):
            with self.subTest(reason=reason):
                engine = Analytics(1.2, person_tripwire_recovery=reason != "disabled")
                wire = {**rule(kind="line-crossing"), "direction": "negative"}
                if reason == "excluded":
                    wire["exclusions"] = [[[.491, .47], [.493, .47], [.493, .54], [.491, .54]]]
                policy = {"version": 1, "rules": [wire]}
                for stamp, x in ((0., .435), (.25, .455), (.5, .475)):
                    if stamp == 0 and reason == "insufficient history":
                        continue
                    self.assertFalse(engine.sample([box(x)], policy, stamp, frame))
                self.assertFalse(engine.sample([], policy, .75, frame, missed_sample=reason == "transport"))
                self.assertFalse(engine.sample([], policy, 1., frame))
                if reason == "profile":
                    policy = {**policy, "rules": [{**wire, "profile": "night"}]}
                hits = [box(.535, score=.25 if reason == "low score" else .9)]
                if reason == "ambiguous":
                    hits.append(box(.545))
                matches = engine.sample(hits, policy, 1.50 if reason == "expired" else 1.25, frame,
                                        person_model_changed=reason == "model change")
                if reason == "confirmed":
                    [(_, event)] = matches
                    self.assertEqual(event["crossing"], [.5, .5])
                    self.assertEqual(event["box"], list(hits[0].box))
                    self.assertFalse(engine.sample([box(.555)], policy, 1.5, frame))
                else:
                    self.assertFalse(matches)

    def test_dwell_resets_on_dropout_and_new_version(self):
        engine = Analytics(1.2)
        policy = {"version": 1, "rules": [rule(kind="zone-dwell")]}
        self.assertFalse(engine.sample([box(.2)], policy, 1, (640, 360)))
        self.assertFalse(engine.sample([], policy, 1.25, (640, 360)))
        self.assertFalse(engine.sample([box(.2)], policy, 1.6, (640, 360)))
        self.assertFalse(engine.sample([box(.2)], policy, 1.9, (640, 360)))
        self.assertEqual(len(engine.sample([box(.2)], policy, 2.15, (640, 360))), 1)
        self.assertFalse(engine.sample([box(.2)], {**policy, "version": 2}, 2.45, (640, 360)))

    def test_dwell_pauses_during_short_gap_and_counts_only_supported_time(self):
        engine = Analytics(1.2)
        dwell = {**rule(kind="zone-dwell"), "seconds": .75, "dwellGapSeconds": .75}
        policy = {"version": 1, "rules": [dwell]}
        for stamp, detections in ((0, [box(.2)]), (.25, [box(.2)]), (.5, []), (.75, [box(.2)]), (1, [box(.2)])):
            self.assertFalse(engine.sample(detections, policy, stamp, (640, 360)))
        matches = engine.sample([box(.2)], policy, 1.25, (640, 360))
        self.assertEqual([item["dwellSeconds"] for _, item in matches], [.75])

    def test_live_dwell_uses_decoded_slots_to_tolerate_bounded_receipt_jitter(self):
        dwell = {**rule(kind="zone-dwell"), "seconds": .5, "dwellGapSeconds": .75}
        policy = {"version": 1, "rules": [dwell]}
        frame = (640, 360)
        for resume_at, sequence, expected in ((1.017, 4, True), (1.09, 4, False), (1.017, 5, False)):
            with self.subTest(resume_at=resume_at, sequence=sequence):
                engine = Analytics(1.2, .4375, frame_period=.25)
                for index, stamp in enumerate((0, .25, .5, .75)):
                    detections = [box(.2)] if index < 2 else []
                    self.assertFalse(engine.sample(detections, policy, stamp, frame, source_sequence=index))
                self.assertFalse(engine.sample([box(.2)], policy, resume_at, frame, source_sequence=sequence))
                matches = engine.sample([box(.2)], policy, resume_at + .25, frame, source_sequence=sequence + 1)
                self.assertEqual(bool(matches), expected)
                if matches:
                    self.assertAlmostEqual(matches[0][1]["dwellSeconds"], .5)
        self.assertFalse(engine.sample([box(.2)], policy, 1.5, (640, 360)))

    def test_dwell_restarts_after_long_gap_or_reconnection_across_mask(self):
        for elapsed, after in ((.5, .52), (1., .48)):
            with self.subTest(elapsed=elapsed, after=after):
                dwell = {**rule(kind="zone-dwell"), "seconds": .5, "dwellGapSeconds": .75,
                         "exclusions": [[[.49, .4], [.51, .4], [.51, .6], [.49, .6]]]}
                policy = {"version": 1, "rules": [dwell]}
                engine = Analytics(1.5)
                for stamp in (0, .25):
                    self.assertFalse(engine.sample([box(.48)], policy, stamp, (640, 360)))
                reconnected = .25 + elapsed
                self.assertFalse(engine.sample([box(after)], policy, reconnected, (640, 360), missed_sample=True))
                self.assertFalse(engine.sample([box(after)], policy, reconnected + .25, (640, 360)))
                self.assertEqual(len(engine.sample([box(after)], policy, reconnected + .5, (640, 360))), 1)

    def test_dwell_reconnects_motion_only_within_the_configured_gap_and_region(self):
        def person(center):
            return Detection("person", .9, (center - .06, .3, center + .06, .5), "fixture-model")

        mask = [[[.38, .4], [.39, .4], [.39, .6], [.38, .6]]]
        for reason, centers, gap, exclusions, expected in (
            ("consistent motion", (.3, .325, .35), 1., [], [.75]),
            ("unexpected jump", (.35, .35, .35), 1., [], []),
            ("elapsed gap limit", (.3, .325, .35), .75, [], []),
            ("path crosses mask", (.3, .325, .35), 1., mask, []),
        ):
            with self.subTest(reason=reason):
                engine = Analytics(1.2)
                dwell = {**rule(kind="zone-dwell"), "seconds": .75, "dwellGapSeconds": gap, "exclusions": exclusions}
                policy = {"version": 1, "rules": [dwell]}
                for index, center in enumerate(centers):
                    self.assertFalse(engine.sample([person(center)], policy, index * .25, (640, 360)))
                for stamp in (.75, 1., 1.25):
                    self.assertFalse(engine.sample([], policy, stamp, (640, 360)))
                self.assertFalse(engine.sample([person(.425)], policy, 1.5, (640, 360)))
                matches = engine.sample([person(.435)], policy, 1.75, (640, 360))
                self.assertEqual([item["dwellSeconds"] for _, item in matches], expected)

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

    def test_vehicle_stop_rejects_small_moving_vehicle_and_requires_contiguous_samples(self):
        stop = {"version": 1, "rules": [rule("stop", "restricted-stop", "vehicle")]}
        frame = (1920, 1080)

        def distant_vehicle(x):
            return Detection("vehicle", .9, (x - .01, .3, x + .01, .328), "fixture")

        engine = Analytics(1.2)
        for index, center in enumerate((.348, .355, .362, .369)):
            self.assertFalse(engine.sample([distant_vehicle(center)], stop, index * .25, frame))
        self.assertFalse(engine.sample([distant_vehicle(.369)], stop, 1.25, frame, missed_sample=True))
        self.assertFalse(engine.sample([distant_vehicle(.370)], stop, 1.5, frame))
        self.assertEqual(len(engine.sample([distant_vehicle(.368)], stop, 1.75, frame)), 1)

    def test_movement_stop_requires_an_approach_and_stationary_confirmation(self):
        stop = {**rule("stop", "restricted-stop", "vehicle"), "requireMovement": True,
                "points": [[.30, .1], [.8, .1], [.8, .8], [.3, .8]]}
        policy = {"version": 1, "rules": [stop]}
        frame = (640, 360)
        engine = Analytics(1.2)
        for index, center in enumerate((.60, .601, .599, .60, .599)):
            self.assertFalse(engine.sample([box(center, "vehicle")], policy, index * .25, frame))
            self.assertFalse(engine.confirming_vehicle_stop)
        for index, center in enumerate((.25, .273, .296, .319, .34, .34)):
            self.assertFalse(engine.sample([box(center, "vehicle")], policy, 2.5 + index * .25, frame))
            self.assertEqual(engine.confirming_vehicle_stop, center >= .30)
        [(_, item)] = engine.sample([box(.341, "vehicle")], policy, 4., frame)
        self.assertIs(item["movementObserved"], True)
        self.assertFalse(engine.confirming_vehicle_stop)
        self.assertFalse(engine.sample([box(.34, "vehicle")], policy, 4.25, frame))

    def test_movement_stop_discards_approach_after_missing_sample(self):
        policy = {"version": 1, "rules": [{**rule("stop", "restricted-stop", "vehicle"), "requireMovement": True}]}
        for gap in ("skipped", "missing", "low confidence"):
            with self.subTest(gap=gap):
                engine = Analytics(1.2)
                frame = (640, 360)
                for index, center in enumerate((.25, .273, .296, .319, .34)):
                    self.assertFalse(engine.sample([box(center, "vehicle")], policy, index * .25, frame))
                detected = [] if gap == "missing" else [box(.34, "vehicle", .20 if gap == "low confidence" else .9)]
                self.assertFalse(engine.sample(detected, policy, 1.25, frame, missed_sample=gap == "skipped"))
                self.assertFalse(engine.confirming_vehicle_stop)
                for stamp in (1.5, 1.75, 2.):
                    self.assertFalse(engine.sample([box(.34, "vehicle")], policy, stamp, frame))

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

    def test_duration_is_validated_only_for_the_published_dwell_rule(self):
        dwell = {**rule(kind="zone-dwell"), "seconds": 30, "dwellGapSeconds": .75,
                 "points": [[.1, .1], [.8, .1], [.8, .8], [.1, .8]]}
        self.snapshot["sensors"][0]["analyticsRules"] = [dwell]
        snapshot = normalize_configuration(self.snapshot)
        self.assertEqual(snapshot["sensors"][0]["analyticsRules"][0]["dwellGapSeconds"], .75)
        with self.monitor.activity.connect() as db:
            db.execute("UPDATE configuration_versions SET body=? WHERE version=1", (json.dumps(snapshot),))
        event = self.event("dur", kind="zone-dwell")
        with self.assertRaises(CameraError):
            self.telemetry.push(event)
        event["analytics"]["dwellSeconds"] = 29.5
        with self.assertRaises(CameraError):
            self.telemetry.push(event)
        event["analytics"]["dwellSeconds"] = 30.25
        item = evidence(event["analytics"])
        self.assertFalse(matches_evidence({**dwell, "kind": "zone-entry"}, item))
        self.assertFalse(matches_evidence(dwell, {**item, "box": [.85, .3, .95, .5]}))
        self.assertTrue(self.telemetry.push(event)["accepted"])
        result = self.monitor.view({"cameras": "*"}, 0, 0, 0, 0)["siteEvents"][0]["observation"]
        self.assertEqual(result["analytics"]["dwellSeconds"], 30.25)
        self.assertIn("30.25 s", result["summary"])

    def test_movement_stop_requires_matching_published_rule_and_evidence(self):
        stop = {**rule("stop", "restricted-stop", "vehicle"), "requireMovement": True,
                "points": [[.1, .1], [.8, .1], [.8, .8], [.1, .8]]}
        self.snapshot["sensors"][0]["analyticsRules"] = [stop]
        snapshot = normalize_configuration(self.snapshot)
        self.assertIs(snapshot["sensors"][0]["analyticsRules"][0]["requireMovement"], True)
        with self.monitor.activity.connect() as db:
            db.execute("UPDATE configuration_versions SET body=? WHERE version=1", (json.dumps(snapshot),))
        event = self.event("arrived", "stop", "restricted-stop", "vehicle")
        with self.assertRaises(CameraError):
            self.telemetry.push(event)
        event["analytics"]["movementObserved"] = True
        self.assertFalse(matches_evidence(stop, {**event["analytics"], "box": [.85, .3, .9, .5]}))
        self.assertTrue(self.telemetry.push(event)["accepted"])
        [row] = self.monitor.view({"cameras": "*"}, 0, 0, 0, 0)["siteEvents"]
        self.assertIs(row["observation"]["analytics"]["movementObserved"], True)
        self.snapshot["sensors"][0]["analyticsRules"] = [{**rule(), "requireMovement": True}]
        with self.assertRaises(CameraError):
            normalize_configuration(self.snapshot)

    def test_scale_crossing_buffer_is_saved_only_for_person_tripwires(self):
        wire = {**rule(kind="line-crossing"), "scaleCrossingBuffer": True}
        self.snapshot["sensors"][0]["analyticsRules"] = [wire]
        snapshot = normalize_configuration(self.snapshot)
        self.assertIs(snapshot["sensors"][0]["analyticsRules"][0]["scaleCrossingBuffer"], True)
        for wrong_rule in ({**wire, "object": "vehicle"}, {**rule(), "scaleCrossingBuffer": True},
                           {**wire, "scaleCrossingBuffer": "true"}):
            with self.subTest(rule=wrong_rule), self.assertRaisesRegex(CameraError, "person tripwires"):
                self.snapshot["sensors"][0]["analyticsRules"] = [wrong_rule]
                normalize_configuration(self.snapshot)

    def test_analytics_status_is_independent_and_camera_scoped(self):
        self.monitor.analytics_heartbeat("camera", {"state": "running", "version": 1, "models": ["fixture-model"],
                                          "pending": 0, "fps": 5, "frameAgeSeconds": .2})
        private = self.monitor.view({"cameras": []}, 0, 0, 0, 0)
        self.assertEqual(private["analyticsWorkers"], {})
        supervisor = self.monitor.view({"cameras": "*"}, 0, 0, 0, 0)
        self.assertEqual(supervisor["analyticsWorkers"]["camera"]["fps"], 5)
        self.assertEqual(supervisor["health"], [])

    def test_dock_snapshot_requires_the_current_released_camera_rule(self):
        camera = self.snapshot["sensors"][0]
        dock = {**rule("dock", label="vehicle", mode="observe"), "trackOccupancy": True}
        camera["analyticsRules"].append(dock)
        with self.monitor.activity.connect() as db:
            db.execute("UPDATE configuration_versions SET body=?", (json.dumps(normalize_configuration(self.snapshot)),))
        now = time.time()
        sample = {"ruleId": "dock", "state": "occupied", "sampledAt": now - 1, "changedAt": now - 8,
                  "arrivalEventId": "cctv-" + "a" * 32}
        status = {"state": "running", "version": 1, "models": ["fixture-model"], "pending": 0, "fps": 5, "frameAgeSeconds": .2}
        self.monitor.analytics_heartbeat("camera", {**status, "dockOccupancy": [sample]})
        late = {**sample, "state": "vacant", "sampledAt": now - 2, "arrivalEventId": None}
        self.monitor.analytics_heartbeat("camera", {**status, "dockOccupancy": [late]})
        self.assertEqual(self.monitor.view({"cameras": ["camera"]}, 0, 0, 0, 0)["analyticsWorkers"]["camera"]["dockOccupancy"], [sample])
        self.assertEqual(self.monitor.view({"cameras": ["outside"]}, 0, 0, 0, 0)["analyticsWorkers"], {})
        for invalid in ([sample, sample], [{**sample, "ruleId": "neighbor"}], [{**sample, "sampledAt": now + 30}],
                        [{**sample, "changedAt": now}], [{**sample, "state": "unknown"}]):
            with self.subTest(invalid=invalid), self.assertRaises(CameraError):
                self.monitor.analytics_heartbeat("camera", {**status, "dockOccupancy": invalid})
        self.monitor.analytics_heartbeat("camera", {**status, "version": 0, "dockOccupancy": [sample]})
        old = self.monitor.view({"cameras": ["camera"]}, 0, 0, 0, 0)["analyticsWorkers"]["camera"]
        self.assertNotIn("dockOccupancy", old)
        self.telemetry.push(self.event(sample["arrivalEventId"], rule_id="dock", label="vehicle"))
        view = self.monitor.view({"cameras": ["camera"]}, 0, 0, 0, 0)
        self.assertEqual(view["alerts"], [])
        self.assertEqual(view["siteEvents"][0]["observation"]["id"], sample["arrivalEventId"])

    def test_factory_safety_review_filters_use_the_released_rule_and_camera_scope(self):
        camera = self.snapshot["sensors"][0]
        lane = {**rule("lane", mode="observe"), "safetyRule": "person-vehicle-lane"}
        walking = {**rule("walkway", "restricted-stop", "vehicle", "observe"), "safetyRule": "vehicle-marked-walkway"}
        camera["analyticsRules"].extend([lane, walking, rule("ordinary", mode="observe")])
        with self.monitor.activity.connect() as db:
            db.execute("UPDATE configuration_versions SET body=?", (json.dumps(normalize_configuration(self.snapshot)),))
        for item in (self.event("lane-event", "lane"), self.event("walkway-event", "walkway", "restricted-stop", "vehicle"),
                     self.event("ordinary-event", "ordinary")):
            self.telemetry.push(item)
        review = self.monitor.analytics_review
        actor = {"cameras": ["camera"]}
        def matches(filter_id, scoped=actor):
            return review.read(scoped, {"safety": [filter_id], "version": ["1"]})

        self.assertEqual(matches("all")["counts"]["pending"], 2)
        [entry] = matches("person-vehicle-lane")["entries"]
        self.assertEqual(entry["event"]["observation"]["id"], "lane-event")
        self.assertEqual(matches("all", {"cameras": ["other"]})["entries"], [])
        review.change({**actor, "role": "operator", "name": "Reviewer", "subject": "tester"},
                      {"sequence": entry["sequence"], "revision": 0, "outcome": "confirmed", "note": "Reviewed"})
        self.assertEqual(matches("person-vehicle-lane")["counts"]["confirmed"], 1)
        self.assertEqual(matches("person-vehicle-lane")["entries"], [])

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
