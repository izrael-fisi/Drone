# Software Download Checklist

This checklist follows the current hardware-first scaffold. ROS 2, Gazebo, and
PX4 SITL are not required for the active workflow.

## Desktop / Mac

Install:

- Git
- Git LFS
- VS Code or Cursor
- Python 3.10 or newer
- `uv` or a normal Python virtual environment
- Node.js LTS
- Rust/Cargo for the Tauri backend
- QGroundControl for real Pixhawk setup, parameters, radio calibration, and logs

Useful Python packages are installed from this repo's project metadata and
requirements files. For direct local development:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[geo,mavlink]"
pip install -r requirements/pi-host.txt
```

Desktop app:

```bash
cd desktop-app
npm ci
npm run build
cd src-tauri
cargo check
cargo test
```

## Raspberry Pi 5

Use Raspberry Pi OS 64-bit or Ubuntu Server arm64, then run the project
bootstrap:

```bash
git clone https://github.com/izrael-fisi/Drone.git
cd Drone
chmod +x scripts/pi/*.sh
./scripts/pi/bootstrap_pi5.sh
sudo reboot
```

After reboot:

```bash
cd ~/Drone
./scripts/pi/first_run_checks.sh
```

The active Pi software needs:

- camera tools (`rpicam-*` or `libcamera-*`)
- Python vision/navigation dependencies
- MAVLink Python support
- Docker only if using the optional Pi container workflow
- SSH/rsync for desktop transfer

## QGroundControl

Use QGroundControl for real hardware only:

- firmware check
- airframe setup
- sensor calibration
- radio calibration
- flight mode switch verification
- parameter export to `px4.params`
- MAVLink telemetry over USB or SiK radio
- log download

## Not Required For Active Workflow

Do not install these for the next milestone unless a separate experiment needs
them:

- robotics middleware desktop stacks
- DDS bridge agents
- PX4-Autopilot checkout
- simulator engines
- simulated PX4 airframe targets
- `px4_msgs`

The next validation path is the real Holybro X500 V2 prop-off hardware test.
