import type { FeatureMethod, VisionPipeline } from "./types";

export type PipelineConfig = {
  pipeline: VisionPipeline;
  featureMethod: FeatureMethod;
  modelReleaseId: string;
  mapReleaseId: string;
  calibrationId: string;
  inferenceProvider: "onnxruntime_cpu" | "hailo_ai_hat_optional";
  shadowOnly: true;
  // Retained only for mission-plan compatibility. The learned model owns these values.
  maxFeatures: 0;
};

export const PIPELINE_CONFIG_KEY = "drone_vision_pipeline_config";

export const DEFAULT_PIPELINE_CONFIG: PipelineConfig = {
  pipeline: "pi5_crossview_nav_v1",
  featureMethod: "xfeat_lighterglue",
  modelReleaseId: "pi5-crossview-nav@unassigned",
  mapReleaseId: "unassigned",
  calibrationId: "down-camera-v1",
  inferenceProvider: "onnxruntime_cpu",
  shadowOnly: true,
  maxFeatures: 0,
};

export function loadPipelineConfig(): PipelineConfig {
  try {
    const raw = localStorage.getItem(PIPELINE_CONFIG_KEY);
    if (!raw) return DEFAULT_PIPELINE_CONFIG;
    const saved = JSON.parse(raw) as Partial<PipelineConfig>;
    // Prior installations stored selectable classical methods. Intentionally ignore them.
    return {
      ...DEFAULT_PIPELINE_CONFIG,
      modelReleaseId: typeof saved.modelReleaseId === "string" ? saved.modelReleaseId : DEFAULT_PIPELINE_CONFIG.modelReleaseId,
      mapReleaseId: typeof saved.mapReleaseId === "string" ? saved.mapReleaseId : DEFAULT_PIPELINE_CONFIG.mapReleaseId,
      calibrationId: typeof saved.calibrationId === "string" ? saved.calibrationId : DEFAULT_PIPELINE_CONFIG.calibrationId,
      inferenceProvider: saved.inferenceProvider === "hailo_ai_hat_optional" ? "hailo_ai_hat_optional" : "onnxruntime_cpu",
    };
  } catch {
    return DEFAULT_PIPELINE_CONFIG;
  }
}

export function savePipelineConfig(config: PipelineConfig) {
  localStorage.setItem(PIPELINE_CONFIG_KEY, JSON.stringify(config));
}
