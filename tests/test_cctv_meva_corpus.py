from fractions import Fraction
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/dev"))

from benchmark_cctv_meva_corpus import infer_frames, match_boxes, score_tripwire, tripwire_fixture
from video_analytics.tracking import Detection


class CorpusScoringTests(unittest.TestCase):
    def test_one_detection_cannot_satisfy_two_reference_boxes_and_class_must_agree(self):
        box = (.1, .2, .3, .7)
        references = [("person-1", "person", box), ("person-2", "person", box), ("car", "vehicle", box)]
        predicted = [Detection("person", .9, box, "fixture")]
        self.assertEqual(match_boxes(references, predicted), {"person-1"})

    def test_tripwire_positive_and_reverse_negative_use_the_annotated_path(self):
        rate = Fraction(30)
        case = {"activity": "person_enters_scene_through_structure", "activityFrames": list(range(61))}
        for axis in (0, 1):
            for direction in (-1, 1):
                with self.subTest(axis=axis, direction=direction):
                    anchors = [(frame, .5 + direction * (.24 * frame / 60 - .12)) for frame in range(61)]
                    track = {frame: ((anchor - .05, .30, anchor + .05, .70) if axis == 0 else
                                     (.45, anchor - .30, .55, anchor)) for frame, anchor in anchors}
                    fixture = tripwire_fixture(case, track, rate, (960, 540))
                    self.assertIsNotNone(fixture)
                    frames, _ = fixture
                    detections = {frame: [Detection("person", .9, track[frame], "fixture")] for frame in frames}
                    result = score_tripwire(fixture, track, detections, (960, 540), rate)
                    self.assertTrue(result["matched"])
                    self.assertEqual(result["reverseReports"], 0)
                    self.assertEqual(result["otherReports"], 0)

    def test_stationary_actor_has_no_derived_crossing_fixture(self):
        case = {"activity": "person_enters_scene_through_structure", "activityFrames": list(range(61))}
        track = {frame: (.45, .30, .55, .70) for frame in case["activityFrames"]}
        self.assertIsNone(tripwire_fixture(case, track, Fraction(30), (960, 540)))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for the frame selection test")
    def test_sparse_decoder_preserves_source_frame_numbers(self):
        class FrameReader:
            def infer(self, frame, minimum):
                return int(frame[0, 0, 0])

        frames = [index for index in range(240) if index % 3]
        with TemporaryDirectory() as directory:
            video = Path(directory) / "sequence.mkv"
            pixels = b"".join(bytes([index]) * (8 * 8 * 3) for index in range(240))
            subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "8x8",
                            "-i", "pipe:0", "-c:v", "ffv1", str(video)], input=pixels, check=True, capture_output=True)
            selected = infer_frames(video, frames, (8, 8), FrameReader())
        self.assertEqual(selected, {index: index for index in frames})


if __name__ == "__main__":
    unittest.main()
