"""Anonymous single-camera tracks and deterministic image-space rules."""

from collections import Counter
from dataclasses import dataclass, field
import math
from uuid import uuid4

from analytics_contract import detection_anchor, excluded, qualifies
from site_geometry import contains, cross, segments_intersect


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    box: tuple[float, float, float, float]
    model: str

    @property
    def anchor(self):
        return detection_anchor(self.box, self.label)


def overlap(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    total = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / total if total else 0


@dataclass
class RuleState:
    profile: str = "all-day"
    since: float | None = None
    anchor: tuple | None = None
    side: int = 0
    fired: bool = False
    observed: float = 0.
    last_inside: float | None = None
    last_inside_sequence: int | None = None
    interrupted: bool = False
    reference_box: tuple | None = None
    dwell_velocity: tuple = (0., 0.)
    approach_box: tuple | None = None
    moved: bool = False
    tripwire_samples: tuple = ()
    wire_observation: tuple[tuple[float, float], float] | None = None
    wire_crossing: tuple[float, float] | None = None

    def reset_confirmation(self):
        self.anchor = self.since = self.last_inside = self.reference_box = None
        self.last_inside_sequence = None
        self.side, self.observed, self.interrupted = 0, 0., False
        self.dwell_velocity = (0., 0.)
        self.approach_box, self.moved = None, False
        self.tripwire_samples = ()
        self.wire_observation = self.wire_crossing = None


@dataclass
class Track:
    id: str
    detection: object
    first: float
    last: float
    velocity: tuple = (0., 0.)
    hits: int = 1
    states: dict = field(default_factory=dict)
    support: tuple[Detection, float] | None = None
    color: object = None


def update_detection(track, detection, stamp, color=None):
    elapsed = max(.001, stamp - track.last)
    velocity = tuple(max(-.3, min(.3, (after - before) / elapsed))
                     for before, after in zip(track.detection.anchor, detection.anchor))
    track.velocity = tuple(.5 * old + .5 * new for old, new in zip(track.velocity, velocity))
    track.detection, track.last, track.hits = detection, stamp, track.hits + 1
    track.support = None
    track.color = color


def unique_pairs(matches):
    tracks = Counter(identifier for identifier, _, _ in matches)
    boxes = Counter(index for _, index, _ in matches)
    return [match for match in matches if tracks[match[0]] == boxes[match[1]] == 1]


def same_entry(track, support, rules, recovered=None):
    anchors = [track.detection.anchor, support.anchor]
    if recovered is not None:
        anchors.append(recovered.anchor)
    entries = {}
    for rule in rules:
        state = track.states.get(rule["id"])
        if state is None or not state.fired or state.profile != rule.get("profile", "all-day"):
            continue
        if (track.detection.score < rule["minimumScore"]
                or (recovered is not None and recovered.score < rule["minimumScore"])):
            continue
        if any(not contains(rule["points"], point) or excluded(rule, point) for point in anchors):
            continue
        rings = (rule["points"], *rule.get("exclusions", ()))
        if any(segments_intersect(before, after, a, b)
               for before, after in zip(anchors, anchors[1:])
               for ring in rings for a, b in zip(ring, ring[1:] + ring[:1])):
            continue
        entries[rule["id"]] = state
    return entries


def support_is_recent(track, stamp):
    return track.support is not None and stamp - track.last <= 2.25 and stamp - track.support[1] <= 1.35


def supports_recovery(track, detection, stamp):
    weak, seen_at = track.support
    scale = (stamp - seen_at) / (seen_at - track.last)
    dx, dy = ((axis - previous) * scale for axis, previous in zip(weak.anchor, track.detection.anchor))
    projected = tuple(axis + shift for axis, shift in zip(weak.box, (dx, dy, dx, dy)))
    return overlap(weak.box, detection.box) >= .15 and overlap(projected, detection.box) >= .40


class Tracker:
    def __init__(self, max_gap):
        self.max_gap = max_gap
        self.tracks = {}

    def update(self, detections, stamp, weak=(), entry_rules=(), *, person_motion_association=False, colors=None):
        colors = colors or {}
        self.tracks = {identifier: track for identifier, track in self.tracks.items()
                       if stamp - track.last <= self.max_gap or support_is_recent(track, stamp)}
        matches = []
        for identifier, track in self.tracks.items():
            anchor, elapsed = track.detection.anchor, stamp - track.last
            if elapsed > self.max_gap:
                continue
            predicted = tuple(axis + speed * elapsed for axis, speed in zip(anchor, track.velocity))
            reference_box = track.detection.box
            if person_motion_association and track.detection.label == "person":
                # Compare both the anchor and the box at the expected position of this sample.
                dx, dy = (after - before for after, before in zip(predicted, anchor))
                left, top, right, bottom = reference_box
                reference_box = (left + dx, top + dy, right + dx, bottom + dy)
            for index, detection in enumerate(detections):
                if detection.label != track.detection.label:
                    continue
                distance = math.dist(predicted, detection.anchor)
                iou = overlap(reference_box, detection.box)
                if iou >= .15 or distance <= .035 + min(elapsed * .07, .07):
                    cost = distance - .12 * iou
                    color = colors.get(index)
                    if (elapsed <= .60 and track.color is not None and color is not None
                            and detection.model == track.detection.model):
                        cost += .04 * max(0., 1 - .5 * float(track.color @ color))
                    matches.append((cost, identifier, index))
        used_tracks, used_boxes = set(), set()
        updated = {}
        for _, identifier, index in sorted(matches):
            if identifier in used_tracks or index in used_boxes:
                continue
            track, detection = self.tracks[identifier], detections[index]
            update_detection(track, detection, stamp, colors.get(index))
            used_tracks.add(identifier)
            used_boxes.add(index)
            updated[index] = track
        if entry_rules:
            recovered = []
            for identifier, track in self.tracks.items():
                if stamp - track.last <= self.max_gap or not support_is_recent(track, stamp):
                    continue
                for index, detection in enumerate(detections):
                    if index in used_boxes or detection.label != "person" or not supports_recovery(track, detection, stamp):
                        continue
                    entries = same_entry(track, track.support[0], entry_rules, recovered=detection)
                    if entries:
                        recovered.append((identifier, index, entries))
            for identifier, index, entries in unique_pairs(recovered):
                track = self.tracks[identifier]
                track.states = entries
                update_detection(track, detections[index], stamp, colors.get(index))
                used_tracks.add(identifier)
                used_boxes.add(index)
                updated[index] = track
            self.record_support(detections, weak, entry_rules, used_tracks, stamp)
        for index, detection in enumerate(detections):
            if index not in used_boxes:
                track = Track(uuid4().hex, detection, stamp, stamp, color=colors.get(index))
                self.tracks[track.id] = updated[index] = track
        return list(updated.values())

    def record_support(self, detections, weak, rules, used_tracks, stamp):
        proposals = []
        for index, detection in enumerate(weak):
            if detection.label != "person" or any(
                    hit.label == "person" and overlap(hit.box, detection.box) >= .15 for hit in detections):
                continue
            for identifier, track in self.tracks.items():
                if (identifier not in used_tracks and track.detection.label == "person"
                        and 0 < stamp - track.last <= min(1., self.max_gap)
                        and overlap(track.detection.box, detection.box) >= .50
                        and same_entry(track, detection, rules)):
                    proposals.append((identifier, index, None))
        for identifier, index, _ in unique_pairs(proposals):
            self.tracks[identifier].support = (weak[index], stamp)


def tripwire(rule, state, detection, stamp):
    a, b = rule["points"]
    anchor = detection.anchor
    length = math.dist(a, b)
    signed = cross(a, b, anchor)
    buffer = .012
    if rule.get("scaleCrossingBuffer"):
        left, top, right, bottom = detection.box
        # Measure the box perpendicular to the wire in normalized image coordinates.
        span = (abs(b[1] - a[1]) * (right - left) + abs(b[0] - a[0]) * (bottom - top)) / length
        buffer = min(buffer, max(.006, span / 2))
    previous = state.wire_observation
    state.wire_observation = (anchor, signed)
    if state.side and previous is not None:
        point, prior_signed = previous
        if signed * state.side >= 0:
            state.wire_crossing = None
        elif prior_signed * state.side >= 0:
            # Record the intersection where the observed path changes sides,
            # even when both observations are inside the confirmation buffer.
            fraction = prior_signed / (prior_signed - signed)
            state.wire_crossing = tuple(old + (new - old) * fraction for old, new in zip(point, anchor))
    if abs(signed) / length < buffer:
        return None
    side = 1 if signed > 0 else -1
    if state.side == 0 or state.side == side:
        state.anchor, state.side = anchor, side
        if state.since is None:
            state.since = stamp
        return None
    crossing = state.wire_crossing
    ready = stamp - state.since >= rule["seconds"] and rule["direction"] in ("either", "positive" if side > 0 else "negative")
    state.anchor, state.side, state.since = anchor, side, stamp
    state.wire_crossing = None
    if not ready or crossing is None:
        return None
    along = sum((axis - start) * (end - start) for axis, start, end in zip(crossing, a, b)) / length ** 2
    return crossing if 0 <= along <= 1 else None


def can_hold_tripwire(state, rule, stamp):
    if state.fired or state.since is None or len(state.tripwire_samples) != 2:
        return False
    (previous_at, _), (last_at, last) = state.tripwire_samples
    return (state.profile == rule.get("profile", "all-day") and state.anchor == last.anchor
            and 0 < last_at - previous_at <= .35 and stamp - last_at <= .75 + 1e-6
            and last_at - state.since + 1e-6 >= rule["seconds"])


def recover_tripwire_tracks(tracks, detections, rule, stamp):
    people = [track for track in tracks if track.detection.label == "person" and stamp - track.last <= .75 + 1e-6]
    candidates = []
    start, end = rule["points"]
    for track in people:
        state = track.states.get(rule["id"])
        if not state or not state.interrupted or not can_hold_tripwire(state, rule, stamp):
            continue
        (previous_at, previous), (last_at, last) = state.tripwire_samples
        scale = (stamp - last_at) / (last_at - previous_at)
        dx, dy = ((axis - old) * scale for axis, old in zip(last.anchor, previous.anchor))
        predicted = tuple(axis + shift for axis, shift in zip(last.box, (dx, dy, dx, dy)))
        prior_side, last_side = (cross(start, end, item.anchor) for item in (previous, last))
        for index, detection in enumerate(detections):
            if (detection.label != "person" or detection.model != last.model
                    or overlap(predicted, detection.box) < .35 or excluded(rule, detection.anchor)):
                continue
            next_side = cross(start, end, detection.anchor)
            if (last_side * next_side >= 0 or (last_side - prior_side) * (next_side - last_side) <= 0
                    or any(other is not track and overlap(other.detection.box, detection.box) >= .35 for other in people)
                    or any(segments_intersect(last.anchor, detection.anchor, a, b)
                           for ring in rule.get("exclusions", ()) for a, b in zip(ring, ring[1:] + ring[:1]))):
                continue
            candidates.append((track.id, index, None))
    return {identifier: detections[index] for identifier, index, _ in unique_pairs(candidates)}


def near_zone_edge(points, detection, frame_size):
    width, height = frame_size
    x, y = detection.anchor
    box_height = (detection.box[3] - detection.box[1]) * height
    margin = min(.05 * box_height, .01 * min(width, height))
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        dx, dy = (bx - ax) * width, (by - ay) * height
        px, py = (x - ax) * width, (y - ay) * height
        length_squared = dx * dx + dy * dy
        projection = min(1., max(0., (px * dx + py * dy) / length_squared)) if length_squared else 0.
        if math.hypot(px - projection * dx, py - projection * dy) <= margin:
            return True
    return False


def in_region(rule, state, detection, stamp, missed_sample, frame_size):
    anchor = detection.anchor
    if not contains(rule["points"], anchor):
        state.reset_confirmation()
        # Keep a reported entry latched through small shifts of the box at the boundary.
        state.fired = state.fired and rule["kind"] == "zone-entry" and near_zone_edge(rule["points"], detection, frame_size)
        return False
    if state.since is None or (rule["kind"] == "restricted-stop" and
            (missed_sample or math.dist(state.anchor, anchor) > .018 or overlap(state.reference_box, detection.box) < .55)):
        # The box overlap limits drift relative to the size of a distant vehicle.
        state.anchor, state.since = anchor, stamp
        state.reference_box = detection.box
    return stamp - state.since >= rule["seconds"]


def vehicle_displacement(detection, reference, frame_size, fraction, minimum_pixels):
    reference_anchor = detection_anchor(reference, "vehicle")
    extent = reference[2] - reference[0], reference[3] - reference[1]
    return any(abs(current - previous) > max(part * fraction, minimum_pixels / pixels)
               for current, previous, part, pixels in zip(detection.anchor, reference_anchor, extent, frame_size))


def moved_then_stopped(rule, state, detection, stamp, missed_sample, frame_size):
    if missed_sample:
        state.reset_confirmation()
    if state.approach_box is None:
        state.approach_box = detection.box
    elif not state.moved:
        state.moved = vehicle_displacement(detection, state.approach_box, frame_size, .5, 3)
    if not contains(rule["points"], detection.anchor):
        state.since = state.reference_box = None
        state.fired = state.fired and near_zone_edge(rule["points"], detection, frame_size)
        return False
    if not state.moved or state.fired:
        return False
    if state.since is None or vehicle_displacement(detection, state.reference_box, frame_size, .07, 1):
        state.since, state.reference_box = stamp, detection.box
    return stamp - state.since + 1e-6 >= rule["seconds"]


def reconnects_dwell(state, anchor, box, elapsed):
    distance = math.dist(state.anchor, anchor)
    if distance <= .05:
        return True
    if distance > .12 or overlap(state.reference_box, box) < .15:
        return False
    dx, dy = (axis * elapsed for axis in state.dwell_velocity)
    projected = tuple(axis + delta for axis, delta in zip(state.reference_box, (dx, dy, dx, dy)))
    return overlap(projected, box) >= .30


def dwell_gap_exceeded(elapsed, limit, previous_sequence, sequence, frame_period):
    if elapsed <= limit + 1e-6:
        return False
    if previous_sequence is None or sequence is None or frame_period is None:
        return True
    nominal = (sequence - previous_sequence) * frame_period
    # A bounded arrival-time overrun can still represent the allowed number of decoded slots.
    jitter = min(.05, frame_period / 2)
    return not (0 < nominal <= limit + 1e-6 and elapsed <= nominal + jitter + 1e-6)


def dwell(rule, state, track, stamp, missed_sample, sequence, frame_period):
    anchor, box = track.detection.anchor, track.detection.box
    if state.last_inside is not None:
        elapsed = stamp - state.last_inside
        if state.interrupted or missed_sample:
            # Use motion from the last qualifying observation to corroborate a moving track.
            rings = (rule["points"], *rule.get("exclusions", ()))
            if (dwell_gap_exceeded(elapsed, rule.get("dwellGapSeconds", 0), state.last_inside_sequence, sequence, frame_period)
                    or not reconnects_dwell(state, anchor, box, elapsed) or
                    any(segments_intersect(state.anchor, anchor, a, b)
                        for ring in rings for a, b in zip(ring, ring[1:] + ring[:1]))):
                state.observed = 0.
        else:
            state.observed += elapsed
    state.anchor, state.last_inside, state.interrupted = anchor, stamp, False
    state.last_inside_sequence = sequence
    state.reference_box, state.dwell_velocity = box, track.velocity
    return state.observed + 1e-6 >= rule["seconds"]


@dataclass
class DockOccupancy:
    profile: str
    state: str = "unknown"
    candidate: str | None = None
    candidate_since: float = 0.
    changed_at: float | None = None
    arrival_event_id: str | None = None

    def sample(self, occupied, stamp, seconds, missed_sample):
        candidate = "occupied" if occupied else "vacant"
        if candidate == self.state:
            self.candidate = None
            return False
        if missed_sample or candidate != self.candidate:
            self.candidate, self.candidate_since = candidate, stamp
            return False
        if stamp - self.candidate_since + 1e-6 < (seconds if occupied else 5.):
            return False
        arrival = occupied and self.state == "vacant"
        self.state, self.changed_at = candidate, stamp
        self.arrival_event_id = None
        self.candidate = None
        return arrival

    def snapshot(self, rule_id, stamp, occurred_at):
        result = {"ruleId": rule_id, "state": self.state, "sampledAt": round(occurred_at, 3)}
        if self.changed_at is not None:
            result["changedAt"] = round(occurred_at - (stamp - self.changed_at), 3)
        if self.arrival_event_id:
            result["arrivalEventId"] = self.arrival_event_id
        return result


class Analytics:
    def __init__(self, max_gap, max_sample_interval=None, *, person_track_support=False,
                 person_motion_association=False, frame_period=None, person_tripwire_recovery=False,
                 person_color_association=False):
        self.max_gap = max_gap
        self.max_sample_interval = max_sample_interval
        self.person_track_support = person_track_support
        self.person_motion_association = person_motion_association
        self.person_tripwire_recovery = person_tripwire_recovery
        self.person_color_association = person_color_association
        self.frame_period = frame_period
        self.version = None
        self.tracker = Tracker(max_gap)
        self.last = None
        self.confirming_vehicle_stop = False
        self.dock_occupancy = {}

    def sample(self, detections, policy, stamp, frame_size, missed_sample=False, weak=(), *,
               source_sequence=None, person_model_changed=False, colors=None):
        if policy["version"] != self.version or self.last is not None and stamp - self.last > self.max_gap:
            self.version = policy["version"]
            self.tracker = Tracker(self.max_gap)
            self.dock_occupancy.clear()
        if self.max_sample_interval is not None and self.last is not None and stamp - self.last > self.max_sample_interval:
            missed_sample = True
        self.last = stamp
        self.confirming_vehicle_stop = False
        result = []
        dock_rules = {rule["id"]: rule for rule in policy["rules"] if rule.get("trackOccupancy")}
        dock_candidates = {}
        entry_rules = ()
        if self.person_track_support:
            entry_rules = [rule for rule in policy["rules"] if rule["kind"] == "zone-entry" and rule["object"] == "person"]
            if missed_sample or person_model_changed:
                for track in self.tracker.tracks.values():
                    track.support = None
                weak = ()
        tripwire_rules = {rule["id"]: rule for rule in policy["rules"]
                          if self.person_tripwire_recovery and rule["kind"] == "line-crossing" and rule["object"] == "person"}
        keep_tripwire = not missed_sample and not person_model_changed
        recovered = {identifier: recover_tripwire_tracks(self.tracker.tracks.values(), detections, rule, stamp)
                     for identifier, rule in tripwire_rules.items()} if keep_tripwire else {}
        updated = self.tracker.update(detections, stamp, weak, entry_rules,
                                      person_motion_association=self.person_motion_association, colors=colors)
        dwell_ids = {rule["id"] for rule in policy["rules"] if rule["kind"] == "zone-dwell"}
        for track in self.tracker.tracks.values():
            if track.last != stamp:
                for identifier, state in track.states.items():
                    if identifier in dwell_ids:
                        state.interrupted = True
                    elif keep_tripwire and identifier in tripwire_rules and can_hold_tripwire(state, tripwire_rules[identifier], stamp):
                        state.interrupted = True
                    else:
                        state.reset_confirmation()
        for track in updated:
            detection = track.detection
            person_transition = person_model_changed and detection.label == "person"
            item = {"object": detection.label, "box": list(detection.box), "score": detection.score,
                    "model": detection.model, "trackId": track.id, "frame": list(frame_size), "policyVersion": self.version}
            for rule in policy["rules"]:
                if rule["object"] != detection.label:
                    continue
                profile = rule.get("profile", "all-day")
                if rule["id"] in dock_rules:
                    anchor = detection.anchor
                    if (contains(rule["points"], anchor) and not excluded(rule, anchor) and qualifies(rule, item)
                            and detection.score > dock_candidates.get(rule["id"], {"score": -1})["score"]):
                        dock_candidates[rule["id"]] = {**item, "ruleId": rule["id"], "profile": profile}
                    continue
                state = track.states.get(rule["id"])
                if state is None or state.profile != profile:
                    state = track.states[rule["id"]] = RuleState(profile=profile)
                crossing = None
                observed = None
                anchor = detection.anchor
                if rule["kind"] == "zone-dwell":
                    if excluded(rule, anchor) or not contains(rule["points"], anchor):
                        state.reset_confirmation()
                        state.fired = False
                        continue
                    if not qualifies(rule, item):
                        state.interrupted = True
                        continue
                    confirmed = dwell(rule, state, track, stamp, missed_sample or person_transition, source_sequence, self.frame_period)
                    matched = confirmed and track.hits > 1 and not state.fired
                    observed = state.observed
                elif excluded(rule, anchor) or not qualifies(rule, item):
                    state.reset_confirmation()
                    continue
                elif rule["kind"] == "line-crossing":
                    can_recover = rule["id"] in tripwire_rules
                    if person_transition or (can_recover and (not keep_tripwire or
                            state.interrupted and recovered.get(rule["id"], {}).get(track.id) is not detection)):
                        state.reset_confirmation()
                    if can_recover:
                        state.interrupted = False
                        state.tripwire_samples = (*state.tripwire_samples[-1:], (stamp, detection))
                    if not state.fired:
                        crossing = tripwire(rule, state, detection, stamp)
                    matched = track.hits > 1 and crossing is not None and not excluded(rule, crossing)
                elif rule.get("requireMovement"):
                    matched = moved_then_stopped(rule, state, detection, stamp, missed_sample, frame_size)
                    if state.since is not None and not state.fired and not matched:
                        self.confirming_vehicle_stop = True
                else:
                    matched = (in_region(rule, state, detection, stamp, missed_sample or person_transition, frame_size)
                               and track.hits > 1 and not state.fired)
                if matched:
                    state.fired = True
                    result.append((rule, {**item, "ruleId": rule["id"], "profile": profile,
                                          **({"crossing": list(crossing)} if crossing is not None else {}),
                                          **({"dwellSeconds": observed} if observed is not None else {}),
                                          **({"movementObserved": True} if rule.get("requireMovement") else {})}))
        for identifier, rule in dock_rules.items():
            profile = rule.get("profile", "all-day")
            dock = self.dock_occupancy.get(identifier)
            if dock is None or dock.profile != profile:
                dock = self.dock_occupancy[identifier] = DockOccupancy(profile)
            candidate = dock_candidates.get(identifier)
            if dock.sample(candidate is not None, stamp, rule["seconds"], missed_sample):
                result.append((rule, candidate))
        return result
