#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This installer expects a Raspberry Pi/aarch64 Linux host." >&2
  exit 1
fi

qgc_url="${QGC_APPIMAGE_URL:-https://d176tv9ibo4jno.cloudfront.net/builds/master/QGroundControl-aarch64.AppImage}"
install_dir="${QGC_INSTALL_DIR:-/opt/qgroundcontrol}"
appimage="$install_dir/QGroundControl-aarch64.AppImage"
tmp="$(mktemp)"

cleanup() {
  rm -f "$tmp"
}
trap cleanup EXIT

echo "Installing QGroundControl dependencies..."
sudo apt-get update
sudo apt-get install -y \
  gstreamer1.0-libav \
  gstreamer1.0-plugins-bad \
  libfuse2t64 \
  libxcb-cursor0 \
  libxcb-xinerama0 \
  libxkbcommon-x11-0

echo "Downloading QGroundControl AppImage..."
python3 - "$qgc_url" "$tmp" <<'PY'
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

sudo install -d -m 0755 "$install_dir"
sudo install -m 0755 "$tmp" "$appimage"

sudo tee /usr/local/bin/qgroundcontrol >/dev/null <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec /opt/qgroundcontrol/QGroundControl-aarch64.AppImage "$@"
SH
sudo chmod 0755 /usr/local/bin/qgroundcontrol

sudo tee /usr/local/bin/qgroundcontrol-safe >/dev/null <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo "QGroundControl is installed, but no graphical DISPLAY/WAYLAND_DISPLAY is available." >&2
  echo "Launch it from the Raspberry Pi desktop, or export a valid display session." >&2
  exit 64
fi
exec /opt/qgroundcontrol/QGroundControl-aarch64.AppImage "$@"
SH
sudo chmod 0755 /usr/local/bin/qgroundcontrol-safe

echo "Installed QGroundControl:"
ls -lh "$appimage"
sha256sum "$appimage"
command -v qgroundcontrol
