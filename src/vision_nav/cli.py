from __future__ import annotations

import argparse
import numpy as np
from .adapters import DeterministicStub
from .estimation.factor_graph import VisionFactorGraph
from .estimation.geometry import GeometryPolicy, RobustGeometry
from .runtime import RuntimePolicy, VisionNavigationRuntime
from .types import CameraModel, FramePacket


def demo() -> None:
    stub = DeterministicStub()
    runtime = VisionNavigationRuntime(stub, stub, stub, stub,
        RobustGeometry(GeometryPolicy(min_inliers=12)), VisionFactorGraph(),
        RuntimePolicy(retrieval_top_k=1, global_recovery_period_s=.1, min_accept_confidence=.1))
    camera = CameraModel(960, 960, 640, 360, 1280, 720)
    for i in range(6):
        nav = runtime.ingest_frame(
            FramePacket(
                i,
                (i + 1) * .1,
                np.zeros((720, 1280, 3), dtype=np.uint8),
                camera,
                calibration_id="demo-camera-v1",
                clock_domain="demo-monotonic",
            )
        )
        print(f"t={nav.timestamp_s:.1f}s mode={nav.mode.value} conf={nav.confidence:.2f} tile={nav.active_tile_id}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="vision-nav")
    parser.add_argument("command", choices=["demo"])
    if parser.parse_args().command == "demo":
        demo()


if __name__ == "__main__":
    main()
