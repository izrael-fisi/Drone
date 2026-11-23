"""Container integration smoke: actual native VIO, synthetic images, no aircraft."""

import argparse
import json

from vision_nav.vio.openvins_replay import make_synthetic
from vision_nav.vio.stream_replay import run
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "checkout", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    capture = args.output / "capture"
    make_synthetic(args.checkout, capture)
    report = run(args.binary, capture, args.output / "vio")
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] and report["position_authority"] is False else 2


if __name__ == "__main__":
    raise SystemExit(main())
