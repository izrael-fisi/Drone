import type { FeatureMethod, VisionPipeline } from "./types";

export type PipelineConfig = {
  pipeline: VisionPipeline;
  featureMethod: FeatureMethod;
  maxFeatures: number;
  matcherRatio: number;
  minMatches: number;
  superpointPath: string;
  lightgluePath: string;
};

export const PIPELINE_CONFIG_KEY = "drone_vision_pipeline_config";

export const DEFAULT_PIPELINE_CONFIG: PipelineConfig = {
  pipeline: "classical",
  featureMethod: "orb",
  maxFeatures: 3000,
  matcherRatio: 0.75,
  minMatches: 20,
  superpointPath: "weights/superpoint_v1.pth",
  lightgluePath: "weights/lightglue_v0.1_disk.pth",
};

export function loadPipelineConfig(): PipelineConfig {
  try {
    const raw = localStorage.getItem(PIPELINE_CONFIG_KEY);
    return raw ? { ...DEFAULT_PIPELINE_CONFIG, ...JSON.parse(raw) } : DEFAULT_PIPELINE_CONFIG;
  } catch {
    return DEFAULT_PIPELINE_CONFIG;
  }
}

export function savePipelineConfig(config: PipelineConfig) {
  localStorage.setItem(PIPELINE_CONFIG_KEY, JSON.stringify(config));
}
