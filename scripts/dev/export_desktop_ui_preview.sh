#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESKTOP_DIR="$REPO_ROOT/desktop-app"
TEMPLATE_DIR="$DESKTOP_DIR/export-preview"
OUT_DIR="$DESKTOP_DIR/export/windows-ui-preview"
ZIP_PATH="$DESKTOP_DIR/export/DroneVisionNav-Windows-UI-Preview.zip"

echo "[Drone Vision Nav] Building desktop UI..."
npm run build --prefix "$DESKTOP_DIR"

echo "[Drone Vision Nav] Preparing export folder..."
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

cp -R "$DESKTOP_DIR/dist/." "$OUT_DIR/"
cp "$TEMPLATE_DIR/server.mjs" "$OUT_DIR/server.mjs"
cp "$TEMPLATE_DIR/start-windows.bat" "$OUT_DIR/start-windows.bat"
cp "$TEMPLATE_DIR/start-powershell.ps1" "$OUT_DIR/start-powershell.ps1"
cp "$TEMPLATE_DIR/deploy-and-run.ps1" "$OUT_DIR/deploy-and-run.ps1"
cp "$TEMPLATE_DIR/seed-demo-state.html" "$OUT_DIR/seed-demo-state.html"
cp "$TEMPLATE_DIR/WINDOWS_QUICKSTART.md" "$OUT_DIR/WINDOWS_QUICKSTART.md"
cp "$TEMPLATE_DIR/CLAUDE_AGENT_CONTEXT.md" "$OUT_DIR/CLAUDE_AGENT_CONTEXT.md"
cp "$TEMPLATE_DIR/AGENT_AUTO_DEPLOY.md" "$OUT_DIR/AGENT_AUTO_DEPLOY.md"
cp "$TEMPLATE_DIR/README.md" "$OUT_DIR/README.md"

cat > "$OUT_DIR/EXPORT_MANIFEST.txt" <<MANIFEST
Drone Vision Nav Windows UI Preview
Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Source repo: $REPO_ROOT
Source commit: $(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)
Source branch: $(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || echo unknown)

Run on Windows:
  start-windows.bat

Optional demo data:
  http://127.0.0.1:1420/seed-demo-state.html

Agent context:
  CLAUDE_AGENT_CONTEXT.md

Agent auto deploy:
  AGENT_AUTO_DEPLOY.md
  deploy-and-run.ps1
MANIFEST

echo "[Drone Vision Nav] Creating zip package..."
rm -f "$ZIP_PATH"
(cd "$OUT_DIR" && zip -qr "$ZIP_PATH" .)

echo ""
echo "[Drone Vision Nav] Export ready:"
echo "  $OUT_DIR"
echo "  $ZIP_PATH"
echo ""
echo "Copy the zip or folder to the Windows 10 desktop and run deploy-and-run.ps1 or start-windows.bat."
