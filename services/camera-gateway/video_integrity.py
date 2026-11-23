"""Small grayscale samples for suspected scene quality issues."""

from hashlib import sha256

WIDTH, HEIGHT = 96, 54
FRAME_BYTES = WIDTH * HEIGHT
FILTER = f"fps=2,scale={WIDTH}:{HEIGHT},format=gray"


def inspect_sample(raw, last_digest=None):
    frames = [raw[offset:offset + FRAME_BYTES] for offset in range(0, len(raw) - FRAME_BYTES + 1, FRAME_BYTES)]
    if not frames:
        return None, None
    frame = frames[-1]
    digest = sha256(frame).digest()
    mean = sum(frame) / FRAME_BYTES
    if mean < 25 and sum(pixel < 35 for pixel in frame) > FRAME_BYTES * .9:
        return "dark", digest
    variance = sum((pixel - mean) ** 2 for pixel in frame) / FRAME_BYTES
    if variance < 14:
        return "obstructed", digest
    if last_digest == digest and len(frames) >= 2 and all(other == frame for other in frames):
        return "frozen", digest
    horizontal = sum(abs(frame[index] - frame[index - 1]) for index in range(1, FRAME_BYTES) if index % WIDTH)
    vertical = sum(abs(frame[index] - frame[index - WIDTH]) for index in range(WIDTH, FRAME_BYTES))
    return ("defocused" if (horizontal + vertical) / (FRAME_BYTES * 2) < 1.1 else "clear"), digest
