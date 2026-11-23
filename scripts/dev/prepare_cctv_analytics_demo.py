"""Stage public, pinned CCTV benchmark inputs in a directory outside the checkout."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import NamedTemporaryFile

OWL = "https://huggingface.co/google/owlv2-base-patch16-ensemble/resolve/cfd3195ba4ea9592eec887ded089f4c08eff231d/"
HALMSTAD = "https://raw.githubusercontent.com/DroneDetectionThesis/Drone-detection-dataset/442d0c6750708cc221636f7a1fd31cbd8cb0aeb8/Data/Video_V/"
MEDIA = "https://upload.wikimedia.org/wikipedia/commons/"
FILES = {
    "yolox_nano.onnx": ("https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx",
                        "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"),
    "street.jpg": (MEDIA + "e/e9/People_walking_across_urban_street_%28Unsplash%29.jpg",
                   "b94e12757871872db32bad72604f5e0d1fa48baff6e8f9a6e2020964b62a2504"),
    "near-drone.jpg": (MEDIA + "6/6a/Quadcopter_camera_drone_in_flight.jpg",
                       "512ec63254f2fc941e96bf51e8452b61aef44ef95e64bae7aacd77850e980adc"),
    "drone-017.mp4": (HALMSTAD + "V_DRONE_017.mp4", "7ea6d6467e005fff7d4e14818bd2c48a78915e9b869de9693550cb017e2f18c4"),
    "bird-001.mp4": (HALMSTAD + "V_BIRD_001.mp4", "062059c958360e552fa475f73ad32431441abc3970939e6a12f464a76d1095bf"),
    "drone-017.mat": (HALMSTAD + "V_DRONE_017_LABELS.mat", "60b7c1254591f29b7820dd994b0b059de7cf8638b8b3f076c6301f0e73f202bc"),
    "owlv2/model.safetensors": (OWL + "model.safetensors", "e1e130b9e404cf91a75ad45644c1da9d7fa5284085eecc864266a6923efb99e7"),
}
for name in ("config.json", "preprocessor_config.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.json", "merges.txt", "added_tokens.json"):
    FILES["owlv2/" + name] = (OWL + name, None)


def checksum(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def retrieve(output, name, url, digest):
    path = output / name
    if path.is_file() and (digest is None or checksum(path) == digest):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as draft:
        temporary = Path(draft.name)
    try:
        subprocess.run(["curl", "-fLSs", "--retry", "2", "--max-time", "240", "-A", "ProxigoCCTVResearch/1.0",
                        url, "-o", str(temporary)], check=True)
        if digest and checksum(temporary) != digest:
            raise ValueError(f"Checksum changed for {name}.")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[2]):
        parser.error("Keep downloaded models and fixtures outside the checkout.")
    if not shutil.which("ffmpeg") or not shutil.which("curl"):
        parser.error("Install FFmpeg and cURL.")
    os.umask(0o077)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for job in [pool.submit(retrieve, output, name, url, digest) for name, (url, digest) in FILES.items()]:
            job.result()
    for name, size, fps in (("street", "640:960", "4"), ("near-drone", "768:512", "2")):
        source, target = output / (name + ".jpg"), output / (name + ".mp4")
        if not target.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-loop", "1", "-i", str(source), "-vf", "scale=" + size, "-r", fps,
                            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)], check=True)
    print(output)


if __name__ == "__main__":
    main()
