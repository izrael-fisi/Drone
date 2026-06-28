from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from vision_nav.companion_api import (
    CompanionApiConfig,
    control_service,
    latest_runtime_status,
    normalize_mavlink_endpoint,
)


class CompanionApiTests(unittest.TestCase):
    def test_normalize_bare_serial_endpoint(self) -> None:
        self.assertEqual(
            normalize_mavlink_endpoint("/dev/ttyACM0", default_serial_baud=921600),
            "serial:/dev/ttyACM0:921600",
        )
        self.assertEqual(
            normalize_mavlink_endpoint("serial:/dev/ttyACM0:57600", default_serial_baud=921600),
            "serial:/dev/ttyACM0:57600",
        )

    def test_latest_runtime_status_finds_newest_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "a" / "runtime_status.json"
            newer = root / "b" / "runtime_status.json"
            older.parent.mkdir()
            newer.parent.mkdir()
            older.write_text(json.dumps({"sequence": 1}))
            newer.write_text(json.dumps({"sequence": 2}))
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            config = CompanionApiConfig(
                host="127.0.0.1",
                port=5000,
                repo_root=root,
                status_roots=[root],
                default_mavlink_endpoint=None,
                default_serial_baud=921600,
                allow_service_control=False,
                service_units={},
            )

            result = latest_runtime_status(config)

            self.assertTrue(result["ok"])
            self.assertTrue(result["status_found"])
            self.assertEqual(result["status"], {"sequence": 2})

    def test_service_control_is_disabled_by_default(self) -> None:
        config = CompanionApiConfig(
            host="127.0.0.1",
            port=5000,
            repo_root=Path("."),
            status_roots=[],
            default_mavlink_endpoint=None,
            default_serial_baud=921600,
            allow_service_control=False,
            service_units={"api": "drone-vision-nav-api.service"},
        )

        status, body = control_service(config, "api", "restart")

        self.assertEqual(status.value, 403)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "service_control_disabled")


if __name__ == "__main__":
    unittest.main()
