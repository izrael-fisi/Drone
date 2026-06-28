from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from vision_nav.mavlink_bridge import _load_mavutil, parse_mavlink_endpoint


API_SCHEMA_VERSION = "vision_nav_companion_api_v1"
DEFAULT_STATUS_ROOTS = "$HOME/DroneTransfer/outgoing:$HOME/drone-data:$HOME/Drone"
DEFAULT_SERVICE_UNITS = {
    "api": "drone-vision-nav-api.service",
    "terrain": "drone-vision-nav.service",
    "status-bridge": "drone-vision-nav-status-bridge.service",
}


@dataclass(frozen=True)
class CompanionApiConfig:
    host: str
    port: int
    repo_root: Path
    status_roots: list[Path]
    default_mavlink_endpoint: str | None
    default_serial_baud: int
    allow_service_control: bool
    service_units: dict[str, str]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def split_roots(value: str) -> list[Path]:
    roots: list[Path] = []
    for item in value.split(os.pathsep):
        expanded = os.path.expandvars(item.strip())
        if expanded:
            roots.append(Path(expanded).expanduser())
    return roots


def build_config(args: argparse.Namespace) -> CompanionApiConfig:
    repo_root = Path(args.repo_root or os.environ.get("VISION_NAV_REPO_ROOT") or Path.cwd()).expanduser().resolve()
    status_roots_value = args.status_roots or os.environ.get("VISION_NAV_RUNTIME_STATUS_ROOTS") or DEFAULT_STATUS_ROOTS
    return CompanionApiConfig(
        host=args.host,
        port=args.port,
        repo_root=repo_root,
        status_roots=split_roots(status_roots_value),
        default_mavlink_endpoint=args.default_mavlink_endpoint
        or os.environ.get("VISION_NAV_API_MAVLINK_ENDPOINT")
        or os.environ.get("VISION_NAV_MAVLINK_ENDPOINT"),
        default_serial_baud=args.default_serial_baud,
        allow_service_control=args.allow_service_control or env_bool("VISION_NAV_API_ALLOW_SERVICE_CONTROL"),
        service_units=DEFAULT_SERVICE_UNITS,
    )


def run_command(command: list[str], *, timeout_s: float = 3.0) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "duration_s": round(time.monotonic() - started, 3),
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "duration_s": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": f"Timed out after {timeout_s}s",
            "duration_s": round(time.monotonic() - started, 3),
        }


def read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key] = value.strip().strip('"')
    return result


def hostname_ips() -> list[str]:
    ips: set[str] = set()
    hostname_i = run_command(["hostname", "-I"], timeout_s=1.5)
    if hostname_i["ok"]:
        ips.update(part for part in hostname_i["stdout"].split() if part)
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = item[4][0]
            if address and not address.startswith("127."):
                ips.add(address)
    except OSError:
        pass
    return sorted(ips)


def discover_serial_devices() -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*", "/dev/serial*", "/dev/ttyAMA*"):
        for path in sorted(Path("/").glob(pattern.lstrip("/"))):
            try:
                stat = path.stat()
            except OSError:
                continue
            resolved = None
            if path.is_symlink():
                try:
                    resolved = str(path.resolve())
                except OSError:
                    resolved = None
            candidates[str(path)] = {
                "path": str(path),
                "resolved_path": resolved,
                "is_symlink": path.is_symlink(),
                "mode": oct(stat.st_mode & 0o777),
                "group_read_write": bool(stat.st_mode & 0o060),
            }
    return list(candidates.values())


def service_status(unit: str) -> dict[str, Any]:
    active = run_command(["systemctl", "--user", "is-active", unit], timeout_s=2.0)
    enabled = run_command(["systemctl", "--user", "is-enabled", unit], timeout_s=2.0)
    show = run_command(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "--property=LoadState,ActiveState,SubState,ExecMainPID,Result,FragmentPath",
            "--no-page",
        ],
        timeout_s=2.0,
    )
    properties: dict[str, str] = {}
    if show["stdout"]:
        for line in show["stdout"].splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                properties[key] = value
    return {
        "unit": unit,
        "active": active["stdout"] or ("inactive" if not active["ok"] else "unknown"),
        "enabled": enabled["stdout"] or ("disabled" if not enabled["ok"] else "unknown"),
        "properties": properties,
        "errors": [item["stderr"] for item in (active, enabled, show) if item.get("stderr")],
    }


