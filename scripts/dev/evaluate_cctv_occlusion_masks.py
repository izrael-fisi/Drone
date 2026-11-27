"""Test whether prompted masks corroborate a reviewed pair of pedestrian crossings."""

import argparse
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from evaluate_cctv_occlusion_motion import body_samples, crossing_point, inside, replay
from prepare_cctv_analytics_demo import checksum


def write_frames(video, reference, start, directory):
    from PIL import Image

    width, height = reference["frameSize"]
    fps = reference["sampleFps"]
    end = reference["liveWindowEndSeconds"]
    left, top, right, bottom = reference["motionRegion"]
    count = 0
    for stamp, frame in replay(str(video), width, height, fps):
        if stamp < start:
            continue
        if stamp >= end:
            break
        if not math.isclose(stamp, start + count / fps):
            raise ValueError("Use a recording with continuous frames at the review sampling rate.")
        Image.fromarray(frame[top:bottom, left:right]).save(directory / f"{count:05d}.jpg", quality=100, subsampling=0)
        count += 1
    if count != math.ceil((end - start) * fps):
        raise ValueError("The recording must cover the entire reviewed crossing window.")


def mask_points(mask, origin):
    import numpy as np

    y, x = np.nonzero(mask)
    if not y.size:
        return None
    bottom = np.percentile(y, 99)
    return ((float(np.median(x[y >= bottom - 4]) + origin[0]), float(bottom + origin[1])),
            int(y.max() - y.min() + 1))


def includes_head(mask, point, origin):
    import numpy as np

    x, y = (int(round(axis - offset)) for axis, offset in zip(point, origin))
    if not (0 <= x < mask.shape[1] and 0 <= y < mask.shape[0]):
        return False
    return bool(np.any(mask[max(0, y - 3):y + 4, max(0, x - 3):x + 4]))


def evaluate(reference, video, trace, checkpoint, device, threads, duplicate_seed):
    import numpy as np
    import torch
    from sam2.build_sam import build_sam2_video_predictor

    start, _ = reference["bodySeedSeconds"]
    region = reference["bodySeedRegion"]
    seeds = sorted((box for box in body_samples(trace, reference).get(start, ()) if inside(box, region)),
                   key=lambda box: box[1])
    if len(seeds) != 2:
        raise ValueError("The seed sample must contain two observed person boxes.")
    if duplicate_seed:
        seeds[1] = seeds[0]
    fps = reference["sampleFps"]
    head_labels = {float(stamp): points for stamp, points in reference["headCenters"].items()}
    origin = reference["motionRegion"][:2]
    offsets = np.tile(origin, 2)
    torch.set_num_threads(threads)
    predictor = build_sam2_video_predictor("configs/sam2.1/sam2.1_hiera_t.yaml", str(checkpoint),
                                        device=device, apply_postprocessing=False)
    with TemporaryDirectory(prefix="proxigo-occlusion-masks-") as directory, torch.inference_mode():
        write_frames(video, reference, start, Path(directory))
        began = perf_counter()
        state = predictor.init_state(directory, offload_state_to_cpu=True, offload_video_to_cpu=True)
        for ident, box in enumerate(seeds):
            predictor.add_new_points_or_box(state, frame_idx=0, obj_id=ident, box=np.asarray(box) - offsets)

        initial = []
        review, confirmations = [], []
        previous = None
        confirmed = None
        for index, ids, logits in predictor.propagate_in_video(state):
            stamp = start + index / fps
            if ids != [0, 1]:
                raise ValueError("The predictor must preserve both seed identifiers.")
            masks = (logits[:, 0] > 0).cpu().numpy()
            footprints = [mask_points(mask, origin) for mask in masks]
            intersection = int(np.count_nonzero(masks[0] & masks[1]))
            union = int(np.count_nonzero(masks[0] | masks[1]))
            separate = bool(union and intersection / union <= .20 and all(footprints))
            if index == 0:
                initial = footprints if separate else []
            if stamp in head_labels:
                heads = head_labels[stamp]
                assigned = [includes_head(masks[i], head, origin) for i, head in enumerate(heads)]
                exclusive = [present and not includes_head(masks[1 - i], head, origin)
                             for i, (present, head) in enumerate(zip(assigned, heads))]
                review.append({"seconds": stamp, "assigned": sum(assigned), "exclusive": sum(exclusive)})
            crossings = []
            if initial and separate and all(now[1] >= .65 * seed[1] for seed, now in zip(initial, footprints)):
                crossings = [crossing_point(seed[0], now[0], reference["line"], reference["direction"])
                             for seed, now in zip(initial, footprints)]
            if crossings and all(crossings):
                confirmations.append({"seconds": stamp, "crossings": crossings, "maskIoU": round(intersection / union, 4)})
                if confirmed is None and previous == index - 1:
                    confirmed = {"recordingSeconds": stamp, "processingSeconds": round(perf_counter() - began, 2)}
                previous = index
            else:
                previous = None
    return {"source": reference["source"], "bodyModel": reference["bodyModel"], "device": device,
            "duplicateSeed": duplicate_seed, "reviewedHeads": 2 * len(review),
            "headsInsideAssignedMask": sum(frame["assigned"] for frame in review),
            "headsAssignedExclusively": sum(frame["exclusive"] for frame in review),
            "pairedMaskObservations": confirmations, "confirmation": confirmed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--body-trace", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True, help="Local Meta SAM 2.1 Hiera Tiny checkpoint")
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--device", choices=("cpu", "mps", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--duplicate-seed", action="store_true", help="Negative control using the first prompt twice")
    args = parser.parse_args()
    try:
        reference = json.loads(args.reference.read_text())
        if checksum(args.video) != reference["sourceSha256"]:
            raise ValueError("The recording SHA-256 does not match the reviewed source.")
        if checksum(args.checkpoint) != args.checkpoint_sha256:
            raise ValueError("The model SHA-256 does not match the approved checkpoint.")
        result = evaluate(reference, args.video, args.body_trace, args.checkpoint, args.device, args.threads,
                          args.duplicate_seed)
    except (ImportError, OSError, RuntimeError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
