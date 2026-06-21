from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import time
from typing import Any


@dataclass(frozen=True)
class ExternalPositionHealthConfig:
    min_rate_hz: float = 1.0
    max_latency_ms: float = 500.0
    max_horizontal_variance_m2: float = 400.0
    max_vertical_variance_m2: float = 900.0
    max_yaw_variance_rad2: float = math.radians(60.0) ** 2
    window_s: float = 10.0

    def to_dict(self) -> dict[str, float]:
        return {
            "min_rate_hz": float(self.min_rate_hz),
            "max_latency_ms": float(self.max_latency_ms),
            "max_horizontal_variance_m2": float(self.max_horizontal_variance_m2),
            "max_vertical_variance_m2": float(self.max_vertical_variance_m2),
            "max_yaw_variance_rad2": float(self.max_yaw_variance_rad2),
            "window_s": float(self.window_s),
        }


@dataclass
class ExternalPositionHealthSnapshot:
    status: str
    message_type: str
    attempt_count: int
    sent_count: int
    skipped_count: int
    send_rate_hz: float | None
    last_latency_ms: float | None
    last_skip_reason: str | None
    last_warnings: list[str] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)
    last_sent_age_s: float | None = None
    config: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message_type": self.message_type,
            "attempt_count": self.attempt_count,
            "sent_count": self.sent_count,
            "skipped_count": self.skipped_count,
            "send_rate_hz": self.send_rate_hz,
            "last_latency_ms": self.last_latency_ms,
            "last_skip_reason": self.last_skip_reason,
            "last_warnings": list(self.last_warnings),
            "skip_reasons": dict(self.skip_reasons),
            "last_sent_age_s": self.last_sent_age_s,
            "config": dict(self.config),
        }


class ExternalPositionStreamHealth:
    def __init__(self, config: ExternalPositionHealthConfig | None = None) -> None:
        self.config = config or ExternalPositionHealthConfig()
        self.attempt_count = 0
        self.sent_count = 0
        self.skipped_count = 0
        self.skip_reasons: dict[str, int] = {}
        self.last_skip_reason: str | None = None
        self.last_latency_ms: float | None = None
        self.last_warnings: list[str] = []
        self.last_message_type = "none"
        self._sent_monotonic_s: deque[float] = deque()

    def update(
        self,
        *,
        result: dict[str, Any],
        mavlink_result: dict[str, Any] | None,
        message_type: str,
        now_monotonic_s: float | None = None,
        now_time_us: int | None = None,
    ) -> ExternalPositionHealthSnapshot:
        now_monotonic_s = time.monotonic() if now_monotonic_s is None else float(now_monotonic_s)
        now_time_us = int(time.time() * 1_000_000) if now_time_us is None else int(now_time_us)
        mavlink_result = mavlink_result or {}
        self.attempt_count += 1
        self.last_message_type = message_type
        self.last_warnings = self._warnings_for_result(result, now_time_us)
        self.last_latency_ms = self._latency_ms(result, now_time_us)

        if mavlink_result.get("sent") is True:
            self.sent_count += 1
            self.last_skip_reason = None
            self._sent_monotonic_s.append(now_monotonic_s)
        else:
            self.skipped_count += 1
            reason = str(mavlink_result.get("reason") or "not_sent")
            self.last_skip_reason = reason
            self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
            if reason not in ("match_not_accepted", "missing_local_position"):
                self.last_warnings.append(f"send_skipped:{reason}")

        self._trim_sent_window(now_monotonic_s)
        return self.snapshot(now_monotonic_s=now_monotonic_s)

    def snapshot(self, *, now_monotonic_s: float | None = None) -> ExternalPositionHealthSnapshot:
        now_monotonic_s = time.monotonic() if now_monotonic_s is None else float(now_monotonic_s)
        send_rate_hz = self._send_rate_hz()
        last_sent_age_s = None
        if self._sent_monotonic_s:
            last_sent_age_s = max(0.0, now_monotonic_s - self._sent_monotonic_s[-1])
        status = self._status(send_rate_hz, last_sent_age_s)
        return ExternalPositionHealthSnapshot(
            status=status,
            message_type=self.last_message_type,
            attempt_count=self.attempt_count,
            sent_count=self.sent_count,
            skipped_count=self.skipped_count,
            send_rate_hz=send_rate_hz,
            last_latency_ms=self.last_latency_ms,
            last_skip_reason=self.last_skip_reason,
            last_warnings=list(self.last_warnings),
            skip_reasons=dict(self.skip_reasons),
            last_sent_age_s=last_sent_age_s,
            config=self.config.to_dict(),
        )

    def _status(self, send_rate_hz: float | None, last_sent_age_s: float | None) -> str:
        if self.attempt_count == 0:
            return "inactive"
        if self.sent_count == 0:
            return "degraded"
        if self.last_warnings:
            return "degraded"
        if last_sent_age_s is not None and last_sent_age_s > max(self.config.window_s, 1.0):
            return "degraded"
        if send_rate_hz is None:
            return "warming_up"
        if send_rate_hz < max(self.config.min_rate_hz, 0.0):
            return "degraded"
        return "healthy"

    def _warnings_for_result(self, result: dict[str, Any], now_time_us: int) -> list[str]:
        warnings: list[str] = []
        if result.get("status") != "accepted":
            warnings.append("match_not_accepted")
            return warnings

        timestamp_us = _optional_int(result.get("timestamp_us"))
        if timestamp_us is None:
            warnings.append("missing_timestamp")
        else:
            latency_ms = max(0.0, (now_time_us - timestamp_us) / 1000.0)
            if latency_ms > self.config.max_latency_ms:
                warnings.append("latency_high")

        measurement = result.get("measurement") or {}
        covariance = measurement.get("covariance") or result.get("covariance") or {}
        east_m2 = _optional_float(covariance.get("x_m2"))
        north_m2 = _optional_float(covariance.get("y_m2"))
        z_m2 = _optional_float(covariance.get("z_m2"))
        yaw_rad2 = _optional_float(covariance.get("yaw_rad2"))

        if east_m2 is None or north_m2 is None:
            warnings.append("horizontal_covariance_missing")
        elif max(east_m2, north_m2) > self.config.max_horizontal_variance_m2:
            warnings.append("horizontal_covariance_high")
        if z_m2 is not None and z_m2 > self.config.max_vertical_variance_m2:
            warnings.append("vertical_covariance_high")
        if yaw_rad2 is not None and yaw_rad2 > self.config.max_yaw_variance_rad2:
            warnings.append("yaw_covariance_high")
        return warnings

    def _latency_ms(self, result: dict[str, Any], now_time_us: int) -> float | None:
        timestamp_us = _optional_int(result.get("timestamp_us"))
        if timestamp_us is None:
            return None
        return max(0.0, (now_time_us - timestamp_us) / 1000.0)

    def _trim_sent_window(self, now_monotonic_s: float) -> None:
        window_s = max(float(self.config.window_s), 0.1)
        while self._sent_monotonic_s and now_monotonic_s - self._sent_monotonic_s[0] > window_s:
            self._sent_monotonic_s.popleft()

    def _send_rate_hz(self) -> float | None:
        if len(self._sent_monotonic_s) < 2:
            return None
        elapsed_s = self._sent_monotonic_s[-1] - self._sent_monotonic_s[0]
        if elapsed_s <= 0.0:
            return None
        return (len(self._sent_monotonic_s) - 1) / elapsed_s


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        output = float(value)
        return output if math.isfinite(output) else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
