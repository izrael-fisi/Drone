"""Torch-free antialiased retrieval input; reference checks run during export."""

from pathlib import Path

import numpy as np

from vision_nav.training_capture import sha256_file


ANTIALIAS_ID = "pillow_float_bilinear_rgb_imagenet_nchw_v1"
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
PREPROCESS_ATOL = 2e-4
PREPROCESS_RTOL = 1e-5


def antialias_contract(image_size: int) -> dict:
    return {"id": ANTIALIAS_ID, "image_size": [image_size, image_size],
            "color_space": "rgb", "layout": "nchw", "value_range": "zero_one", "dtype": "float32",
            "mean": list(MEAN), "std": list(STD), "normalize_before_resize": True,
            "resample": "bilinear_antialias_float32", "source_sha256": sha256_file(Path(__file__))}


def antialias_image(bgr: np.ndarray, image_size: int) -> np.ndarray:
    from PIL import Image

    rgb = np.ascontiguousarray(bgr[:, :, ::-1].transpose(2, 0, 1), dtype=np.float32) / np.float32(255)
    rgb = (rgb - np.asarray(MEAN, np.float32)[:, None, None]) / np.asarray(STD, np.float32)[:, None, None]
    # Float-mode channels avoid the uint8 rounding of an RGB Pillow resize.
    channels = [np.asarray(Image.fromarray(channel).resize((image_size, image_size),
                Image.Resampling.BILINEAR), dtype=np.float32) for channel in rgb]
    return np.ascontiguousarray(np.stack(channels)[None])


def official_reference_image(bgr: np.ndarray, image_size: int) -> np.ndarray:
    """MegaLoc README tensor pipeline, used only in the research environment."""
    import torchvision.transforms as transforms

    transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize(MEAN, STD),
                                    transforms.Resize([image_size, image_size], antialias=True)])
    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    return transform(rgb).numpy()[None]


def preprocessing_parity(expected: np.ndarray, actual: np.ndarray) -> dict:
    if (expected.shape != actual.shape or expected.dtype != np.float32 or actual.dtype != np.float32
            or not np.isfinite(expected).all() or not np.isfinite(actual).all() or not expected.size):
        raise ValueError("Invalid preprocessing parity tensors")
    return {"passed": bool(np.allclose(expected, actual, atol=PREPROCESS_ATOL, rtol=PREPROCESS_RTOL)),
            "atol": PREPROCESS_ATOL, "rtol": PREPROCESS_RTOL,
            "max_absolute_error": float(np.max(np.abs(expected - actual)))}