def service_snapshot(config: CompanionApiConfig) -> dict[str, Any]:
    return {key: service_status(unit) for key, unit in config.service_units.items()}


def control_service(config: CompanionApiConfig, service_id: str, action: str) -> tuple[HTTPStatus, dict[str, Any]]:
    if not config.allow_service_control:
        return (
            HTTPStatus.FORBIDDEN,
            {
                "ok": False,
                "error": "service_control_disabled",
                "message": "Set VISION_NAV_API_ALLOW_SERVICE_CONTROL=1 to enable service start/stop/restart.",
            },
        )
    if service_id not in config.service_units:
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown_service", "service": service_id}
    if action not in {"start", "stop", "restart"}:
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown_service_action", "action": action}
    unit = config.service_units[service_id]
    result = run_command(["systemctl", "--user", action, unit], timeout_s=10.0)
    return (
        HTTPStatus.OK if result["ok"] else HTTPStatus.INTERNAL_SERVER_ERROR,
        {
            "ok": result["ok"],
            "service": service_id,
            "unit": unit,
            "action": action,
            "command": result,
            "status": service_status(unit),
        },
    )


def latest_runtime_status(config: CompanionApiConfig, *, max_bytes: int = 262_144) -> dict[str, Any]:
    candidates: list[Path] = []
    for root in config.status_roots:
        if not root.exists():
            continue
        try:
            candidates.extend(path for path in root.rglob("runtime_status.json") if path.is_file())
        except OSError:
            continue
    if not candidates:
        return {
            "ok": True,
            "status_found": False,
            "roots": [str(root) for root in config.status_roots],
            "status": None,
        }
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    stat = latest.stat()
    if stat.st_size > max_bytes:
        return {
            "ok": False,
            "status_found": True,
            "path": str(latest),
            "size_bytes": stat.st_size,
            "error": "runtime_status_too_large",
        }
    try:
        status = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "status_found": True,
            "path": str(latest),
            "size_bytes": stat.st_size,
            "error": "invalid_runtime_status_json",
            "message": str(exc),
        }
    return {
        "ok": True,
        "status_found": True,
        "path": str(latest),
        "size_bytes": stat.st_size,
        "modified_unix_ms": int(stat.st_mtime * 1000),
        "status": status,
    }


def normalize_mavlink_endpoint(endpoint: str, *, default_serial_baud: int) -> str:
    value = endpoint.strip()
    if not value:
        raise ValueError("MAVLink endpoint is empty")
    if value.startswith("/dev/"):
        return f"serial:{value}:{default_serial_baud}"
    return value


def probe_mavlink_heartbeat(endpoint: str, *, timeout_s: float = 4.0, default_serial_baud: int = 921600) -> dict[str, Any]:
    started = time.monotonic()
    normalized_endpoint = normalize_mavlink_endpoint(endpoint, default_serial_baud=default_serial_baud)
    try:
        mavutil = _load_mavutil()
        connection_string, baud = parse_mavlink_endpoint(normalized_endpoint)
        kwargs: dict[str, Any] = {"source_system": 255, "source_component": 0}
        if baud is not None:
            kwargs["baud"] = baud
        connection = mavutil.mavlink_connection(connection_string, **kwargs)
        try:
            message = connection.wait_heartbeat(timeout=max(timeout_s, 0.1))
            if message is None:
                return {
                    "ok": False,
                    "connected": False,
                    "endpoint": normalized_endpoint,
                    "status": "timeout",
                    "message": f"No MAVLink heartbeat within {timeout_s}s",
                    "duration_s": round(time.monotonic() - started, 3),
                }
            return {
                "ok": True,
                "connected": True,
                "endpoint": normalized_endpoint,
                "status": "heartbeat",
                "target_system": getattr(connection, "target_system", None),
                "target_component": getattr(connection, "target_component", None),
                "duration_s": round(time.monotonic() - started, 3),
                "heartbeat": {
                    "type": getattr(message, "type", None),
                    "autopilot": getattr(message, "autopilot", None),
                    "base_mode": getattr(message, "base_mode", None),
                    "system_status": getattr(message, "system_status", None),
                    "mavlink_version": getattr(message, "mavlink_version", None),
                },
            }
        finally:
            connection.close()
    except Exception as exc:
        return {
            "ok": False,
            "connected": False,
            "endpoint": normalized_endpoint,
            "status": "error",
            "message": str(exc),
            "duration_s": round(time.monotonic() - started, 3),
        }


