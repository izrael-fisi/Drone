"""Launch an isolated test gateway with deterministic Bedrock responses."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "services/camera-gateway"))

import ai_jobs
from ai_provider import BEDROCK_MODEL
from gateway import main


class BedrockFixture:
    name, model = "bedrock", BEDROCK_MODEL

    def __init__(self, settings):
        pass

    def generate(self, instructions, text, frames, schema):
        return {"outcome": "unable-to-verify", "explanation": "The fixture frame is inconclusive."}, {
            "inputTokens": 1100, "outputTokens": 24}

    def review_video(self, video):
        return [{"start_sec": 1, "end_sec": 2, "occupancy": "observed", "summary": "A person crossed the fixture gate."}], {
            "inputVideoSeconds": 6, "outputTokens": 36}


if __name__ == "__main__":
    ai_jobs.Bedrock = BedrockFixture
    main()
