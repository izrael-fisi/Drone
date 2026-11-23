"""Unit checks for the engine-backed deployment verification driver."""

import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("container_verification", Path(__file__).resolve().parents[1] /
                                               "scripts/dev/verify_module_containers.py")
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


def test_native_success_requires_completed_shadow_replay():
    report = {"complete": True, "position_authority": False, "px4_external_vision": "disabled",
              "worker": {"completed_batch_count": 160, "thread_alive": False}}
    verification.assert_native_report(report)
    for key, value in (("complete", False), ("position_authority", True), ("px4_external_vision", "enabled")):
        with pytest.raises(ValueError):
            verification.assert_native_report({**report, key: value})
    with pytest.raises(ValueError):
        verification.assert_native_report({**report, "worker": {"completed_batch_count": 159, "thread_alive": False}})


def test_failed_launch_must_prove_the_expected_rejection():
    result = {"state": "rejected", "reason": "requires explicit --propellers-removed",
              "position_authority": False, "px4_external_vision": "disabled"}
    verification.assert_rejection("Container created\n" + json.dumps(result), "--propellers-removed")
    for log in ("Docker daemon unavailable", json.dumps({**result, "reason": "missing package"}),
                json.dumps({**result, "position_authority": True})):
        with pytest.raises(ValueError):
            verification.assert_rejection(log, "--propellers-removed")


def test_resolved_mount_and_network_safety_is_checked():
    service = {"network_mode": "none", "read_only": True, "init": True,
               "cap_drop": ["ALL"], "tmpfs": ["/tmp:rw,nosuid,nodev,size=512m"]}
    verification.assert_isolation(service)
    for key, value in (("network_mode", "host"), ("privileged", True), ("devices", ["/dev:/dev"]),
                       ("tmpfs", ["/tmp:rw", "nosuid", "nodev", "size=512m"])):
        with pytest.raises(ValueError):
            verification.assert_isolation({**service, key: value})


def test_tree_identity_ignores_generated_bytecode_but_detects_source_changes(tmp_path):
    source = tmp_path / "module.py"
    source.write_text("value = 1\n")
    first = verification.tree_digest(tmp_path)
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"test")
    (tmp_path / ".DS_Store").write_bytes(b"Finder metadata")
    assert verification.tree_digest(tmp_path) == first
    source.write_text("value = 2\n")
    assert verification.tree_digest(tmp_path) != first
