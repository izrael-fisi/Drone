import { useState } from "react";
import type { RightDockRoute } from "../../../lib/shellStore";
import { cmd } from "../../../lib/tauri";
import { cn } from "../../../lib/utils";
import type { OperatorRuntimeModel } from "../../../lib/operatorRuntimeAdapter";
import { DEFAULT_PIPELINE_CONFIG } from "../../../lib/pipelineConfig";
import { PanelAction, PanelStack, StatusRow } from "../../../platform/ui";

export function CameraPanel({
  model,
  pushRightDock,
}: {
  model: OperatorRuntimeModel;
  pushRightDock: (route: RightDockRoute) => void;
}) {
  const device = model.activeDevice;
  return (
    <PanelStack title="Camera" subtitle="Vision pipeline">
      <StatusRow label="Target profile" value={DEFAULT_PIPELINE_CONFIG.pipeline} healthy={false} />
      <StatusRow label="Local features" value="XFeat + LighterGlue (shadow)" healthy={false} />
      <StatusRow label="Camera profile" value={device?.camera_profile ?? "not selected"} healthy={Boolean(device?.camera_profile)} />
      <StatusRow label="Export state" value="No live camera export listener in browser fallback" healthy={false} />
      <PanelAction label="Create Camera Config" detail="Camera config editor is not attached to this shell yet" disabled />
      <PanelAction label="Calibration" detail="Open calibration drill-down" onClick={() => pushRightDock("calibration")} />
    </PanelStack>
  );
}

export function CalibrationPanel({ model }: { model: OperatorRuntimeModel }) {
  const device = model.activeDevice;
  const [capturing, setCapturing] = useState(false);
  const [capturePreview, setCapturePreview] = useState<string | null>(null);
  const [capturePath, setCapturePath] = useState<string | null>(null);
  const [captureMessage, setCaptureMessage] = useState<string | null>(null);
  const [captureOk, setCaptureOk] = useState(false);
  const hasCaptureConfig = Boolean(
    device?.host
    && device?.username
    && device?.auth
    && device?.remote_project_path,
  );

  const startCaptureTest = async () => {
    if (!device?.host || !device?.username || !device?.auth || !device?.remote_project_path) {
      setCaptureOk(false);
      setCaptureMessage("Select a device with host, username, auth, and runtime path first.");
      return;
    }
    setCapturing(true);
    setCaptureOk(false);
    setCaptureMessage("capturing frame");
    try {
      const frame = await cmd.sshCaptureCameraFrame(
        device.host,
        device.port ?? 22,
        device.username,
        device.auth,
        device.remote_project_path,
        960,
        720,
        1000,
      );
      setCapturePreview(`data:${frame.mime_type};base64,${frame.base64_data}`);
      setCapturePath(frame.remote_path);
      setCaptureOk(true);
      const output = [frame.stdout, frame.stderr].filter(Boolean).join(" ").trim();
      setCaptureMessage(output ? `captured ${frame.remote_path}: ${output}` : `captured ${frame.remote_path}`);
    } catch (error) {
      setCapturePreview(null);
      setCapturePath(null);
      setCaptureOk(false);
      setCaptureMessage(String(error));
    } finally {
      setCapturing(false);
    }
  };

  return (
    <PanelStack title="Calibration" subtitle="Guided capture">
      <StatusRow label="Device" value={device?.name ?? "No active device"} healthy={Boolean(device)} />
      <StatusRow label="Capture set" value={captureOk ? "Frame captured" : capturing ? "Capturing" : "Not started"} healthy={captureOk} />
      <StatusRow label="Intrinsics" value="Manual / pending capture" healthy={false} />
      <StatusRow label="Distortion model" value="Use camera pipeline defaults" healthy={false} />
      <PanelAction
        label={capturing ? "Capturing" : "Start Capture"}
        detail={hasCaptureConfig ? "Capture a test frame over SSH" : "Connect a configured desktop device first"}
        onClick={startCaptureTest}
        disabled={capturing || !hasCaptureConfig}
      />
      {capturePreview && (
        <div className="overflow-hidden rounded-lg bg-white/[0.035]">
          <img src={capturePreview} alt="Latest camera calibration capture" className="aspect-video w-full object-cover" />
          <div className="border-t border-border px-3 py-2 font-mono text-[11px] text-slate-500">
            {capturePath ?? "camera-preview/latest.jpg"}
          </div>
        </div>
      )}
      {captureMessage && (
        <div
          className={cn(
            "whitespace-pre-wrap break-words rounded-lg border px-3 py-2 text-xs",
            captureOk
              ? "border-status-ready/30 bg-status-ready/10 text-status-ready"
              : "border-status-warning/30 bg-status-warning/10 text-status-warning",
          )}
        >
          {captureMessage}
        </div>
      )}
    </PanelStack>
  );
}
