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

