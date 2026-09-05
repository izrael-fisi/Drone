"""Logical ONNX execution adapter for a validated learned model release."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from .artifacts import REQUIRED_ONNX_ARTIFACTS, validate_artifact_interfaces, validate_model_artifacts
from .releases import ModelRelease


class OnnxRuntimeUnavailable(RuntimeError):
    pass


class LearnedModelRunner:
    """Run named learned-model stages through release-defined tensor bindings.

    It does not know anything about vehicle poses or PX4. Its sole purpose is
    to ensure the local runtime consumes only a verified release with explicit
    input/output names rather than relying on exporter-specific conventions.
    """

    def __init__(
        self,
        model_release: ModelRelease,
        *,
        artifact_root: str | Path,
        provider: str = "onnxruntime_cpu",
        session_factory: Callable[[Path, list[str]], Any] | None = None,
    ) -> None:
        self.model_release = model_release
        self.artifact_root = Path(artifact_root).expanduser().resolve()
        self.provider = provider
        interface_errors = validate_artifact_interfaces(model_release, required=True)
        if interface_errors:
            raise ValueError("Invalid learned model interface: " + ", ".join(interface_errors))
        validation = validate_model_artifacts(model_release, artifact_root=self.artifact_root, require_interfaces=True)
        if not validation.valid:
            raise ValueError("Invalid learned model artifacts: " + ", ".join(validation.errors))
        self._session_factory = session_factory or _onnx_session_factory
        self._sessions: dict[str, Any] = {}

    def run(self, role: str, logical_inputs: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        if role not in REQUIRED_ONNX_ARTIFACTS:
            raise ValueError(f"Unsupported learned model role: {role}")
        contract = self.model_release.artifact_interfaces[role]
        input_contract = contract["inputs"]
        output_contract = contract["outputs"]
        expected_inputs = set(input_contract)
        actual_inputs = set(logical_inputs)
        if expected_inputs != actual_inputs:
            raise ValueError(
                f"{role} inputs do not match release contract: expected {sorted(expected_inputs)}, got {sorted(actual_inputs)}."
            )
        feeds: dict[str, np.ndarray] = {}
        for logical_name, spec in input_contract.items():
            value = np.asarray(logical_inputs[logical_name])
            rank = spec.get("rank")
            if rank is not None and value.ndim != rank:
                raise ValueError(f"{role}:{logical_name} must have rank {rank}, got {value.ndim}.")
            feeds[spec["name"]] = value
        session = self._session_for(role)
        output_names = [spec["name"] for spec in output_contract.values()]
        values = session.run(output_names, feeds)
        return {logical_name: np.asarray(values[index]) for index, logical_name in enumerate(output_contract)}

    def _session_for(self, role: str) -> Any:
        if role not in self._sessions:
            artifact_path = self.artifact_root / self.model_release.artifact_paths[role]
            self._sessions[role] = self._session_factory(artifact_path, _provider_names(self.provider))
        return self._sessions[role]


def _provider_names(provider: str) -> list[str]:
    if provider == "onnxruntime_cpu":
        return ["CPUExecutionProvider"]
    if provider == "hailo_ai_hat_optional":
        return ["HailoExecutionProvider", "CPUExecutionProvider"]
    raise ValueError(f"Unsupported inference provider: {provider}")


def _onnx_session_factory(path: Path, providers: list[str]) -> Any:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise OnnxRuntimeUnavailable("Install drone-vision-nav[neural-runtime] to load ONNX models.") from exc
    try:
        return ort.InferenceSession(str(path), providers=providers)
    except Exception as exc:  # Runtime errors vary by provider and ONNX Runtime version.
        raise OnnxRuntimeUnavailable(f"Could not load ONNX model {path.name}: {exc}") from exc
