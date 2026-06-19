#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

warn() {
  printf '\n[WARN] %s\n' "$*" >&2
}

require_sudo() {
  if ! sudo -v; then
    echo "sudo is required for package installation and service setup." >&2
    exit 1
  fi
}

apt_install_if_available() {
  local packages_to_install=()
  local package

  for package in "$@"; do
    if apt-cache show "$package" >/dev/null 2>&1; then
      packages_to_install+=("$package")
    else
      warn "Skipping unavailable apt package: $package"
    fi
  done

  if ((${#packages_to_install[@]} > 0)); then
    sudo apt-get install -y "${packages_to_install[@]}"
  fi
}

install_docker_repo() {
  local os_id version_codename repo_os arch

  # shellcheck disable=SC1091
  . /etc/os-release
  os_id="${ID:-}"
  version_codename="${VERSION_CODENAME:-}"
  arch="$(dpkg --print-architecture)"

  case "$os_id" in
    ubuntu)
      repo_os="ubuntu"
      ;;
    debian|raspbian)
      repo_os="debian"
      ;;
    *)
      warn "Unknown OS ID '$os_id'. Trying Debian Docker repo semantics."
      repo_os="debian"
      ;;
  esac

  if [[ -z "$version_codename" ]]; then
    echo "Could not determine VERSION_CODENAME from /etc/os-release." >&2
    exit 1
  fi

  log "Adding Docker apt repository for ${repo_os}/${version_codename}/${arch}"
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL "https://download.docker.com/linux/${repo_os}/gpg" \
    -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/${repo_os}
Suites: ${version_codename}
Components: stable
Architectures: ${arch}
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  sudo apt-get update
  sudo apt-get install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin
}

install_docker_fallback() {
  warn "Official Docker packages failed. Falling back to distro docker.io."
  sudo apt-get update
  sudo apt-get install -y docker.io
  apt_install_if_available docker-compose docker-compose-plugin
}

main() {
  if [[ "${EUID}" -eq 0 ]]; then
    echo "Run this script as your normal Pi user, not with sudo." >&2
    echo "It will request sudo only for the commands that need it." >&2
    exit 1
  fi

  require_sudo

  local target_user="$USER"
  local target_home
  target_home="$(getent passwd "$target_user" | cut -d: -f6)"

  log "Updating package lists"
  sudo apt-get update

  log "Installing base system packages"
  sudo apt-get install -y \
    ca-certificates \
    curl \
    git \
    git-lfs \
    gnupg \
    jq \
    lsb-release \
    openssh-server \
    rsync \
    avahi-daemon \
    build-essential \
    cmake \
    pkg-config \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    python3-numpy \
    python3-opencv \
    libopencv-dev \
    sqlite3 \
    libsqlite3-dev \
    v4l-utils \
    i2c-tools \
    htop \
    tmux \
    vim

  log "Installing Raspberry Pi camera packages when available"
  apt_install_if_available \
    rpicam-apps \
    libcamera-tools \
    python3-picamera2 \
    python3-libcamera \
    python3-kms++

  log "Installing geospatial packages when available"
  apt_install_if_available \
    gdal-bin \
    libgdal-dev \
    python3-gdal \
    python3-rasterio \
    python3-pyproj \
    python3-geographiclib

  log "Enabling SSH and mDNS services"
  sudo systemctl enable --now ssh
  sudo systemctl enable --now avahi-daemon

  log "Installing Docker"
  if ! command -v docker >/dev/null 2>&1; then
    if ! install_docker_repo; then
      install_docker_fallback
    fi
  else
    log "Docker already installed"
  fi

  sudo systemctl enable --now docker
  sudo usermod -aG docker "$target_user"

  log "Creating transfer and data folders"
  install -d \
    "$target_home/DroneTransfer/incoming" \
    "$target_home/DroneTransfer/outgoing" \
    "$target_home/DroneTransfer/logs" \
    "$target_home/DroneTransfer/map-bundles" \
    "$target_home/drone-data/logs" \
    "$target_home/drone-data/map_bundles" \
    "$target_home/drone-data/captures"

  log "Creating Python virtual environment"
  python3 -m venv --system-site-packages "$target_home/drone_vision_nav_venv"
  # shellcheck disable=SC1091
  source "$target_home/drone_vision_nav_venv/bin/activate"
  python -m pip install --upgrade pip setuptools wheel

  if [[ -f requirements/pi-host.txt ]]; then
    python -m pip install -r requirements/pi-host.txt
    python -m pip install -e . --no-deps
  elif [[ -f requirements/pi.txt ]]; then
    warn "requirements/pi-host.txt not found. Falling back to requirements/pi.txt."
    python -m pip install -r requirements/pi.txt
    python -m pip install -e . --no-deps
  else
    warn "No Pi requirements file found. Skipping project Python install."
  fi

  log "Setup complete"
  echo "User added to docker group: $target_user"
  echo "Reboot the Pi before running Docker without sudo:"
  echo "  sudo reboot"
}

main "$@"
