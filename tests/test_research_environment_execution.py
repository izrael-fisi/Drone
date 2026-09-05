from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from vision_nav.neural import research_environment as environment


def test_actual_cpu_forward_backward_probe():
    torch = pytest.importorskip("torch")
    result = environment.exercise_torch_backend(torch, "cpu")
    assert result["status"] == "passed"
    assert result["convolution_loss"] == 81.
    assert result["matrix_result"] == [1., 2., 3.]


def test_cuda_requirement_does_not_fallback_to_cpu():
    fake = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    assert environment.exercise_torch_backend(fake, "cuda") == {
        "status": "failed", "device": "cuda", "reason": "cuda_unavailable_no_cpu_fallback"}


def test_report_rejects_execution_failure_even_if_cuda_is_available():
    fake = SimpleNamespace(__version__="test", cuda=SimpleNamespace(
        is_available=lambda: True, device_count=lambda: 1, get_device_name=lambda i: "test"))
    result = environment.evaluate_research_environment(
        require_cuda=True, exercise_torch=True, torch_loader=lambda: fake,
        module_finder=lambda n: object(), command_finder=lambda n: None)
    assert result["pytorch"]["cuda_available"] is True
    assert result["status"] == "not_ready"
    assert "torch_execution_probe_failed" in result["errors"]


def test_report_keeps_existing_nonprobe_mode():
    fake = SimpleNamespace(__version__="test", cuda=SimpleNamespace(is_available=lambda: False))
    result = environment.evaluate_research_environment(
        torch_loader=lambda: fake, module_finder=lambda n: object(), command_finder=lambda n: None)
    assert result["status"] == "ready"
    assert result["torch_execution_probe"]["status"] == "not_requested"


def test_core_pin_checker_rejects_nonexact_requirements(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("torch>=2.1\n")
    with pytest.raises(ValueError, match="exact core package pins"):
        environment.audit_bearing_requirements(path)


def test_core_pin_checker_reports_mismatch_and_accepts_cuda_version_suffix(tmp_path, monkeypatch):
    path = tmp_path / "requirements.txt"
    path.write_text("torch==2.1.2\nnumpy==1.26.4\n")
    monkeypatch.setattr(environment.importlib.metadata, "version", lambda n: "2.1.2+cu121" if n == "torch" else "2.0.0")
    result = environment.audit_bearing_requirements(path)
    assert result["distributions"]["torch"]["matches"] is True
    assert result["distributions"]["numpy"]["matches"] is False
    assert "bearing_dependency_mismatch:numpy" in result["errors"]


def test_setup_script_is_valid_shell_and_has_no_provisioning():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/research/setup_bearinguav_linux.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    text = script.read_text()
    assert "--require-hashes --no-build" in text
    assert "--require-cuda --exercise-torch" in text
    assert "pip sync" in text
    assert "--system" not in text and "pod create" not in text


def test_setup_script_rejects_nonabsolute_target_before_installation():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["bash", str(root / "scripts/research/setup_bearinguav_linux.sh"), "relative"],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "Usage:" in result.stderr
