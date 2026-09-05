"""Explicit small-member HTTP-range audit; no dataset training or redistribution."""

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

import numpy as np


def main():
    from remotezip import RemoteZip

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must be new")
    revision = "6573175e5b888df73a8eaa7076710b95c90c52ed"
    url = f"https://huggingface.co/datasets/HaoyZhou/bearinguav/resolve/{revision}/Bearing_UAV_90K.zip?download=true"
    members = {
        "metadata": "Bearing_UAV_90K/c1_254k_37bc_b15_s1_v3d/metadata/metadata.csv",
        "query_geometry": "Bearing_UAV_90K/cityc/uav_254k_37bc_b15_s100/blk_0_0_s_083_v3d.json",
        "map_geometry": "Bearing_UAV_90K/city_rsi/1.2897673873873876_103.84197619336068_1791.95_1024_1024_4326_city.json",
    }
    with RemoteZip(url, timeout=30) as archive:
        data = {}
        for key, name in members.items():
            if archive.getinfo(name).file_size > 1024 * 1024:
                raise ValueError("Unexpectedly large audit member")
            data[key] = archive.read(name)
    rows = list(csv.DictReader(io.StringIO(data["metadata"].decode("utf-8-sig"))))
    for row in rows:
        for role, suffix in zip(("p1", "p2", "p3", "p4"), ("00", "10", "01", "11")):
            if not row[f"{role}_path"].endswith(f"block_{row['block_x']}_{row['block_y']}_base_{suffix}.jpg"):
                raise ValueError("Dataset patch ordering differs from audited row-major convention")
        theta = np.deg2rad(float(row["theta"]))
        np.testing.assert_allclose([float(row["x_cosa"]), float(row["y_sina"])], [np.cos(theta), np.sin(theta)], atol=1e-12)
        np.testing.assert_allclose([float(row["x_uccs"]), float(row["y_uccs"])],
                                   [float(row["x_norm"]), -float(row["y_norm"])], atol=1e-12)
    query, map_geo = json.loads(data["query_geometry"]), json.loads(data["map_geometry"])
    first = rows[0]
    if not first["target_path"].endswith("blk_0_0_s_083_v3d.jpg"):
        raise ValueError("Scale-check sample changed")
    pixel = np.array([(query["lng"] - map_geo["left_mid"][1]) / map_geo["lng_per_pixel"],
                      (map_geo["top_mid"][0] - query["lat"]) / map_geo["lat_per_pixel"]])
    expected = np.array([float(first["x_norm"]), float(first["y_norm"])])
    calculated = (pixel - 256) / 128
    np.testing.assert_allclose(calculated, expected, atol=1e-4, rtol=0)
    report = {"schema_version": "vision_nav_bearinguav_coordinate_audit_v1", "dataset_revision": revision,
        "members": {key: {"path": members[key], "sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)} for key, value in data.items()},
        "metadata_rows_checked": len(rows), "patch_suffix_order": ["00", "10", "01", "11"],
        "position_axes": "right_down", "direction_axes": "cos_theta_sin_theta_cartesian_not_PX4_yaw",
        "block_size_px": 512, "normalization_unit_px": 128,
        "georef_label_check": {"calculated": calculated.tolist(), "metadata": expected.tolist(),
                              "absolute_error": abs(calculated - expected).tolist()},
        "full_archive_sha256_verified": False, "small_members_zip_crc_verified": True,
        "scope": "225-row-metadata-order-and-direction-check_one-georef-scale-check_not_accuracy",
        "commercial_dataset_rights_verified": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
