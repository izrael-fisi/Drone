export interface Profile {
  name: string;
  email: string;
  org: string;
  accent_color: string;
  onboarding_complete: boolean;
  mapbox_key?: string;
  bing_key?: string;
}

export type TileSource = "esri" | "mapbox" | "bing";
export type MapSource = TileSource | "uploaded" | "folder";

export type DeviceKind = "pi5" | "local";
export type AuthMethod = "password" | "key";
export type VisionPipeline = "classical" | "neural";
export type FeatureMethod = "orb" | "akaze" | "sift";

export interface Device {
  id: string;
  name: string;
  kind: DeviceKind;
  host?: string;
  port?: number;
  username?: string;
  auth?: { type: "Password"; password: string } | { type: "Key"; key_path: string; passphrase?: string };
  remote_project_path?: string;
  known_fingerprint?: string;
  mavlink_endpoint?: string; // e.g. "serial:/dev/ttyAMA0:921600" | "udp:14550" | "tcp:host:port"
  autopilot?: "px4" | "ardupilot";
  vision_pipeline?: VisionPipeline;
  feature_method?: FeatureMethod;
}

export interface Region {
  id: string;
  name: string;
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
  zoom: number;
  source?: MapSource;
  output_path: string;
  last_downloaded?: string;
  tile_count?: number;
  gsd_m_per_px?: number;
  georef_source?: string;
  georef_confidence?: number;
  georef_crs?: string;
  file_size_mb?: number;
  location_label?: string;
}

export interface ModelSet {
  id: string;
  name: string;
  superpoint_path: string;
  lightglue_path: string;
  is_active: boolean;
  downloaded: boolean;
}

export interface TileEstimate {
  tile_count: number;
  nx: number;
  ny: number;
  estimated_mb: number;
  gsd_m_per_px: number;
  too_large: boolean;
}

export interface DownloadProgress {
  current: number;
  total: number;
  percent: number;
  tile_x: number;
  tile_y: number;
}

export interface UploadProgress {
  file: string;
  bytes_sent: number;
  total_bytes: number;
  percent: number;
}

export interface BuildDroneBundleRequest {
  region_dir: string;
  output_dir: string;
  repo_path: string;
  pipeline: VisionPipeline;
  feature_method: FeatureMethod;
  max_features: number;
  mission_plan_json?: string;
  qgc_plan_json?: string;
}

export interface BuildDroneBundleResult {
  bundle_dir: string;
  manifest_path: string;
  stac_manifest_path?: string;
  orthophoto_path: string;
  features_path: string;
  terrain_index_path?: string;
  terrain_config_path?: string;
  terrain_tile_count?: number;
  terrain_feature_count?: number;
  terrain_gsd_m?: number;
  terrain_tile_size_px?: number;
  checksums_path: string;
  mission_plan_path?: string;
  qgc_plan_path?: string;
  command: string;
  stdout: string;
  stderr: string;
  exit_code: number;
}

export interface ImportMapFileRequest {
  map_path: string;
  output_dir: string;
  name: string;
  origin_lat?: number;
  origin_lon?: number;
  gsd_m_per_px?: number;
  origin_pixel_x?: number;
  origin_pixel_y?: number;
  rotation_deg?: number;
}

export interface ImportMapFileResult {
  output_dir: string;
  mosaic_path: string;
  metadata_path: string;
  width_px: number;
  height_px: number;
  gsd_m_per_px: number;
  origin_lat: number;
  origin_lon: number;
  origin_pixel_x: number;
  origin_pixel_y: number;
  rotation_deg: number;
  georef_source: string;
  georef_confidence: number;
  georef_crs?: string;
  source: "uploaded";
}

export interface CommandResult {
  exit_code: number;
  stdout: string;
  stderr: string;
}

export interface BBox {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}
