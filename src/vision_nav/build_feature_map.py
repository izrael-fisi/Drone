from __future__ import annotations

import argparse
import json
from pathlib import Path

from vision_nav.features import FeatureMethod, extract_features, load_gray_image, save_feature_index
from vision_nav.georef import build_georef_from_cli


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a feature index for a map image.")
    parser.add_argument("--map-image", required=True, help="Path to orthophoto/map image.")
    parser.add_argument("--output", required=True, help="Output .npz feature index path.")
    parser.add_argument(
        "--method",
        choices=["orb", "akaze", "sift"],
        default="orb",
        help="Feature extraction method.",
    )
    parser.add_argument("--max-features", type=int, default=3000)
    parser.add_argument("--metadata-json", help="Optional JSON metadata output path.")
    parser.add_argument("--origin-lat", type=float, help="Latitude at origin pixel.")
    parser.add_argument("--origin-lon", type=float, help="Longitude at origin pixel.")
    parser.add_argument("--gsd-m", type=float, help="Ground sample distance in meters per pixel.")
    parser.add_argument("--origin-pixel-x", type=float, default=0.0)
    parser.add_argument("--origin-pixel-y", type=float, default=0.0)
    parser.add_argument(
        "--rotation-deg",
        type=float,
        default=0.0,
        help="Counter-clockwise rotation of north-up image axes in local ENU.",
    )
    return parser.parse_args()


def build_feature_map(
    map_image: str,
    output: str,
    method: FeatureMethod = "orb",
    max_features: int = 3000,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
    gsd_m: float | None = None,
    origin_pixel_x: float = 0.0,
    origin_pixel_y: float = 0.0,
    rotation_deg: float = 0.0,
) -> dict:
    image = load_gray_image(map_image)
    features = extract_features(image, method=method, max_features=max_features)
    georef = build_georef_from_cli(
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        gsd_m=gsd_m,
        origin_pixel_x=origin_pixel_x,
        origin_pixel_y=origin_pixel_y,
        rotation_deg=rotation_deg,
    )
    save_feature_index(output, map_image, image.shape[:2], features, georef=georef)

    result = {
        "map_image": map_image,
        "output": output,
        "method": method,
        "image_shape": list(image.shape[:2]),
        "keypoints": int(features.keypoints_xy.shape[0]),
    }
    if georef:
        result["georef"] = georef.to_dict()
    return result


def main() -> None:
    args = parse_args()
    result = build_feature_map(
        map_image=args.map_image,
        output=args.output,
        method=args.method,
        max_features=args.max_features,
        origin_lat=args.origin_lat,
        origin_lon=args.origin_lon,
        gsd_m=args.gsd_m,
        origin_pixel_x=args.origin_pixel_x,
        origin_pixel_y=args.origin_pixel_y,
        rotation_deg=args.rotation_deg,
    )

    if args.metadata_json:
        Path(args.metadata_json).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
