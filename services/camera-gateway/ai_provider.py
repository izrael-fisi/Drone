"""Bounded inference adapters used by the site gateway's optional AI jobs."""

from base64 import b64encode
import json
from pathlib import Path
import re
import ssl
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from onvif import CameraError, NoRedirect

BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
LOCAL_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
MAX_RESPONSE = 64 * 1024


class Bedrock:
    name = "bedrock"

    def __init__(self, settings):
        region = settings.get("region", "us-east-1")
        if region not in ("us-east-1", "us-east-2", "us-west-1", "us-west-2") or settings.get("approveUsGeography") is not True:
            raise CameraError("Approve US geographic inference and select a supported AWS region.")
        try:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:
            raise CameraError("Install the optional gateway AI requirements for Bedrock.") from None
        self.model = BEDROCK_MODEL
        self.errors = BotoCoreError, ClientError
        self.client = boto3.client("bedrock-runtime", region_name=region, config=Config(
            connect_timeout=3, read_timeout=24, retries={"total_max_attempts": 1}))

    def generate(self, instructions, text, frames, schema):
        content = [{"text": text}]
        for frame in frames:
            content.extend(({"text": json.dumps(frame["reference"])},
                            {"image": {"format": "jpeg", "source": {"bytes": frame["jpeg"]}}}))
        try:
            reply = self.client.converse(
                modelId=self.model, system=[{"text": instructions}],
                messages=[{"role": "user", "content": content}], inferenceConfig={"maxTokens": 640, "temperature": 0},
                outputConfig={"textFormat": {"type": "json_schema", "structure": {"jsonSchema": {"schema": json.dumps(schema)}}}})
        except self.errors:
            raise CameraError("The cloud model is unavailable. Try again or continue manually.") from None
        if reply.get("stopReason") != "end_turn":
            raise CameraError("The cloud model could not finish a structured response.")
        response = "".join(part.get("text", "") for part in reply["output"]["message"]["content"])
        return decode(response), reply.get("usage", {})


class LocalModel:
    name = "on-premises"

    def __init__(self, gateway, settings):
        origin = gateway.validate_url(settings.get("localOrigin"), ("http", "https"))
        parsed = urlsplit(origin)
        if (parsed.path not in ("", "/") or parsed.query
                or parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "::1")):
            raise CameraError("Use a loopback HTTP or private HTTPS origin for local inference.")
        self.endpoint = origin.rstrip("/") + "/v1/chat/completions"
        self.model = settings.get("localModel", LOCAL_MODEL)
        if not isinstance(self.model, str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,120}", self.model):
            raise CameraError("Use a valid local inference model name.")
        headers = {"Content-Type": "application/json"}
        if token_file := settings.get("localTokenFile"):
            token = Path(token_file).read_text().strip()
            if not re.fullmatch(r"[!-~]{1,512}", token):
                raise CameraError("Use a valid local inference token file.")
            headers["Authorization"] = "Bearer " + token
        elif parsed.scheme == "https":
            raise CameraError("A private HTTPS model endpoint requires a token file.")
        self.headers = headers
        ca = settings.get("localCa")
        self.opener = build_opener(ProxyHandler({}), NoRedirect(),
                                   HTTPSHandler(context=ssl.create_default_context(cafile=ca)))

    def generate(self, instructions, text, frames, schema):
        parts = [{"type": "text", "text": text}]
        for frame in frames:
            parts.extend(({"type": "text", "text": json.dumps(frame["reference"])},
                          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64encode(frame["jpeg"]).decode()}}))
        payload = {"model": self.model, "temperature": 0, "max_tokens": 640, "stream": False,
                   "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": parts}],
                   "response_format": {"type": "json_schema", "json_schema": {"name": "site_review", "schema": schema, "strict": True}}}
        with self.opener.open(Request(self.endpoint, json.dumps(payload).encode(), self.headers), timeout=24) as reply:
            result = decode(reply.read(MAX_RESPONSE + 1))
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise CameraError("The local model could not finish a structured response.")
        usage = result.get("usage", {})
        return decode(choice["message"]["content"]), {"inputTokens": usage.get("prompt_tokens"),
                                                      "outputTokens": usage.get("completion_tokens")}


def decode(value):
    if not isinstance(value, (str, bytes)) or len(value) > MAX_RESPONSE:
        raise CameraError("The model returned an invalid response.")
    return json.loads(value)
