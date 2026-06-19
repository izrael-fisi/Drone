from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a synthetic map/query pair for smoke tests.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--map-name", default="synthetic_map.png")
    parser.add_argument("--query-name", default="synthetic_query.png")
    return parser.parse_args()


def generate_pair(output_dir: str, map_name: str = "synthetic_map.png", query_name: str = "synthetic_query.png") -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    image = np.zeros((700, 900), dtype=np.uint8)
    cv2.rectangle(image, (80, 80), (230, 220), 255, 4)
    cv2.circle(image, (620, 190), 70, 255, 4)
    cv2.line(image, (90, 520), (780, 430), 255, 5)
    cv2.putText(image, "ORTHO", (270, 590), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 255, 5)
    cv2.drawMarker(image, (430, 330), 255, markerType=cv2.MARKER_CROSS, markerSize=80, thickness=4)

    matrix = np.float32([[1.0, 0.02, 35], [-0.01, 1.0, -28]])
    query = cv2.warpAffine(image, matrix, (900, 700))

    map_path = out / map_name
    query_path = out / query_name
    cv2.imwrite(str(map_path), image)
    cv2.imwrite(str(query_path), query)

    return {
        "map_image": str(map_path),
        "query_image": str(query_path),
    }


def main() -> None:
    args = parse_args()
    result = generate_pair(args.output_dir, args.map_name, args.query_name)
    print(result["map_image"])
    print(result["query_image"])


if __name__ == "__main__":
    main()

