"""Optional read-only Profile G adapter for an explicitly assigned recording."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from onvif import CameraError, OnvifDevice, SCHEMA, add

SEARCH = "http://www.onvif.org/ver10/search/wsdl"
REPLAY = "http://www.onvif.org/ver10/replay/wsdl"


def profile_g_recordings(path):
    if not path:
        return {}
    recordings = json.loads(path.read_text())
    if not isinstance(recordings, dict) or any(not isinstance(device, str) or not isinstance(token, str) or not 1 <= len(token) <= 200
        for device, token in recordings.items()):
        raise CameraError("Map ONVIF device IDs to their recording tokens in the Profile G configuration.")
    return recordings


class OnvifArchive:
    def __init__(self, recordings, validate_url, capture_python):
        self.recordings, self.validate_url, self.capture_python = recordings, validate_url, capture_python

    def supports(self, stream):
        return bool(stream and stream.get("deviceId") in self.recordings and stream.get("onvifAddress"))

    @staticmethod
    def timestamp(text):
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return stamp.timestamp() if stamp.tzinfo else None
        except (AttributeError, ValueError, OverflowError):
            return None

    def capture(self, stream, event, incoming):
        token = self.recordings[stream["deviceId"]]
        live = urlsplit(stream["url"])
        address = self.validate_url(stream["onvifAddress"], ("http", "https"))
        if live.hostname != urlsplit(address).hostname:
            raise CameraError("The recording service must use the registered camera address.")
        username, password = unquote(live.username or ""), unquote(live.password or "")
        device = OnvifDevice(address, username, password, self.validate_url)
        services = device.services()
        endpoints = {}
        for protocol in (SEARCH, REPLAY):
            if not services.get(protocol):
                raise CameraError("The camera did not advertise Profile G recording search and replay services.")
            endpoint = self.validate_url(services[protocol], ("http", "https"))
            if urlsplit(endpoint).hostname != live.hostname:
                raise CameraError("Recording search and replay must stay on the registered camera.")
            endpoints[protocol] = endpoint
        info = device.request(endpoints[SEARCH], SEARCH, "GetRecordingInformation", lambda node: add(node, SEARCH, "RecordingToken", token))
        earliest, latest = (self.timestamp(info.findtext(f".//{{*}}{name}")) for name in ("EarliestRecording", "LatestRecording"))
        if earliest is None or latest is None or not earliest <= event < latest:
            return None

        def setup(node):
            stream_setup = add(node, REPLAY, "StreamSetup")
            add(stream_setup, SCHEMA, "Stream", "RTP-Unicast")
            add(add(stream_setup, SCHEMA, "Transport"), SCHEMA, "Protocol", "RTSP")
            add(node, REPLAY, "RecordingToken", token)

        result = device.request(endpoints[REPLAY], REPLAY, "GetReplayUri", setup)
        uri = self.validate_url(result.findtext(".//{*}Uri"), ("rtsp",))
        if urlsplit(uri).hostname != live.hostname:
            raise CameraError("The replay URI must stay on the registered camera.")
        left, right = max(earliest, event - 8), min(latest, event + 8)
        helper = Path(__file__).with_name("onvif_capture.py")
        captured = subprocess.run([self.capture_python or sys.executable, str(helper), str(incoming)],
            input=json.dumps({"uri": uri, "username": username, "password": password, "start": left, "end": right, "event": event}),
            capture_output=True, text=True, check=True, timeout=26)
        metadata = json.loads(captured.stdout)
        return metadata["start"], metadata["end"]


def clock_range(start, end):
    def utc(seconds):
        return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"clock={utc(start)}-{utc(end)}"
