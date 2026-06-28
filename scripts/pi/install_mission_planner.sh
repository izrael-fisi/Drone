#!/usr/bin/env bash
set -euo pipefail

mission_planner_url="${MISSION_PLANNER_ZIP_URL:-https://firmware.ardupilot.org/Tools/MissionPlanner/MissionPlanner-latest.zip}"
install_dir="${MISSION_PLANNER_INSTALL_DIR:-/opt/missionplanner}"
tmp="$(mktemp -d)"

cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

echo "Installing Mission Planner dependencies..."
sudo apt-get update
sudo apt-get install -y mono-complete unzip

echo "Downloading Mission Planner..."
python3 - "$mission_planner_url" "$tmp/MissionPlanner.zip" <<'PY'
from pathlib import Path
from urllib.request import Request, urlopen
import sys
import time

url = sys.argv[1]
target = Path(sys.argv[2])
req = Request(url, headers={"User-Agent": "DroneVisionInstaller/1.0"})
with urlopen(req, timeout=60) as response:
    total = int(response.headers.get("content-length") or 0)
    done = 0
    last = 0.0
    with target.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            now = time.time()
            if now - last > 5:
                if total:
                    print(f"downloaded {done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MiB", flush=True)
                else:
                    print(f"downloaded {done / 1024 / 1024:.1f} MiB", flush=True)
                last = now
print(f"download complete {done} bytes", flush=True)
PY

unzip -q "$tmp/MissionPlanner.zip" -d "$tmp/MissionPlanner"
test -f "$tmp/MissionPlanner/MissionPlanner.exe"

sudo rm -rf "$install_dir"
sudo install -d -m 0755 "$install_dir"
sudo cp -a "$tmp/MissionPlanner/." "$install_dir/"
sudo chmod -R a+rX "$install_dir"

sudo tee /usr/local/bin/missionplanner >/dev/null <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec mono /opt/missionplanner/MissionPlanner.exe "$@"
SH
sudo chmod 0755 /usr/local/bin/missionplanner

sudo tee /usr/local/bin/missionplanner-safe >/dev/null <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo "Mission Planner is installed, but no graphical DISPLAY/WAYLAND_DISPLAY is available." >&2
  echo "Launch it from a Raspberry Pi desktop session, or use native Windows Mission Planner." >&2
  exit 64
fi
exec mono /opt/missionplanner/MissionPlanner.exe "$@"
SH
sudo chmod 0755 /usr/local/bin/missionplanner-safe

echo "Installed Mission Planner:"
ls -lh "$install_dir/MissionPlanner.exe"
command -v missionplanner
mono --version | sed -n '1,3p'
