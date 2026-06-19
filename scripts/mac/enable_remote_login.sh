#!/usr/bin/env bash
set -euo pipefail

echo "Enabling macOS Remote Login. You may be prompted for your admin password."
sudo systemsetup -setremotelogin on
systemsetup -getremotelogin || true

