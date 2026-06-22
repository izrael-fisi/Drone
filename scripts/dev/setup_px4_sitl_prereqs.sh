#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
px4_dir="${VISION_NAV_PX4_AUTOPILOT_DIR:-$HOME/PX4-Autopilot}"
apply=0
clone_px4=0

usage() {
  cat <<EOF
Usage: $0 [--apply] [--clone-px4] [--px4-dir PATH]

Prepare local prerequisites for the PX4 SITL external-vision receiver harness.

By default this is a dry run: it prints the commands it would run.

Options:
  --apply          Run package/install commands instead of only printing them.
                   Installs tmux, cmake, and PX4 Python requirements when missing.
  --clone-px4     Clone PX4-Autopilot if the checkout is missing.
  --px4-dir PATH  PX4 checkout path. Defaults to VISION_NAV_PX4_AUTOPILOT_DIR
                  or \$HOME/PX4-Autopilot.
  -h, --help      Show this help.

Examples:
  $0
  $0 --apply
  $0 --apply --clone-px4
EOF
}

quote_command() {
  local first=1
  for arg in "$@"; do
    if [[ "$first" == "1" ]]; then
      first=0
    else
      printf ' '
    fi
    printf '%q' "$arg"
  done
}

run_or_print() {
  printf '+ '
  quote_command "$@"
  printf '\n'
  if [[ "$apply" == "1" ]]; then
    "$@"
  fi
}

have_command() {
  command -v "$1" >/dev/null 2>&1
}

install_tmux_if_needed() {
  if have_command tmux; then
    echo "[OK] tmux is installed: $(command -v tmux)"
    return 0
  fi

  echo "[INFO] tmux is missing."
  case "$(uname -s)" in
    Darwin)
      if ! have_command brew; then
        echo "[WARN] Homebrew is not installed. Install Homebrew first, then rerun this helper." >&2
        return 0
      fi
      run_or_print brew install tmux
      ;;
    Linux)
      if have_command apt-get; then
        run_or_print sudo apt-get update
        run_or_print sudo apt-get install -y tmux
      elif have_command dnf; then
        run_or_print sudo dnf install -y tmux
      elif have_command pacman; then
        run_or_print sudo pacman -S --needed tmux
      else
        echo "[WARN] No supported package manager found. Install tmux manually." >&2
      fi
      ;;
    *)
      echo "[WARN] Unsupported OS for automatic tmux install. Install tmux manually." >&2
      ;;
  esac
}

install_cmake_if_needed() {
  if have_command cmake; then
    echo "[OK] cmake is installed: $(command -v cmake)"
    return 0
  fi

  echo "[INFO] cmake is missing."
  case "$(uname -s)" in
    Darwin)
      if ! have_command brew; then
        echo "[WARN] Homebrew is not installed. Install Homebrew first, then rerun this helper." >&2
        return 0
      fi
      run_or_print brew install cmake
      ;;
    Linux)
      if have_command apt-get; then
        run_or_print sudo apt-get update
        run_or_print sudo apt-get install -y cmake
      elif have_command dnf; then
        run_or_print sudo dnf install -y cmake
      elif have_command pacman; then
        run_or_print sudo pacman -S --needed cmake
      else
        echo "[WARN] No supported package manager found. Install cmake manually." >&2
      fi
      ;;
    *)
      echo "[WARN] Unsupported OS for automatic cmake install. Install cmake manually." >&2
      ;;
  esac
}

install_px4_python_requirements_if_needed() {
  local requirements="$px4_dir/Tools/setup/requirements.txt"
  if [[ ! -f "$requirements" ]]; then
    echo "[INFO] PX4 Python requirements file is not available yet: $requirements"
    return 0
  fi

  if "$python_bin" -c "import menuconfig" >/dev/null 2>&1; then
    echo "[OK] PX4 Python build requirements are available for $python_bin"
    return 0
  fi

  echo "[INFO] PX4 Python build requirements are missing for $python_bin."
  run_or_print "$python_bin" -m pip install -r "$requirements"
}

prepare_px4_checkout() {
  if [[ -d "$px4_dir" ]]; then
    echo "[OK] PX4 checkout path exists: $px4_dir"
    return 0
  fi

  echo "[INFO] PX4 checkout is missing: $px4_dir"
  if [[ "$clone_px4" != "1" ]]; then
    echo "[INFO] Rerun with --clone-px4 to clone PX4-Autopilot, or set VISION_NAV_PX4_AUTOPILOT_DIR to an existing checkout."
    return 0
  fi

  if ! have_command git; then
    echo "[WARN] git is not installed. Install git before cloning PX4-Autopilot." >&2
    return 0
  fi
  run_or_print git clone https://github.com/PX4/PX4-Autopilot.git "$px4_dir"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=1
      shift
      ;;
    --clone-px4)
      clone_px4=1
      shift
      ;;
    --px4-dir)
      if [[ $# -lt 2 ]]; then
        echo "--px4-dir requires a path." >&2
        exit 2
      fi
      px4_dir="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$apply" == "1" ]]; then
  echo "PX4 SITL prerequisite setup"
else
  echo "PX4 SITL prerequisite setup dry run"
fi
echo "Repo: $repo_root"
echo "PX4 checkout: $px4_dir"

install_tmux_if_needed
install_cmake_if_needed
prepare_px4_checkout
install_px4_python_requirements_if_needed

cat <<EOF

Next receiver proof command:
  VISION_NAV_PX4_AUTOPILOT_DIR="$px4_dir" VISION_NAV_SITL_SMOKE_DIR="\$PWD/px4-sitl-evidence" ./scripts/dev/run_px4_sitl_external_vision_capture.sh
EOF

if [[ "$apply" != "1" ]]; then
  echo
  echo "Dry run only. Rerun with --apply to install tmux, and add --clone-px4 if this machine should create a PX4 checkout."
fi
