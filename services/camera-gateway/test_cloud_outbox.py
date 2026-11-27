import json
from math import ceil
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from analytics_worker import SampleSchedule, model_specs
from ecs_worker import GROUND_MODELS, ground_model, prepare
from sqs_outbox import SqsOutbox
from video_analytics.video import LiveVideo


class CloudWorkerTests(unittest.TestCase):
    def test_secret_becomes_task_scoped_config(self):
        env = {"PROXIGO_CCTV_SECRET": json.dumps({"gatewayToken": "a" * 64,
                                                 "source": "rtsp://192.0.2.12/yard",
                                                 "gateway": "https://analytics.site.example", "liveFrameBufferMs": 350}),
               "PROXIGO_ORGANIZATION_ID": "org-a", "PROXIGO_SITE_ID": "site-a",
               "PROXIGO_CAMERA_ID": "yard", "PROXIGO_CCTV_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/yard"}
        with TemporaryDirectory() as directory:
            config = json.loads(prepare(env, Path(directory) / "private").read_text())
            self.assertNotIn("PROXIGO_CCTV_SECRET", env)
            self.assertEqual(config["sensorId"], "yard")
            self.assertEqual(config["siteId"], "site-a")
            self.assertEqual(config["placement"], "cloud")
            self.assertEqual(config["liveFrameBufferMs"], 350)
            self.assertEqual(config["models"]["ground"]["sha256"], GROUND_MODELS["yolox_nano"])
            self.assertEqual(Path(config["tokenFile"]).read_text(), "a" * 64)
            self.assertNotIn("gatewayToken", config)

    def test_pinned_cloud_image_selects_model_and_inference_rate(self):
        env = {"PROXIGO_ORGANIZATION_ID": "org-a", "PROXIGO_SITE_ID": "site-a", "PROXIGO_CAMERA_ID": "yard",
               "PROXIGO_CCTV_GROUND_MODEL": "yolox_s", "PROXIGO_CCTV_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/yard"}
        secret = json.dumps({"gatewayToken": "a" * 64, "source": "rtsp://192.0.2.12/yard",
                             "gateway": "https://analytics.site.example", "fps": 8, "inferenceFps": 6})
        with TemporaryDirectory() as directory:
            config = json.loads(prepare({**env, "PROXIGO_CCTV_SECRET": secret}, Path(directory) / "private").read_text())
        model = model_specs(config, config["fps"])["ground"]
        self.assertEqual(model["path"], "/models/yolox_s.onnx")
        self.assertEqual(model["sha256"], GROUND_MODELS["yolox_s"])
        self.assertEqual(model["fps"], 6)
        with self.assertRaisesRegex(ValueError, "inference rate"):
            model_specs(config, 4)

    def test_open_vocabulary_model_uses_ground_lane_and_conservative_default_rate(self):
        config = {"models": {"ground": {"kind": "owlv2", "path": "/models/owlv2", "lane": "drone"}}}
        model = model_specs(config, 4)["ground"]
        self.assertEqual(model["lane"], "ground")
        self.assertEqual(model["fps"], .5)

    def test_yolo11_ground_profile_requires_pinned_export(self):
        digest = "1" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            ground_model("yolo11s")
        profile = ground_model("yolo11s", digest)
        model = model_specs({"models": {"ground": profile}}, 6)["ground"]
        self.assertEqual((model["kind"], model["sha256"], model["fps"], model["threads"]), ("yolo11", digest, 4, 2))
        with self.assertRaisesRegex(ValueError, "ground model"):
            model_specs({"models": {"drone": profile}}, 6)
        self.assertEqual(ground_model("yolo11l", digest),
                         {"kind": "yolo11", "path": "/models/yolo11l.onnx", "sha256": digest})
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            ground_model("yolo11l", "invalid")

    def test_yolo11_detail_region_validation(self):
        model = ground_model("yolo11l", "1" * 64)
        config = {"models": {"ground": {**model, "detailRegion": [.5, .25, .8, .75]}}}
        self.assertEqual(model_specs(config, 4)["ground"]["detailRegion"], (.5, .25, .8, .75))
        for invalid in ([.8, .25, .5, .75], [0, 0, .01, .01], [True, .25, .8, .75]):
            config["models"]["ground"]["detailRegion"] = invalid
            with self.assertRaisesRegex(ValueError, "detail region"):
                model_specs(config, 4)

    @unittest.skipIf(sys.platform == "win32", "CoreML caches use POSIX file locks.")
    def test_coreml_cache_is_private_and_scoped_to_the_approved_export(self):
        from video_analytics.detectors import onnx_session

        with TemporaryDirectory() as directory:
            root = Path(directory) / "cache"
            model = {**ground_model("yolo11s", "1" * 64), "provider": "CoreMLExecutionProvider",
                     "coremlCacheDirectory": str(root)}
            options = Mock()
            runtime = SimpleNamespace(__version__="test-ort", disable_telemetry_events=Mock(),
                                      SessionOptions=Mock(return_value=options),
                                      get_available_providers=lambda: ["CoreMLExecutionProvider"], InferenceSession=Mock())
            with (
                patch.dict(sys.modules, {"onnxruntime": runtime}),
                patch("video_analytics.detectors.platform.release", return_value="test-os"),
            ):
                for digest in ("1" * 64, "2" * 64):
                    approved = model_specs({"models": {"ground": {**model, "sha256": digest}}}, 4)["ground"]
                    onnx_session(approved)
                    cache = root / f"test-os-test-ort-{digest}"
                    self.assertEqual(runtime.InferenceSession.call_args.kwargs["providers"],
                                     [("CoreMLExecutionProvider", {"ModelCacheDirectory": str(cache)}), "CPUExecutionProvider"])
                    self.assertTrue((cache / ".load.lock").is_file())
                cache.chmod(0o755)
                with self.assertRaisesRegex(ValueError, "private CoreML cache"):
                    onnx_session(approved)
            for change in ({"coremlCacheDirectory": "relative"}, {"provider": "CPUExecutionProvider"}):
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, "coremlCacheDirectory"):
                    model_specs({"models": {"ground": {**model, **change}}}, 4)

    def test_rule_derived_detail_can_use_an_approved_person_model_from_the_camera_secret(self):
        digest = "1" * 64
        env = {"PROXIGO_CCTV_SECRET": json.dumps({"gatewayToken": "a" * 64, "source": "rtsp://192.0.2.12/yard",
                                                 "gateway": "https://analytics.site.example", "detailFromRules": True,
                                                 "detailPose": True, "detailRegionLimit": 2, "personTripwireRecovery": True,
                                                 "personColorAssociation": True}),
               "PROXIGO_ORGANIZATION_ID": "org-a", "PROXIGO_SITE_ID": "site-a", "PROXIGO_CAMERA_ID": "yard",
               "PROXIGO_CCTV_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/yard",
               "PROXIGO_CCTV_GROUND_MODEL": "yolo11s", "PROXIGO_CCTV_GROUND_MODEL_SHA256": digest,
               "PROXIGO_CCTV_DETAIL_POSE_SHA256": "2" * 64}
        with TemporaryDirectory() as directory:
            config = json.loads(prepare(env, Path(directory) / "private").read_text())
        ground = model_specs(config, 4)["ground"]
        self.assertIs(ground["detailFromRules"], True)
        self.assertEqual(ground["detailRegionLimit"], 2)
        self.assertEqual(ground["detailPose"]["sha256"], "2" * 64)
        self.assertIs(ground.pop("personTripwireRecovery"), True)
        self.assertIs(ground.pop("personColorAssociation"), True)
        for options, reason in (({"detailFromRules": "true"}, "detailFromRules"), ({"kind": "yolox"}, "detailFromRules"),
                                ({"detailRegion": [.2, .2, .6, .6]}, "manual"), ({"detailObjects": ["vehicle"]}, "detail objects"),
                                ({"detailFromRules": False}, "detailRegionLimit"), ({"detailRegionLimit": True}, "detailRegionLimit"),
                                ({"detailRegionLimit": 3}, "detailRegionLimit"),
                                ({"personTripwireRecovery": "true"}, "personTripwireRecovery"),
                                ({"personColorAssociation": "true"}, "personColorAssociation")):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, reason):
                model_specs({"models": {"ground": {**ground, **options}}}, 4)

    def test_person_crop_follows_published_geometry_and_falls_back_for_wide_rules(self):
        from video_analytics.detectors import Yolo11, person_rule_detail_region

        entrance = {"object": "person", "points": [[.22, .49], [.32, .49], [.32, .56], [.22, .56]]}
        driveway = {"object": "vehicle", "points": [[0, 0], [1, 1]]}
        far_door = {"object": "person", "points": [[.86, .66], [.92, .70]]}
        self.assertIsNone(person_rule_detail_region([driveway], 1920, 1072))
        self.assertIsNone(person_rule_detail_region([entrance], 100, 100))
        self.assertEqual(person_rule_detail_region([{"object": "person", "points": [[0, .1], [.1, .2]]}], 640, 360),
                         (0., 0., .175, .305))
        detector = Yolo11.__new__(Yolo11)
        detector.full_frame_id = "yolo11:test:r90"
        detector.detail_region_limit = 1
        detector.detail_policy_version = None
        detector.detail_objects = ("person",)
        detector.detail_detector = None
        detector.detail_pose_fallback = False
        detector.apply_rule_detail({"version": 1, "rules": [entrance, driveway]}, 1920, 1072)
        self.assertEqual(detector.detail_regions, ((.145, .345, .395, .665),))
        model = detector.id
        self.assertRegex(model, r"^yolo11:test:r90:rule-detail:[0-9a-f]{8}$")
        detector.apply_rule_detail({"version": 2, "rules": [entrance, driveway, far_door]}, 1920, 1072)
        self.assertEqual(detector.detail_regions, ())
        self.assertEqual(detector.id, "yolo11:test:r90")
        detector.apply_rule_detail({"version": 3, "rules": [entrance]}, 1920, 1072)
        self.assertEqual(detector.id, model)

        detector.detail_region_limit = 2
        broad_person_rule = {**driveway, "object": "person"}
        detector.apply_rule_detail({"version": 4, "rules": [broad_person_rule, far_door, entrance, far_door]}, 1920, 1072)
        self.assertEqual(detector.detail_regions, ((.785, .515, .995, .805), (.145, .345, .395, .665)))
        split_model = detector.id
        self.assertNotEqual(split_model, model)
        detector.apply_rule_detail({"version": 5, "rules": [entrance, far_door, broad_person_rule]}, 1920, 1072)
        self.assertEqual(detector.id, split_model)
        detector.apply_rule_detail({"version": 6, "rules": [entrance]}, 1920, 1072)
        self.assertEqual(detector.id, model)

    def test_tall_person_tripwire_crop_keeps_room_for_the_approach(self):
        from video_analytics.detectors import person_rule_detail_region

        wire = {"object": "person", "kind": "line-crossing", "points": [[.58, .70], [.58, .91]]}
        for size in [(536, 960), (1072, 1920)]:
            with self.subTest(size=size):
                self.assertEqual(person_rule_detail_region([wire], *size), (.423284, .555, .736716, .94))
        edge = {**wire, "points": [[.98, .70], [.98, .91]]}
        self.assertEqual(person_rule_detail_region([edge], 536, 960), (.686567, .555, 1., .94))
        horizontal = {**wire, "points": [[.40, .70], [.60, .70]]}
        self.assertEqual(person_rule_detail_region([horizontal], 536, 960), (.325, .555, .675, .805))

    def test_yolo11_detail_objects_from_camera_secret(self):
        env = {"PROXIGO_ORGANIZATION_ID": "org-a", "PROXIGO_SITE_ID": "site-a", "PROXIGO_CAMERA_ID": "yard",
               "PROXIGO_CCTV_GROUND_MODEL": "yolo11s", "PROXIGO_CCTV_GROUND_MODEL_SHA256": "1" * 64,
               "PROXIGO_CCTV_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/yard",
               "PROXIGO_CCTV_SECRET": json.dumps({"gatewayToken": "a" * 64, "source": "rtsp://192.0.2.12/yard",
                                                 "gateway": "https://analytics.site.example", "detailRegion": [.2, .3, .7, .8],
                                                 "detailObjects": ["vehicle", "person"]})}
        with TemporaryDirectory() as directory:
            config = json.loads(prepare(env, Path(directory) / "private").read_text())
        self.assertEqual(model_specs(config, 4)["ground"]["detailObjects"], ("person", "vehicle"))
        for invalid in ("vehicle", [], ["drone"], ["vehicle", "vehicle"], [None]):
            config["models"]["ground"]["detailObjects"] = invalid
            with self.subTest(objects=invalid), self.assertRaisesRegex(ValueError, "detail objects"):
                model_specs(config, 4)
        del config["models"]["ground"]["detailRegion"]
        config["models"]["ground"]["detailObjects"] = ["vehicle"]
        with self.assertRaisesRegex(ValueError, "detail objects"):
            model_specs(config, 4)

    def test_yolo11_tracking_options_from_camera_secret(self):
        digest = "1" * 64
        env = {"PROXIGO_ORGANIZATION_ID": "org-a", "PROXIGO_SITE_ID": "site-a", "PROXIGO_CAMERA_ID": "yard",
               "PROXIGO_CCTV_GROUND_MODEL": "yolo11s", "PROXIGO_CCTV_GROUND_MODEL_SHA256": digest,
               "PROXIGO_CCTV_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/yard",
               "PROXIGO_CCTV_SECRET": json.dumps({"gatewayToken": "a" * 64, "source": "rtsp://192.0.2.12/yard",
                                                 "gateway": "https://analytics.site.example", "rotationDegrees": 90,
                                                 "personTrackSupport": True, "personMotionAssociation": True})}
        with TemporaryDirectory() as directory:
            config = json.loads(prepare(env, Path(directory) / "private").read_text())
        self.assertEqual(model_specs(config, 4)["ground"]["rotationDegrees"], 90)
        self.assertIs(model_specs(config, 4)["ground"]["personTrackSupport"], True)
        self.assertIs(model_specs(config, 4)["ground"]["personMotionAssociation"], True)
        for invalid in (True, 90.0, "90", -90, 15, 360):
            with self.subTest(rotation=invalid), self.assertRaisesRegex(ValueError, "rotation"):
                model_specs({"models": {"ground": {**ground_model("yolo11s", digest), "rotationDegrees": invalid}}}, 4)
        with self.assertRaisesRegex(ValueError, "rotation"):
            model_specs({"models": {"ground": {**ground_model("yolox_s"), "rotationDegrees": 90}}}, 4)
        for setting in ("personTrackSupport", "personMotionAssociation"):
            for kind, value in (("yolo11s", "true"), ("yolox_s", True)):
                with self.subTest(setting=setting, kind=kind, value=value), self.assertRaisesRegex(ValueError, setting):
                    model_specs({"models": {"ground": {**ground_model(kind, digest), setting: value}}}, 4)

    def test_pose_detail_model_requires_approved_person_crop(self):
        digest = "1" * 64
        env = {"PROXIGO_ORGANIZATION_ID": "org-a", "PROXIGO_SITE_ID": "site-a", "PROXIGO_CAMERA_ID": "entry",
               "PROXIGO_CCTV_GROUND_MODEL": "yolo11s", "PROXIGO_CCTV_GROUND_MODEL_SHA256": digest,
               "PROXIGO_CCTV_DETAIL_POSE_SHA256": "2" * 64,
               "PROXIGO_CCTV_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/yard",
               "PROXIGO_CCTV_SECRET": json.dumps({"gatewayToken": "a" * 64, "source": "rtsp://192.0.2.12/entry",
                                                 "gateway": "https://analytics.site.example",
                                                 "detailRegion": [.2, .2, .6, .6], "detailPose": True, "detailPoseFallback": True})}
        with TemporaryDirectory() as directory:
            private = Path(directory) / "private"
            for pose_env, expected_name in (({}, "yolo11l-pose"), ({"PROXIGO_CCTV_DETAIL_POSE_MODEL": "yolo11s-pose"}, "yolo11s-pose")):
                with self.subTest(pose_model=expected_name):
                    config = json.loads(prepare({**env, **pose_env}, private).read_text())
                    self.assertEqual(model_specs(config, 4)["ground"]["detailPose"],
                                     {"path": f"/models/{expected_name}.onnx", "sha256": "2" * 64})
                    self.assertIs(model_specs(config, 4)["ground"]["detailPoseFallback"], True)
            with self.assertRaisesRegex(ValueError, "supported YOLO11 pose model"):
                prepare({**env, "PROXIGO_CCTV_DETAIL_POSE_MODEL": "../pose"}, private)
        ground = config["models"]["ground"]
        invalid = ({**ground, "detailObjects": ["vehicle"]}, {**ground, "detailPose": True},
                   {**ground, "detailPose": {"path": "relative.onnx", "sha256": digest}},
                   {**ground, "detailPose": {"path": "/models/pose.onnx", "sha256": "unapproved"}})
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaisesRegex(ValueError, "pose model"):
                model_specs({"models": {"ground": spec}}, 4)
        for spec in ({**ground, "detailPoseFallback": "true"}, {**ground_model("yolo11s", digest), "detailPoseFallback": True}):
            with self.assertRaisesRegex(ValueError, "detailPoseFallback"):
                model_specs({"models": {"ground": spec}}, 4)

    def test_yolo11_rectangular_inputs_and_export_contracts(self):
        import numpy as np
        from video_analytics.detectors import Yolo11View

        spec = {"kind": "yolo11", "path": "/models/fixture.onnx", "sha256": "1" * 64}
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        for pose, person_only, channels in ((False, False, 84), (True, False, 56), (False, True, 5)):
            with self.subTest(channels=channels):
                source = SimpleNamespace(name="image", type="tensor(float)", shape=[1, 3, 640, 1152])
                result = SimpleNamespace(type="tensor(float)", shape=[1, channels, 15120])
                tensor = np.zeros(result.shape, dtype=np.float32)
                tensor[0, :5, 0] = (576, 320, 256, 128, .75)
                if pose:
                    tensor[0, 7, 0] = .99  # A keypoint confidence is separate from the box score.
                session = Mock(get_inputs=Mock(return_value=[source]), get_outputs=Mock(return_value=[result]),
                               run=Mock(return_value=[tensor]))
                with patch("video_analytics.detectors.fingerprint", return_value="1" * 16), \
                        patch("video_analytics.detectors.onnx_session", return_value=session):
                    [found] = Yolo11View(spec, pose=pose, person_only=person_only).detect(frame, .25)
                    self.assertEqual(session.run.call_args.args[1]["image"].shape, (1, 3, 640, 1152))
                    self.assertEqual((found.label, found.score), ("person", .75))
                    np.testing.assert_allclose(found.box, (.3875, .4, .6125, .6), atol=.001)
                    result.shape[2] -= 1
                    with self.assertRaisesRegex(ValueError, "static FP32 YOLO11"):
                        Yolo11View(spec, pose=pose, person_only=person_only)

    def test_approved_person_model_from_camera_secret(self):
        env = {"PROXIGO_ORGANIZATION_ID": "org-a", "PROXIGO_SITE_ID": "site-a", "PROXIGO_CAMERA_ID": "entry",
               "PROXIGO_CCTV_GROUND_MODEL": "yolo11s", "PROXIGO_CCTV_GROUND_MODEL_SHA256": "1" * 64,
               "PROXIGO_CCTV_PERSON_MODEL_SHA256": "2" * 64,
               "PROXIGO_CCTV_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/entry",
               "PROXIGO_CCTV_SECRET": json.dumps({"gatewayToken": "a" * 64, "source": "rtsp://192.0.2.12/entry",
                                                 "gateway": "https://analytics.site.example", "personModel": True})}
        with TemporaryDirectory() as directory:
            config = json.loads(prepare(dict(env), Path(directory) / "private").read_text())
            secret = json.loads(env["PROXIGO_CCTV_SECRET"])
            automatic = json.dumps({**secret, "personModelMode": "monochrome"})
            settings = prepare({**env, "PROXIGO_CCTV_SECRET": automatic}, Path(directory) / "private")
            self.assertEqual(model_specs(json.loads(settings.read_text()), 4)["ground"]["personModelMode"], "monochrome")
            with self.assertRaisesRegex(ValueError, "personModelMode"):
                prepare({**env, "PROXIGO_CCTV_SECRET": json.dumps({**secret, "personModel": False,
                                                                  "personModelMode": "monochrome"})}, Path(directory) / "private")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                prepare({**env, "PROXIGO_CCTV_PERSON_MODEL_SHA256": ""}, Path(directory) / "private")
        model = model_specs(config, 4)["ground"]
        self.assertEqual(model["personModel"], {"path": "/models/yolo11-person.onnx", "sha256": "2" * 64})
        for invalid in ({**model, "kind": "yolox"}, {**model, "personModel": True}, {**model, "personModelMode": "unknown"},
                        {**model, "personModel": {"path": "relative.onnx", "sha256": "2" * 64}}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "personModel"):
                model_specs({"models": {"ground": invalid}}, 4)

    def test_person_specialist_uses_general_model_for_vehicle_rules_only(self):
        import numpy as np
        from video_analytics.detectors import Yolo11
        from video_analytics.tracking import Detection

        detector = Yolo11.__new__(Yolo11)
        detector.id = "yolo11:combined"
        detector.detail_regions = ()
        vehicle = Detection("vehicle", .8, (.1, .2, .3, .4), detector.id)
        person = Detection("person", .7, (.4, .5, .6, .8), "specialist")
        detector.detect = Mock(return_value=[vehicle, person])
        detector.person_detector = Mock(detect=Mock(return_value=[person]))
        frame = np.zeros((64, 64, 3), dtype=np.uint8)

        [found_person] = detector.infer(frame, .25, objects={"person"}, capture_floor=.10)
        self.assertEqual(found_person, Detection("person", .7, person.box, detector.id))
        detector.detect.assert_not_called()
        detector.person_detector.detect.assert_called_once_with(frame, .25, capture_floor=.10)
        detector.person_detector.detect.reset_mock()

        self.assertEqual(detector.infer(frame, .25, objects={"vehicle"}), [vehicle])
        detector.person_detector.detect.assert_not_called()
        self.assertEqual(detector.infer(frame, .25, objects={"person", "vehicle"}), [vehicle, found_person])

    def test_person_model_uses_stable_monochrome_frames_and_returns_to_color(self):
        import numpy as np
        from video_analytics.detectors import Yolo11
        from video_analytics.tracking import Detection

        detector = Yolo11.__new__(Yolo11)
        detector.automatic_person_model = True
        detector.person_model_active = False
        detector.monochrome_samples = 0
        detector.full_frame_id = detector.general_frame_id = "general"
        detector.specialist_frame_id = "general:person:specialist"
        detector.configure_detail(())
        bounds = (.2, .3, .4, .5)
        detector.detect = Mock(return_value=[Detection("person", .8, bounds, "general")])
        detector.person_detector = Mock(detect=Mock(return_value=[Detection("person", .9, bounds, "specialist")]))
        monochrome = np.broadcast_to(np.arange(64, dtype=np.uint8)[None, :, None] + 32, (64, 64, 3)).copy()
        color = monochrome.copy()
        color[:, :, 0] += 30
        blank = np.zeros_like(monochrome)

        for _ in range(3):
            self.assertFalse(detector.select_person_model(blank, False))
        self.assertFalse(detector.select_person_model(monochrome, False))
        self.assertFalse(detector.select_person_model(monochrome, False))
        self.assertFalse(detector.select_person_model(monochrome, True))
        self.assertFalse(detector.select_person_model(monochrome, False))
        self.assertEqual(detector.infer(monochrome, .25, objects={"person"})[0].model, "general")
        detector.person_detector.detect.assert_not_called()
        self.assertTrue(detector.select_person_model(monochrome, False))
        self.assertEqual(detector.infer(monochrome, .25, objects={"person"})[0].model, detector.specialist_frame_id)
        self.assertTrue(detector.select_person_model(color, False))
        self.assertEqual(detector.infer(color, .25, objects={"person"})[0].model, "general")

    def test_yolo11_rotates_frames_and_returns_boxes_to_the_original_coordinates(self):
        import numpy as np
        from video_analytics.detectors import Yolo11
        from video_analytics.tracking import Detection

        frame = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        detector = Yolo11.__new__(Yolo11)
        detector.detail_regions = ()
        detector.id = "yolo11:fixture"
        for angle, predicted_box, corner in (
            (0, (.1, .2, .4, .7), frame[0, 0]),
            (90, (.3, .1, .8, .4), frame[-1, 0]),
            (180, (.6, .3, .9, .8), frame[-1, -1]),
            (270, (.2, .6, .7, .9), frame[0, -1]),
        ):
            with self.subTest(angle=angle):
                detector.rotation = angle
                detector.detect_upright = Mock(return_value=[Detection("person", .9, predicted_box, detector.id)])
                [found] = detector.infer(frame, .25)
                rotated, threshold = detector.detect_upright.call_args.args
                self.assertEqual(threshold, .25)
                self.assertEqual(rotated.shape, (3, 2, 3) if angle % 180 else frame.shape)
                np.testing.assert_array_equal(rotated[0, 0], corner)
                np.testing.assert_allclose(found.box, (.1, .2, .4, .7))

    def test_yolo11_weak_boxes_keep_the_original_strong_box_ranking(self):
        import numpy as np
        from video_analytics.detectors import Yolo11View

        detector = Yolo11View.__new__(Yolo11View)
        detector.pose = False
        detector.rotation = 0
        detector.input_width = detector.input_height = 640
        detector.input = "image"
        detector.id = "fixture"
        values = [.45, .75, .75, .32, .75, .75, .75, .10, .18, .32, .12, .18, .32, .45, .32, .12,
                  .45, .75, .12, .32, .18, .75, .10, .18, .75, .18, .10, .45, .75, .75, .75, .18]
        tensor = np.zeros((1, 84, 8400), dtype=np.float32)
        for index, score in enumerate(values):
            tensor[0, :5, index] = (100 + index, 240, 80, 160, score)
        detector.session = Mock(run=Mock(return_value=[tensor]))
        frame = np.zeros((320, 320, 3), dtype=np.uint8)
        baseline = detector.detect(frame, .25)
        captured = detector.detect(frame, .25, capture_floor=.10)
        self.assertEqual([item for item in captured if item.score >= .25], baseline)

    def test_yolo11_rejects_truncated_crop_boxes_at_internal_edges(self):
        import numpy as np
        from video_analytics.detectors import Yolo11
        from video_analytics.tracking import Detection

        detector = Yolo11.__new__(Yolo11)
        detector.id = "fixture"
        detector.detail_regions = ((.25, .25, 1., .75),)
        detector.detail_objects = ("person",)
        detector.detail_detector = None
        person = Detection("person", .6, (.31, .60, .41, .78), detector.id)
        truncated = Detection("person", .95, (.10, .70, .20, .96), detector.id)
        at_frame_edge = Detection("person", .8, (.90, .20, 1., .50), detector.id)
        detector.detect = Mock(side_effect=[[person], [truncated, at_frame_edge]])

        [detail, original] = detector.infer(np.zeros((200, 200, 3), dtype=np.uint8), .25)

        self.assertEqual(original, person)
        self.assertEqual(detail.score, at_frame_edge.score)
        np.testing.assert_allclose(detail.box, (.925, .35, 1., .50))

    def test_yolo11_detail_boxes_return_to_the_full_frame(self):
        import numpy as np
        from video_analytics.detectors import Yolo11
        from video_analytics.tracking import Detection

        detector = Yolo11.__new__(Yolo11)
        detector.id = "yolo11:fixture"
        detector.detail_regions = ((.5, .25, 1, .75),)
        detector.detail_objects = ("person",)
        detector.detail_detector = None
        person = Detection("person", .6, (.55, .3, .65, .45), detector.id)
        vehicle = Detection("vehicle", .6, (.7, .3, .8, .5), detector.id)
        higher_detail = Detection("person", .9, (.1, .1, .3, .4), detector.id)
        unrelated = Detection("person", .8, (.2, .2, .25, .4), detector.id)
        detector.detect = Mock(side_effect=[[person, vehicle, unrelated], [higher_detail, vehicle]])
        found = detector.infer(np.zeros((200, 200, 3), dtype=np.uint8), .25)
        self.assertIn(vehicle, found)
        self.assertIn(unrelated, found)
        self.assertNotIn(person, found)
        self.assertEqual(len(found), 3)
        np.testing.assert_allclose(found[1].box, person.box)

        detector.detail_objects = ("vehicle",)
        higher_detail_vehicle = Detection("vehicle", .9, (.4, .1, .6, .5), detector.id)
        detector.detect = Mock(side_effect=[[person, vehicle, unrelated], [higher_detail_vehicle, higher_detail]])
        found = detector.infer(np.zeros((200, 200, 3), dtype=np.uint8), .25)
        self.assertEqual(found[:2], [person, unrelated])
        self.assertEqual(len(found), 3)
        self.assertEqual((found[2].label, found[2].score), ("vehicle", .9))
        np.testing.assert_allclose(found[2].box, vehicle.box)

        detector.detail_objects = ("person",)
        detector.detail_detector = Mock(detect=Mock(return_value=[higher_detail]))
        detector.detect = Mock(return_value=[person, vehicle])
        found = detector.infer(np.zeros((200, 200, 3), dtype=np.uint8), .25)
        self.assertEqual(found[0], vehicle)
        self.assertEqual((found[1].model, found[1].score), (detector.id, .9))
        np.testing.assert_allclose(found[1].box, person.box)
        detector.detect.assert_called_once()
        self.assertEqual(detector.detail_detector.detect.call_args.args[0].shape, (100, 100, 3))

    def test_pose_crop_merges_partial_boxes_and_keeps_a_nearby_person(self):
        import numpy as np
        from video_analytics.detectors import Yolo11
        from video_analytics.tracking import Detection, overlap

        detector = Yolo11.__new__(Yolo11)
        detector.id = "yolo11:fixture"
        detector.detail_regions = ((.2, .2, .8, .8),)
        detector.detail_objects = ("person",)
        tall = Detection("person", .79, (.40, .25, .46, .55), detector.id)
        nearby = Detection("person", .65, (.452, .35, .507, .56), detector.id)
        partial = (.421, .39, .455, .549)
        self.assertLess(overlap(tall.box, partial), .45)
        cropped = Detection("person", .55, tuple((axis - .2) / .6 for axis in partial), "pose-model")
        detector.detail_detector = Mock(detect=Mock(return_value=[cropped]))
        detector.detect = Mock(return_value=[tall, nearby])
        result = detector.infer(np.zeros((100, 100, 3), dtype=np.uint8), .25)
        self.assertEqual(result[0], nearby)
        self.assertEqual(len(result), 2)
        np.testing.assert_allclose(result[1].box, partial)

        weak = Detection("person", .12, cropped.box, cropped.model)
        detector.detail_detector.detect = Mock(return_value=[weak])
        both = detector.infer(np.zeros((100, 100, 3), dtype=np.uint8), .25, capture_floor=.10)
        self.assertEqual([item for item in both if item.score >= .25], [tall, nearby])
        self.assertEqual(detector.detail_detector.detect.call_args.kwargs["capture_floor"], .10)

    def test_pose_fallback_only_adds_qualifying_people_in_unfilled_zones(self):
        import numpy as np
        from video_analytics.detectors import Yolo11
        from video_analytics.tracking import Detection

        detector = Yolo11.__new__(Yolo11)
        detector.id = "yolo11:fixture"
        detector.detail_regions = ((.20, .20, .80, .80),)
        detector.detail_objects = ("person",)
        pose = Detection("person", .70, (.40, .22, .70, .58), "pose")
        masked_pose = Detection("person", .85, (.10, .25, .30, .62), "pose")
        detector.detail_detector = Mock(detect=Mock(return_value=[pose, masked_pose]))
        frames = np.zeros((100, 100, 3), dtype=np.uint8)
        rule = {"kind": "zone-dwell", "object": "person", "minimumScore": .60, "minimumPixels": 12,
                "points": [[.25, .25], [.70, .25], [.70, .70], [.25, .70]],
                "exclusions": [[[.25, .50], [.36, .50], [.36, .60], [.25, .60]]]}
        vehicle = Detection("vehicle", .92, (.45, .30, .65, .55), detector.id)
        weak = Detection("person", .46, (.18, .27, .37, .50), detector.id)
        detector.detect = Mock(side_effect=[[vehicle], [weak]])
        found = detector.infer(frames, .25, fallback_rules=[rule])
        self.assertEqual([item.label for item in found], ["vehicle", "person", "person"])
        np.testing.assert_allclose(found[-1].box, [.44, .332, .62, .548])
        self.assertTrue(detector.last_pose_fallback)

        strong = Detection("person", .80, (.41, .27, .63, .50), detector.id)
        detector.detail_detector.detect.reset_mock()
        detector.detect = Mock(side_effect=[[strong], []])
        self.assertEqual(detector.infer(frames, .25, fallback_rules=[rule]), [strong])
        self.assertFalse(detector.last_pose_fallback)
        detector.detail_detector.detect.assert_not_called()

        rule["points"][0] = [.10, .25]
        detector.detect = Mock(return_value=[])
        self.assertEqual(detector.infer(frames, .25, fallback_rules=[rule]), [])
        detector.detail_detector.detect.assert_not_called()

    def test_two_detail_regions_share_full_frame_detections_and_gate_pose_separately(self):
        import numpy as np
        from video_analytics.detectors import Yolo11
        from video_analytics.tracking import Detection

        detector = Yolo11.__new__(Yolo11)
        detector.id = "yolo11:fixture"
        detector.detail_regions = ((.1, .2, .5, .8), (.45, .2, .9, .8))
        detector.detail_objects = ("person",)
        full = Detection("person", .65, (.3, .32, .4, .548), detector.id)
        detail = Detection("person", .70, (.5, .20, .75, .58), detector.id)
        detector.detect = Mock(side_effect=[[full], [detail], []])
        detector.detail_detector = Mock(detect=Mock(return_value=[detail]))
        rule = {"object": "person", "minimumScore": .6, "minimumPixels": 5,
                "points": [[.2, .3], [.4, .3], [.4, .65], [.2, .65]]}
        other = {**rule, "points": [[.6, .3], [.8, .3], [.8, .65], [.6, .65]]}
        found = detector.infer(np.zeros((100, 100, 3), dtype=np.uint8), .25, fallback_rules=[rule, other])

        self.assertEqual(detector.detect.call_count, 3)
        detector.detail_detector.detect.assert_called_once()
        self.assertEqual(detector.detail_detector.detect.call_args.args[0].shape, (60, 45, 3))
        self.assertTrue(detector.last_pose_fallback)
        self.assertEqual(len(found), 2)
        np.testing.assert_allclose(found[0].box, full.box)
        np.testing.assert_allclose(found[1].box, (.675, .32, .7875, .548))

    def test_live_camera_recovers_recent_samples_without_replaying_old_frames(self):
        camera = LiveVideo("rtsp://127.0.0.1/test", 640, 480, 4, Event(), lambda: True, buffer_ms=350)
        camera.frames.extend((sequence, stamp, stamp, None) for sequence, stamp in
                             ((1, 95.), (2, 99.2), (3, 99.8), (4, 100.)))
        with patch("video_analytics.video.time.monotonic", return_value=100.):
            self.assertEqual(len(camera.frames), 3)
            self.assertEqual(camera.get(1, ordered=True)[0], 3)
            self.assertEqual(camera.get(3, ordered=True)[0], 4)
            self.assertEqual(camera.get(1)[0], 4)
        with patch("video_analytics.video.time.monotonic", return_value=101.):
            self.assertEqual(camera.get(1, ordered=True)[0], 4)

    def test_camera_sampling_preserves_cadence_after_skipped_frames(self):
        schedule = SampleSchedule(1., 1.5)
        frames = (1, 2, 3, 4, 5, 6, 7, 11, 12, 13)
        samples = [(frame, missed) for frame in frames if (missed := schedule.take(frame, False)) is not None]
        self.assertEqual(samples, [(1, False), (3, False), (4, False), (6, False), (7, False),
                                   (11, True), (12, False), (13, False)])

    def test_camera_sampling_adds_one_midpoint_during_confirmation(self):
        schedule = SampleSchedule(0., 6.)
        frames = (*range(13), 25, 26, 27, 28, 30)
        samples = [(frame, missed) for frame in frames if (missed := schedule.take(frame, frame >= 1)) is not None]
        self.assertEqual(samples, [(0, False), (3, False), (6, False), (9, False), (12, False),
                                   (25, True), (27, False), (30, False)])
        schedule = SampleSchedule(0., 10 / 3)
        self.assertEqual([frame for frame in range(200) if schedule.take(frame, True) is not None],
                         [ceil(index * 5 / 3) for index in range(120)])

    def test_ecs_vehicle_stop_boost_requires_yolo11_and_headroom(self):
        digest = "1" * 64
        env = {"PROXIGO_ORGANIZATION_ID": "org-a", "PROXIGO_SITE_ID": "site-a", "PROXIGO_CAMERA_ID": "yard",
               "PROXIGO_CCTV_GROUND_MODEL": "yolo11s", "PROXIGO_CCTV_GROUND_MODEL_SHA256": digest,
               "PROXIGO_CCTV_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/yard",
               "PROXIGO_CCTV_SECRET": json.dumps({"gatewayToken": "a" * 64, "source": "rtsp://192.0.2.12/yard",
                                                 "gateway": "https://analytics.site.example", "fps": 8,
                                                 "inferenceFps": 4, "vehicleStopConfirmationBoost": True})}
        with TemporaryDirectory() as directory:
            config = json.loads(prepare(env, Path(directory) / "private").read_text())
        model = model_specs(config, 8)["ground"]
        self.assertIs(model["vehicleStopConfirmationBoost"], True)
        with self.assertRaisesRegex(ValueError, "at least twice"):
            model_specs(config, 6)
        for invalid in ({**model, "vehicleStopConfirmationBoost": 1},
                        {**ground_model("yolox_s"), "vehicleStopConfirmationBoost": True}):
            with self.assertRaisesRegex(ValueError, "vehicleStopConfirmationBoost"):
                model_specs({"models": {"ground": invalid}}, 8)

    def queued(self, staging, client):
        box = SqsOutbox.__new__(SqsOutbox)
        box.queue_url = "https://sqs.us-east-1.amazonaws.com/123456789012/example"
        box.staging, box.client, box.errors, box.pending_limit = staging, client, (OSError,), 10000
        return box

    def test_acknowledged_message_can_be_read_after_worker_replacement(self):
        client = Mock()
        original = self.queued(Mock(), client)
        event = {"id": "stable-event", "sensorId": "yard"}
        self.assertTrue(original.put(event))
        original.staging.put.assert_not_called()
        body = client.send_message.call_args.kwargs["MessageBody"]
        client.receive_message.return_value = {"Messages": [{"ReceiptHandle": "receipt", "Body": body}]}
        replacement = self.queued(Mock(**{"batch.return_value": []}), client)
        [(receipt, encoded)] = replacement.batch()
        self.assertEqual(json.loads(encoded), event)
        client.delete_message.assert_not_called()
        replacement.complete(receipt)
        client.delete_message.assert_called_once_with(QueueUrl=original.queue_url, ReceiptHandle="receipt")

    def test_local_staging_is_retained_until_queue_accepts_it(self):
        staging = Mock(**{"put.return_value": True, "batch.return_value": [(5, '{"id":"stable-event"}')]})
        client = Mock()
        client.send_message.side_effect = OSError()
        box = self.queued(staging, client)
        self.assertTrue(box.put({"id": "stable-event"}))
        with self.assertRaises(OSError):
            box.batch()
        staging.complete.assert_not_called()
        client.receive_message.return_value = {}
        client.send_message.side_effect = None
        self.assertEqual(box.batch(), [])
        staging.complete.assert_called_once_with(5)