def device_snapshot(config: CompanionApiConfig) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": API_SCHEMA_VERSION,
        "timestamp_utc": utc_now(),
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "ips": hostname_ips(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME"),
        "home": str(Path.home()),
        "repo_root": str(config.repo_root),
        "default_mavlink_endpoint": config.default_mavlink_endpoint,
        "default_serial_baud": config.default_serial_baud,
        "serial_devices": discover_serial_devices(),
        "os": read_os_release(),
        "services": service_snapshot(config),
    }


class CompanionApiHandler(BaseHTTPRequestHandler):
    server_version = "VisionNavCompanionAPI/0.1"

    @property
    def config(self) -> CompanionApiConfig:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"
        if path in {"/", "/health", "/api/v1/health"}:
            self.send_json(
                {
                    "ok": True,
                    "schema_version": API_SCHEMA_VERSION,
                    "service": "vision_nav_companion_api",
                    "timestamp_utc": utc_now(),
                }
            )
            return
        if path == "/api/v1/device":
            self.send_json(device_snapshot(self.config))
            return
        if path == "/api/v1/status":
            self.send_json(latest_runtime_status(self.config))
            return
        if path == "/api/v1/services":
            self.send_json({"ok": True, "services": service_snapshot(self.config)})
            return
        if path == "/api/v1/mavlink/heartbeat":
            endpoint = first_query_value(query, "endpoint") or self.config.default_mavlink_endpoint
            timeout_s = parse_float(first_query_value(query, "timeout_s"), default=4.0)
            if not endpoint:
                self.send_json({"ok": False, "error": "missing_mavlink_endpoint"}, status=HTTPStatus.BAD_REQUEST)
                return
            self.send_json(
                probe_mavlink_heartbeat(
                    endpoint,
                    timeout_s=timeout_s,
                    default_serial_baud=self.config.default_serial_baud,
                )
            )
            return
        self.send_json({"ok": False, "error": "not_found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/v1/mavlink/heartbeat":
            payload = self.read_json_body()
            endpoint = str(payload.get("endpoint") or self.config.default_mavlink_endpoint or "")
            timeout_s = parse_float(payload.get("timeout_s"), default=4.0)
            if not endpoint:
                self.send_json({"ok": False, "error": "missing_mavlink_endpoint"}, status=HTTPStatus.BAD_REQUEST)
                return
            self.send_json(
                probe_mavlink_heartbeat(
                    endpoint,
                    timeout_s=timeout_s,
                    default_serial_baud=self.config.default_serial_baud,
                )
            )
            return
        prefix = "/api/v1/services/"
        if path.startswith(prefix):
            parts = path[len(prefix) :].split("/")
            if len(parts) == 2:
                status, body = control_service(self.config, parts[0], parts[1])
                self.send_json(body, status=status)
                return
        self.send_json({"ok": False, "error": "not_found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > 65_536:
            raise ValueError("JSON body too large")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def send_json(self, body: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(body, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class CompanionApiServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: CompanionApiConfig) -> None:
        super().__init__(server_address, CompanionApiHandler)
        self.config = config


def first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def parse_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, list):
            value = value[0] if value else default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Drone Vision companion-computer HTTP API.")
    parser.add_argument("--host", default=os.environ.get("VISION_NAV_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VISION_NAV_API_PORT", "5000")))
    parser.add_argument("--repo-root", help="Repository root used in device status metadata.")
    parser.add_argument("--status-roots", help="Colon-separated roots searched for runtime_status.json.")
    parser.add_argument("--default-mavlink-endpoint", help="Default MAVLink endpoint for heartbeat probes.")
    parser.add_argument("--default-serial-baud", type=int, default=int(os.environ.get("VISION_NAV_API_SERIAL_BAUD", "921600")))
    parser.add_argument("--allow-service-control", action="store_true", help="Allow service start/stop/restart endpoints.")
    return parser.parse_args()


def main() -> None:
    config = build_config(parse_args())
    server = CompanionApiServer((config.host, config.port), config)
    print(f"Drone Vision companion API listening on http://{config.host}:{config.port}")
    print(f"Status roots: {', '.join(str(root) for root in config.status_roots)}")
    if config.default_mavlink_endpoint:
        print(f"Default MAVLink endpoint: {config.default_mavlink_endpoint}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping companion API.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
