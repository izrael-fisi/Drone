"""Capture a clock-bounded ONVIF H.264 replay via GStreamer."""

import json
import struct
import sys
import resource
from urllib.parse import urlsplit

from onvif_archive import clock_range

NTP_EPOCH = 2_208_988_800


def replay_timestamp(packet, event):
    if len(packet) < 24 or packet[0] >> 6 != 2 or not packet[0] & 0x10:
        return None
    index = 12 + 4 * (packet[0] & 15)
    if len(packet) < index + 16:
        return None
    extension, length, seconds, fraction = struct.unpack_from("!HHII", packet, index)
    if extension != 0xABAC or length < 3:
        return None
    era = round((event + NTP_EPOCH - seconds) / 2**32)
    return seconds + era * 2**32 - NTP_EPOCH + fraction / 2**32, packet[index + 12]


def capture(settings, output):
    import gi
    gi.require_version("Gst", "1.0")
    gi.require_version("GstRtsp", "1.0")
    from gi.repository import Gst, GstRtsp

    Gst.init(None)
    pipeline = Gst.parse_launch("rtspsrc name=source onvif-mode=true onvif-rate-control=false protocols=tcp latency=150 "
                                "rtph264depay name=depay ! h264parse ! matroskamux ! filesink name=output")
    source = pipeline.get_by_name("source")
    depay = pipeline.get_by_name("depay").get_static_pad("sink")
    pipeline.get_by_name("output").set_property("location", output)
    source.set_property("location", settings["uri"])
    if settings["username"]:
        source.set_property("user-id", settings["username"])
        source.set_property("user-pw", settings["password"])
    event, start, end = (settings[key] for key in ("event", "start", "end"))
    camera_host = urlsplit(settings["uri"]).hostname
    times = {"start": None, "end": None, "gap": False}

    def request(_, message):
        result, method, uri, _version = message.parse_request()
        if result != GstRtsp.RTSPResult.OK or (uri != "*" and urlsplit(uri).hostname != camera_host):
            return False
        if method == GstRtsp.RTSPMethod.PLAY:
            message.remove_header(GstRtsp.RTSPHeaderField.RANGE, -1)
            message.add_header(GstRtsp.RTSPHeaderField.RANGE, clock_range(start, end))
        return True

    def select(_, __, caps):
        return caps.get_size() > 0 and caps.get_structure(0).get_string("encoding-name") == "H264"

    def inspect(_, info):
        buffer = info.get_buffer()
        verified = replay_timestamp(buffer.extract_dup(0, min(128, buffer.get_size())), event) if buffer else None
        if verified:
            stamp, flags = verified
            if stamp > end + .5 or stamp < start - 2:
                return Gst.PadProbeReturn.DROP
            if times["start"] is None and flags & 0x80:
                times["start"] = stamp
            if times["start"] is not None:
                if times["end"] is not None and times["end"] < event < stamp and stamp - times["end"] > 1:
                    times["gap"] = True
                times["end"] = max(times["end"] or stamp, stamp)
        return Gst.PadProbeReturn.OK if times["start"] is not None else Gst.PadProbeReturn.DROP

    def attach(_, pad):
        caps = pad.get_current_caps() or pad.query_caps(None)
        if not depay.is_linked() and select(None, None, caps):
            pad.add_probe(Gst.PadProbeType.BUFFER, inspect)
            pad.link(depay)

    source.connect("before-send", request)
    source.connect("select-stream", select)
    source.connect("pad-added", attach)
    bus = pipeline.get_bus()
    messages = Gst.MessageType.EOS | Gst.MessageType.ERROR
    try:
        pipeline.set_state(Gst.State.PLAYING)
        result = bus.timed_pop_filtered(20 * Gst.SECOND, messages)
        if result is None and times["end"] is not None and times["end"] > event:
            pipeline.send_event(Gst.Event.new_eos())
            result = bus.timed_pop_filtered(2 * Gst.SECOND, messages)
        if result is None or result.type != Gst.MessageType.EOS or times["start"] is None or times["end"] is None \
                or times["gap"] or not times["start"] <= event < times["end"]:
            raise ValueError("Could not verify H.264 replay covers the requested event time.")
        return {"start": times["start"], "end": times["end"]}
    finally:
        pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (24 * 1024 * 1024, 24 * 1024 * 1024))
        print(json.dumps(capture(json.load(sys.stdin), sys.argv[1])))
    except (ValueError, ImportError, AttributeError, RuntimeError):
        sys.exit("Profile G H.264 capture or clock verification unavailable.")
