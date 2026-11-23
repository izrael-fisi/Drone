"""Anonymous single-camera tracks and deterministic image-space rules."""

from dataclasses import dataclass, field
import math
from uuid import uuid4

from analytics_contract import detection_anchor, excluded, qualifies
from site_geometry import contains, cross


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


@dataclass
class Track:
    id: str
    detection: object
    first: float
    last: float
    velocity: tuple = (0., 0.)
    hits: int = 1
    states: dict = field(default_factory=dict)


class Tracker:
    def __init__(self, max_gap):
        self.max_gap = max_gap
        self.tracks = {}

    def update(self, detections, stamp):
        self.tracks = {identifier: track for identifier, track in self.tracks.items() if stamp - track.last <= self.max_gap}
        matches = []
        for identifier, track in self.tracks.items():
            anchor, elapsed = track.detection.anchor, stamp - track.last
            predicted = tuple(axis + speed * elapsed for axis, speed in zip(anchor, track.velocity))
            for index, detection in enumerate(detections):
                if detection.label != track.detection.label:
                    continue
                distance = math.dist(predicted, detection.anchor)
                iou = overlap(track.detection.box, detection.box)
                if iou >= .15 or distance <= .035 + min(elapsed * .07, .07):
                    matches.append((distance - .12 * iou, identifier, index))
        used_tracks, used_boxes = set(), set()
        updated = {}
        for _, identifier, index in sorted(matches):
            if identifier in used_tracks or index in used_boxes:
                continue
            track, detection = self.tracks[identifier], detections[index]
            elapsed = max(.001, stamp - track.last)
            velocity = tuple(max(-.3, min(.3, (after - before) / elapsed)) for before, after in zip(track.detection.anchor, detection.anchor))
            track.velocity = tuple(.5 * old + .5 * new for old, new in zip(track.velocity, velocity))
            track.detection, track.last, track.hits = detection, stamp, track.hits + 1
            used_tracks.add(identifier)
            used_boxes.add(index)
            updated[index] = track
        for index, detection in enumerate(detections):
            if index not in used_boxes:
                track = Track(uuid4().hex, detection, stamp, stamp)
                self.tracks[track.id] = updated[index] = track
        # Rule confirmation needs consecutive observations even if an anonymous track can reconnect.
        for identifier in self.tracks.keys() - used_tracks:
            track = self.tracks[identifier]
            if track.last != stamp:
                for state in track.states.values():
                    state.anchor = state.since = None
                    state.side = 0
        return list(updated.values())


def tripwire(rule, state, anchor, stamp):
    a, b = rule["points"]
    signed_distance = cross(a, b, anchor) / math.dist(a, b)
    if abs(signed_distance) < .012:
        return None
    side = 1 if signed_distance > 0 else -1
    if state.side == 0 or state.side == side:
        state.anchor, state.side = anchor, side
        if state.since is None:
            state.since = stamp
        return None
    before, after = cross(a, b, state.anchor), cross(a, b, anchor)
    ratio = before / (before - after)
    crossing = tuple(old + (new - old) * ratio for old, new in zip(state.anchor, anchor))
    along = sum((axis - start) * (end - start) for axis, start, end in zip(crossing, a, b)) / math.dist(a, b) ** 2
    ready = 0 <= along <= 1 and stamp - state.since >= rule["seconds"] and rule["direction"] in ("either", "positive" if side > 0 else "negative")
    state.anchor, state.side, state.since = anchor, side, stamp
    return crossing if ready else None


def in_region(rule, state, anchor, stamp):
    if not contains(rule["points"], anchor):
        state.anchor = state.since = None
        state.fired = False
        return False
    if state.since is None or rule["kind"] == "restricted-stop" and math.dist(state.anchor, anchor) > .018:
        state.anchor, state.since = anchor, stamp
    return stamp - state.since >= rule["seconds"]


class Analytics:
    def __init__(self, max_gap):
        self.max_gap = max_gap
        self.version = None
        self.tracker = Tracker(max_gap)
        self.last = None

    def sample(self, detections, policy, stamp, frame_size):
        if policy["version"] != self.version or self.last is not None and stamp - self.last > self.max_gap:
            self.version = policy["version"]
            self.tracker = Tracker(self.max_gap)
        self.last = stamp
        result = []
        for track in self.tracker.update(detections, stamp):
            detection = track.detection
            item = {"object": detection.label, "box": list(detection.box), "score": detection.score,
                    "model": detection.model, "trackId": track.id, "frame": list(frame_size), "policyVersion": self.version}
            for rule in policy["rules"]:
                if rule["object"] != detection.label:
                    continue
                profile = rule.get("profile", "all-day")
                state = track.states.get(rule["id"])
                if state is None or state.profile != profile:
                    state = track.states[rule["id"]] = RuleState(profile=profile)
                if excluded(rule, detection.anchor) or not qualifies(rule, item):
                    state.anchor = state.since = None
                    state.side = 0
                    continue
                crossing = None
                if rule["kind"] == "line-crossing":
                    if not state.fired:
                        crossing = tripwire(rule, state, detection.anchor, stamp)
                    matched = track.hits > 1 and crossing is not None and not excluded(rule, crossing)
                else:
                    matched = in_region(rule, state, detection.anchor, stamp) and track.hits > 1 and not state.fired
                if matched:
                    state.fired = True
                    result.append((rule, {**item, "ruleId": rule["id"], "profile": profile,
                                          **({"crossing": list(crossing)} if crossing is not None else {})}))
        return result
