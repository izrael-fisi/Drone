use anyhow::{anyhow, Result};
use image::{ImageBuffer, Rgba};
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet},
    path::{Path, PathBuf},
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter};
use tokio::task::JoinSet;

const DEFAULT_MAX_DOWNLOAD_TILES: u64 = 5_000;
const HARD_MAX_DOWNLOAD_TILES: u64 = 25_000;
const LARGE_AREA_WARNING_KM2: f64 = 100.0;
const TILE_SIZE_PX: u32 = 256;
const WEB_MERCATOR_LAT_LIMIT: f64 = 85.05112878;
const WEB_TILE_GEOREF_CRS: &str = "EPSG:3857";
const WEB_TILE_GEOREF_SOURCE: &str = "web_mercator_tiles";
const WEB_TILE_GEOREF_CONFIDENCE: f64 = 0.85;
const TILE_DOWNLOAD_CONCURRENCY: usize = 12;

#[derive(Serialize, Deserialize, Clone)]
pub struct DownloadProgress {
    pub current: u32,
    pub total: u32,
    pub percent: f32,
    pub tile_x: i32,
    pub tile_y: i32,
}

#[derive(Serialize)]
pub struct DownloadResult {
    pub mosaic_path: String,
    pub metadata_path: String,
    pub coverage_manifest_path: Option<String>,
    pub width_px: u32,
    pub height_px: u32,
    pub gsd_m_per_px: f64,
    pub origin_lat: f64,
    pub origin_lon: f64,
    pub tile_count: u32,
    pub georef_source: String,
    pub georef_confidence: f64,
    pub georef_crs: String,
    pub actual_mb: f64,
    pub provider_tile_counts: HashMap<String, u32>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct TileEstimate {
    pub tile_count: u32,
    pub nx: i32,
    pub ny: i32,
    pub estimated_mb: f64,
    pub gsd_m_per_px: f64,
    pub too_large: bool,
}

#[derive(Serialize, Deserialize, Clone, Copy)]
pub struct BBox {
    pub lat_min: f64,
    pub lat_max: f64,
    pub lon_min: f64,
    pub lon_max: f64,
}

pub type GeoPoint = [f64; 2];

#[derive(Serialize, Deserialize, Clone)]
pub struct MapProvider {
    pub id: String,
    pub label: String,
    pub kind: String,
    pub url_template: Option<String>,
    pub tile_scheme: String,
    pub attribution: String,
    pub min_zoom: u32,
    pub max_native_zoom: u32,
    pub max_zoom: u32,
    pub requires_api_key: bool,
    pub coverage_mode: String,
    pub default_priority: u32,
    pub enabled: bool,
    pub notes: String,
    pub average_tile_kb: f64,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct MapProviderBreakdown {
    pub provider_id: String,
    pub label: String,
    pub tile_count: u32,
    pub estimated_source_mb: f64,
    pub estimated_disk_mb: f64,
    pub gsd_m_per_px: f64,
    pub overzoomed: bool,
    pub key_required: bool,
    pub enabled: bool,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct MapUsageEstimate {
    pub bbox: BBox,
    pub zoom: u32,
    pub area_km2: f64,
    pub tile_count: u32,
    pub nx: i32,
    pub ny: i32,
    pub estimated_source_mb: f64,
    pub estimated_disk_mb: f64,
    pub gsd_m_per_px: f64,
    pub too_large: bool,
    pub over_100_km2: bool,
    pub warnings: Vec<String>,
    pub provider_breakdown: Vec<MapProviderBreakdown>,
}

#[derive(Deserialize)]
pub struct MapUsageEstimateRequest {
    pub bbox: BBox,
    pub zoom: u32,
    pub cut_shape: Option<String>,
    pub polygon_points: Option<Vec<GeoPoint>>,
    pub provider_ids: Option<Vec<String>>,
    pub custom_providers: Option<Vec<MapProvider>>,
    pub api_keys: Option<HashMap<String, String>>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct MapCoverageSample {
    pub provider_id: String,
    pub zoom: u32,
    pub x: i32,
    pub y: i32,
    pub status: u16,
    pub classification: String,
    pub byte_size: u64,
    pub quality_score: f64,
    pub error: Option<String>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct MapCoverageProviderZoom {
    pub provider_id: String,
    pub label: String,
    pub zoom: u32,
    pub tile_count: u32,
    pub sampled_count: u32,
    pub available_count: u32,
    pub valid_count: u32,
    pub missing_count: u32,
    pub blank_count: u32,
    pub low_detail_count: u32,
    pub average_tile_kb: f64,
    pub quality_score: f64,
    pub classification: String,
    pub samples: Vec<MapCoverageSample>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct MapCoverageSurvey {
    pub id: String,
    pub bbox: BBox,
    pub min_zoom: u32,
    pub max_zoom: u32,
    pub sample_budget: u32,
    pub generated_unix_ms: u64,
    pub recommended_provider_order: Vec<String>,
    pub provider_results: Vec<MapCoverageProviderZoom>,
}

#[derive(Deserialize)]
pub struct MapCoverageSurveyRequest {
    pub bbox: BBox,
    pub min_zoom: u32,
    pub max_zoom: u32,
    pub provider_ids: Option<Vec<String>>,
    pub sample_budget: Option<u32>,
    pub custom_providers: Option<Vec<MapProvider>>,
    pub api_keys: Option<HashMap<String, String>>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct MapPatchTileRecord {
    pub x: i32,
    pub y: i32,
    pub zoom: u32,
    pub provider_id: Option<String>,
    pub classification: String,
    pub byte_size: u64,
    pub fallback_reason: Option<String>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct MapPatchManifest {
    pub schema_version: String,
    pub bbox: BBox,
    pub cut_shape: Option<String>,
    pub polygon_points: Option<Vec<GeoPoint>>,
    pub zoom: u32,
    pub min_zoom: u32,
    pub zoom_levels: Vec<u32>,
    pub multi_layer_map: bool,
    pub area_km2: f64,
    pub provider_ids: Vec<String>,
    pub survey_id: Option<String>,
    pub tile_count: u32,
    pub actual_mb: f64,
    pub provider_tile_counts: HashMap<String, u32>,
    pub failed_tiles: Vec<MapPatchTileRecord>,
    pub tile_sources: Vec<MapPatchTileRecord>,
    pub generated_assets: Vec<String>,
}

#[derive(Deserialize)]
pub struct MapDownloadRequest {
    pub bbox: BBox,
    pub zoom: u32,
    pub min_zoom: Option<u32>,
    pub multi_layer_map: Option<bool>,
    pub output_dir: String,
    pub cut_shape: Option<String>,
    pub polygon_points: Option<Vec<GeoPoint>>,
    pub provider_ids: Option<Vec<String>>,
    pub custom_providers: Option<Vec<MapProvider>>,
    pub api_keys: Option<HashMap<String, String>>,
    pub coverage_survey: Option<MapCoverageSurvey>,
    pub confirm_over_100_km2: Option<bool>,
    pub allow_large_tile_count: Option<bool>,
}

#[derive(Clone, Copy)]
struct TileRange {
    x_min: i32,
    x_max: i32,
    y_min: i32,
    y_max: i32,
}

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct TileCoord {
    x: i32,
    y: i32,
}

#[derive(Clone, Copy)]
struct TileJob {
    coord: TileCoord,
    col: u32,
    row: u32,
    zoom: u32,
}

struct TileFetch {
    status: u16,
    bytes: Vec<u8>,
}

struct TileDownloadOutcome {
    job: TileJob,
    selected: Option<(String, Vec<u8>, String, Option<String>)>,
    last_error: Option<String>,
}

#[tauri::command]
pub fn list_map_providers() -> Vec<MapProvider> {
    built_in_providers()
}

#[tauri::command]
pub fn estimate_tiles(bbox: BBox, zoom: u32) -> TileEstimate {
    let usage = estimate_usage(MapUsageEstimateRequest {
        bbox,
        zoom,
        cut_shape: None,
        polygon_points: None,
        provider_ids: Some(vec!["esri-world-imagery".to_string()]),
        custom_providers: None,
        api_keys: None,
    });
    TileEstimate {
        tile_count: usage.tile_count,
        nx: usage.nx,
        ny: usage.ny,
        estimated_mb: usage.estimated_source_mb,
        gsd_m_per_px: usage.gsd_m_per_px,
        too_large: usage.too_large,
    }
}

#[tauri::command]
pub fn estimate_map_usage(request: MapUsageEstimateRequest) -> MapUsageEstimate {
    estimate_usage(request)
}

#[tauri::command]
pub async fn survey_map_coverage(
    request: MapCoverageSurveyRequest,
) -> Result<MapCoverageSurvey, String> {
    inner_survey_map_coverage(request)
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn download_tiles(
    app: AppHandle,
    bbox: BBox,
    zoom: u32,
    output_dir: String,
    source: String,
    api_key: Option<String>,
) -> Result<DownloadResult, String> {
    let provider_id = legacy_source_to_provider_id(&source);
    let mut api_keys = HashMap::new();
    if let Some(key) = api_key {
        api_keys.insert(provider_id.clone(), key.clone());
        api_keys.insert(source, key);
    }
    inner_download_map_region(
        app,
        MapDownloadRequest {
            bbox,
            zoom,
            min_zoom: None,
            multi_layer_map: None,
            output_dir,
            cut_shape: None,
            polygon_points: None,
            provider_ids: Some(vec![provider_id]),
            custom_providers: None,
            api_keys: Some(api_keys),
            coverage_survey: None,
            confirm_over_100_km2: Some(false),
            allow_large_tile_count: Some(false),
        },
    )
    .await
    .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn download_map_region(
    app: AppHandle,
    request: MapDownloadRequest,
) -> Result<DownloadResult, String> {
    inner_download_map_region(app, request)
        .await
        .map_err(|error| error.to_string())
}

fn built_in_providers() -> Vec<MapProvider> {
    vec![
        MapProvider {
            id: "openfreemap-vector".to_string(),
            label: "OpenFreeMap Vector".to_string(),
            kind: "vector".to_string(),
            url_template: Some("https://tiles.openfreemap.org/planet".to_string()),
            tile_scheme: "vector".to_string(),
            attribution: "OpenStreetMap contributors / OpenFreeMap".to_string(),
            min_zoom: 0,
            max_native_zoom: 14,
            max_zoom: 23,
            requires_api_key: false,
            coverage_mode: "global-vector-labels".to_string(),
            default_priority: 900,
            enabled: false,
            notes: "Vector labels and basemap only; not a high-resolution satellite source.".to_string(),
            average_tile_kb: 35.0,
        },
        MapProvider {
            id: "usgs-imagery".to_string(),
            label: "USGS Imagery".to_string(),
            kind: "raster".to_string(),
            url_template: Some("https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}".to_string()),
            tile_scheme: "arcgis".to_string(),
            attribution: "USGS National Map".to_string(),
            min_zoom: 0,
            max_native_zoom: 23,
            max_zoom: 23,
            requires_api_key: false,
            coverage_mode: "survey-required-us".to_string(),
            default_priority: 10,
            enabled: true,
            notes: "Free U.S. imagery. Coverage and detail vary; use survey before large downloads.".to_string(),
            average_tile_kb: 95.0,
        },
        MapProvider {
            id: "esri-world-imagery".to_string(),
            label: "Esri World Imagery".to_string(),
            kind: "raster".to_string(),
            url_template: Some("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}".to_string()),
            tile_scheme: "arcgis".to_string(),
            attribution: "Esri World Imagery".to_string(),
            min_zoom: 0,
            max_native_zoom: 23,
            max_zoom: 23,
            requires_api_key: false,
            coverage_mode: "global-fallback".to_string(),
            default_priority: 20,
            enabled: true,
            notes: "Global fallback imagery. High zoom availability varies by location.".to_string(),
            average_tile_kb: 80.0,
        },
        MapProvider {
            id: "mapbox-satellite".to_string(),
            label: "Mapbox Satellite".to_string(),
            kind: "raster".to_string(),
            url_template: Some("https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}.jpg90?access_token={key}".to_string()),
            tile_scheme: "zxy".to_string(),
            attribution: "Mapbox / OpenStreetMap".to_string(),
            min_zoom: 0,
            max_native_zoom: 22,
            max_zoom: 22,
            requires_api_key: true,
            coverage_mode: "paid-global".to_string(),
            default_priority: 30,
            enabled: true,
            notes: "Optional paid/API-key provider.".to_string(),
            average_tile_kb: 120.0,
        },
        MapProvider {
            id: "bing-aerial".to_string(),
            label: "Bing Aerial".to_string(),
            kind: "raster".to_string(),
            url_template: Some("https://t{s}.ssl.ak.tiles.virtualearth.net/tiles/a{q}.jpeg?g=7&token={key}".to_string()),
            tile_scheme: "quadkey".to_string(),
            attribution: "Microsoft Bing Maps".to_string(),
            min_zoom: 0,
            max_native_zoom: 20,
            max_zoom: 20,
            requires_api_key: true,
            coverage_mode: "paid-global".to_string(),
            default_priority: 40,
            enabled: true,
            notes: "Optional paid/API-key provider using quadkey addressing.".to_string(),
            average_tile_kb: 105.0,
        },
        MapProvider {
            id: "custom-zxy".to_string(),
            label: "Custom Z/X/Y Tiles".to_string(),
            kind: "custom".to_string(),
            url_template: None,
            tile_scheme: "zxy".to_string(),
            attribution: "Custom".to_string(),
            min_zoom: 0,
            max_native_zoom: 23,
            max_zoom: 23,
            requires_api_key: false,
            coverage_mode: "custom".to_string(),
            default_priority: 700,
            enabled: false,
            notes: "Template-ready slot for third-party raster tiles.".to_string(),
            average_tile_kb: 100.0,
        },
        MapProvider {
            id: "custom-arcgis".to_string(),
            label: "Custom ArcGIS Tiles".to_string(),
            kind: "custom".to_string(),
            url_template: None,
            tile_scheme: "arcgis".to_string(),
            attribution: "Custom".to_string(),
            min_zoom: 0,
            max_native_zoom: 23,
            max_zoom: 23,
            requires_api_key: false,
            coverage_mode: "custom".to_string(),
            default_priority: 710,
            enabled: false,
            notes: "Template-ready slot for third-party ArcGIS tiled services.".to_string(),
            average_tile_kb: 100.0,
        },
        MapProvider {
            id: "custom-wmts".to_string(),
            label: "Custom WMTS".to_string(),
            kind: "custom".to_string(),
            url_template: None,
            tile_scheme: "wmts".to_string(),
            attribution: "Custom".to_string(),
            min_zoom: 0,
            max_native_zoom: 23,
            max_zoom: 23,
            requires_api_key: false,
            coverage_mode: "custom".to_string(),
            default_priority: 720,
            enabled: false,
            notes: "Metadata slot for WMTS integration; downloader needs a concrete URL template.".to_string(),
            average_tile_kb: 100.0,
        },
        MapProvider {
            id: "pmtiles".to_string(),
            label: "PMTiles Archive".to_string(),
            kind: "archive".to_string(),
            url_template: None,
            tile_scheme: "pmtiles".to_string(),
            attribution: "Custom PMTiles".to_string(),
            min_zoom: 0,
            max_native_zoom: 23,
            max_zoom: 23,
            requires_api_key: false,
            coverage_mode: "static-archive".to_string(),
            default_priority: 730,
            enabled: false,
            notes: "Preferred static archive path for third-party geotiles and future offline packs.".to_string(),
            average_tile_kb: 80.0,
        },
    ]
}

fn legacy_source_to_provider_id(source: &str) -> String {
    match source {
        "esri" => "esri-world-imagery",
        "mapbox" => "mapbox-satellite",
        "bing" => "bing-aerial",
        other => other,
    }
    .to_string()
}

fn provider_registry(custom: Option<Vec<MapProvider>>) -> HashMap<String, MapProvider> {
    let mut registry = HashMap::new();
    for provider in built_in_providers() {
        registry.insert(provider.id.clone(), provider);
    }
    if let Some(custom_providers) = custom {
        for provider in custom_providers {
            registry.insert(provider.id.clone(), provider);
        }
    }
    registry
}

fn selected_providers(
    registry: &HashMap<String, MapProvider>,
    provider_ids: Option<Vec<String>>,
) -> Vec<MapProvider> {
    let ids = provider_ids.unwrap_or_else(|| {
        vec![
            "usgs-imagery".to_string(),
            "esri-world-imagery".to_string(),
            "mapbox-satellite".to_string(),
            "bing-aerial".to_string(),
        ]
    });
    let mut providers: Vec<_> = ids
        .into_iter()
        .filter_map(|id| {
            let canonical = legacy_source_to_provider_id(&id);
            registry.get(&canonical).cloned()
        })
        .filter(|provider| provider.kind != "vector")
        .collect();
    providers.sort_by_key(|provider| provider.default_priority);
    providers
}

fn api_key_for_provider<'a>(
    api_keys: &'a Option<HashMap<String, String>>,
    provider_id: &str,
) -> Option<&'a str> {
    let keys = api_keys.as_ref()?;
    keys.get(provider_id)
        .or_else(|| {
            keys.get(
                provider_id
                    .strip_suffix("-satellite")
                    .unwrap_or(provider_id),
            )
        })
        .or_else(|| keys.get(provider_id.strip_suffix("-aerial").unwrap_or(provider_id)))
        .map(|key| key.as_str())
        .filter(|key| !key.trim().is_empty())
}

fn clamp_lat(lat: f64) -> f64 {
    lat.max(-WEB_MERCATOR_LAT_LIMIT).min(WEB_MERCATOR_LAT_LIMIT)
}

fn wrap_lon(lon: f64) -> f64 {
    ((((lon + 180.0) % 360.0) + 360.0) % 360.0) - 180.0
}

fn latlon_to_tile(lat: f64, lon: f64, zoom: u32) -> (i32, i32) {
    let zoom = zoom.min(23);
    let n = (2u64.pow(zoom)) as f64;
    let x_raw = ((wrap_lon(lon) + 180.0) / 360.0 * n).floor();
    let lat_rad = clamp_lat(lat).to_radians();
    let y_raw = ((1.0 - (lat_rad.tan() + 1.0 / lat_rad.cos()).ln() / std::f64::consts::PI) / 2.0
        * n)
        .floor();
    let max = n as i32 - 1;
    let x = (x_raw as i32).max(0).min(max);
    let y = (y_raw as i32).max(0).min(max);
    (x, y)
}

fn tile_to_latlon(x: i32, y: i32, zoom: u32) -> (f64, f64) {
    let n = (2u64.pow(zoom.min(23))) as f64;
    let lon = x as f64 / n * 360.0 - 180.0;
    let lat_rad = (std::f64::consts::PI * (1.0 - 2.0 * y as f64 / n))
        .sinh()
        .atan();
    (lat_rad.to_degrees(), lon)
}

fn gsd_at_zoom(zoom: u32, lat: f64) -> f64 {
    40075016.686 * clamp_lat(lat).to_radians().cos()
        / (TILE_SIZE_PX as f64 * 2u64.pow(zoom.min(23)) as f64)
}

fn bbox_area_km2(bbox: BBox) -> f64 {
    let lat_min = bbox.lat_min.min(bbox.lat_max);
    let lat_max = bbox.lat_min.max(bbox.lat_max);
    let mut lon_span = if bbox.lon_min <= bbox.lon_max {
        bbox.lon_max - bbox.lon_min
    } else {
        360.0 - (bbox.lon_min - bbox.lon_max)
    };
    if lon_span.abs() > 360.0 {
        lon_span = 360.0;
    }
    let lat_center = ((lat_min + lat_max) / 2.0).to_radians();
    let ns = (lat_max - lat_min).abs() * 111.32;
    let ew = lon_span.abs() * 111.32 * lat_center.cos().abs();
    ns * ew
}

fn polygon_area_km2(points: &[GeoPoint]) -> Option<f64> {
    if points.len() < 3 {
        return None;
    }
    let lat_center = points.iter().map(|point| point[0]).sum::<f64>() / points.len() as f64;
    let cos_lat = lat_center.to_radians().cos().abs().max(1e-9);
    let projected: Vec<(f64, f64)> = points
        .iter()
        .map(|point| (point[1] * 111.32 * cos_lat, point[0] * 111.32))
        .collect();
    let mut area = 0.0;
    for index in 0..projected.len() {
        let (x1, y1) = projected[index];
        let (x2, y2) = projected[(index + 1) % projected.len()];
        area += x1 * y2 - x2 * y1;
    }
    Some((area / 2.0).abs())
}

fn effective_area_km2(bbox: BBox, cut_shape: Option<&str>, polygon_points: Option<&[GeoPoint]>) -> f64 {
    if cut_shape == Some("polygon") {
        if let Some(area) = polygon_points.and_then(polygon_area_km2) {
            return area;
        }
    }
    bbox_area_km2(bbox)
}

fn point_in_polygon(lat: f64, lon: f64, points: &[GeoPoint]) -> bool {
    if points.len() < 3 {
        return true;
    }
    let mut inside = false;
    let mut previous = points.len() - 1;
    for current in 0..points.len() {
        let pi = points[current];
        let pj = points[previous];
        let denom = pj[0] - pi[0];
        let intersects = denom.abs() > 1e-12
            && ((pi[0] > lat) != (pj[0] > lat))
            && (lon < (pj[1] - pi[1]) * (lat - pi[0]) / denom + pi[1]);
        if intersects {
            inside = !inside;
        }
        previous = current;
    }
    inside
}

fn tile_fraction_to_latlon(x: f64, y: f64, zoom: u32) -> (f64, f64) {
    let n = 2u64.pow(zoom.min(23)) as f64;
    let lon = x / n * 360.0 - 180.0;
    let lat_rad = (std::f64::consts::PI * (1.0 - 2.0 * y / n)).sinh().atan();
    (lat_rad.to_degrees(), lon)
}

fn apply_polygon_alpha_mask(
    mosaic: &mut ImageBuffer<Rgba<u8>, Vec<u8>>,
    ranges: &[TileRange],
    zoom: u32,
    polygon_points: &[GeoPoint],
) {
    if polygon_points.len() < 3 {
        return;
    }
    let mut col_offset = 0u32;
    for range in ranges {
        let width_tiles = (range.x_max - range.x_min + 1).max(0) as u32;
        let x_start = col_offset * TILE_SIZE_PX;
        let x_end = x_start + width_tiles * TILE_SIZE_PX;
        for py in 0..mosaic.height() {
            let tile_y = range.y_min as f64 + (py as f64 + 0.5) / TILE_SIZE_PX as f64;
            for px in x_start..x_end.min(mosaic.width()) {
                let local_px = px - x_start;
                let tile_x = range.x_min as f64 + (local_px as f64 + 0.5) / TILE_SIZE_PX as f64;
                let (lat, lon) = tile_fraction_to_latlon(tile_x, tile_y, zoom);
                if !point_in_polygon(lat, lon, polygon_points) {
                    let pixel = mosaic.get_pixel_mut(px, py);
                    pixel[3] = 0;
                }
            }
        }
        col_offset += width_tiles;
    }
}

fn tile_ranges_for_bbox(bbox: BBox, zoom: u32) -> Vec<TileRange> {
    let lat_min = bbox.lat_min.min(bbox.lat_max);
    let lat_max = bbox.lat_min.max(bbox.lat_max);
    let lon_min = wrap_lon(bbox.lon_min);
    let lon_max = wrap_lon(bbox.lon_max);
    let (x_min_raw, y_max_raw) = latlon_to_tile(lat_min, lon_min, zoom);
    let (x_max_raw, y_min_raw) = latlon_to_tile(lat_max, lon_max, zoom);
    let y_min = y_min_raw.min(y_max_raw);
    let y_max = y_min_raw.max(y_max_raw);

    if bbox.lon_min <= bbox.lon_max {
        let x_min = x_min_raw.min(x_max_raw);
        let x_max = x_min_raw.max(x_max_raw);
        vec![TileRange {
            x_min,
            x_max,
            y_min,
            y_max,
        }]
    } else {
        let n = 2i32.pow(zoom.min(23));
        let (left_x, _) = latlon_to_tile(lat_min, lon_min, zoom);
        let (right_x, _) = latlon_to_tile(lat_min, lon_max, zoom);
        vec![
            TileRange {
                x_min: left_x,
                x_max: n - 1,
                y_min,
                y_max,
            },
            TileRange {
                x_min: 0,
                x_max: right_x,
                y_min,
                y_max,
            },
        ]
    }
}

fn tile_count_for_ranges(ranges: &[TileRange]) -> u64 {
    ranges
        .iter()
        .map(|range| {
            let nx = (range.x_max - range.x_min + 1).max(0) as u64;
            let ny = (range.y_max - range.y_min + 1).max(0) as u64;
            nx * ny
        })
        .sum()
}

fn total_width_tiles(ranges: &[TileRange]) -> u32 {
    ranges
        .iter()
        .map(|range| (range.x_max - range.x_min + 1).max(0) as u32)
        .sum()
}

fn max_height_tiles(ranges: &[TileRange]) -> u32 {
    ranges
        .iter()
        .map(|range| (range.y_max - range.y_min + 1).max(0) as u32)
        .max()
        .unwrap_or(0)
}

fn each_tile(ranges: &[TileRange]) -> Vec<(TileCoord, u32, u32)> {
    let mut tiles = Vec::new();
    let mut col_offset = 0u32;
    for range in ranges {
        for y in range.y_min..=range.y_max {
            for x in range.x_min..=range.x_max {
                let col = col_offset + (x - range.x_min) as u32;
                let row = (y - range.y_min) as u32;
                tiles.push((TileCoord { x, y }, col, row));
            }
        }
        col_offset += (range.x_max - range.x_min + 1).max(0) as u32;
    }
    tiles
}

fn estimate_usage(request: MapUsageEstimateRequest) -> MapUsageEstimate {
    let registry = provider_registry(request.custom_providers.clone());
    let providers = selected_providers(&registry, request.provider_ids.clone());
    let zoom = request.zoom.min(
        providers
            .iter()
            .map(|provider| provider.max_zoom)
            .max()
            .unwrap_or(23),
    );
    let ranges = tile_ranges_for_bbox(request.bbox, zoom);
    let tile_count_64 = tile_count_for_ranges(&ranges);
    let tile_count = tile_count_64.min(u32::MAX as u64) as u32;
    let nx = total_width_tiles(&ranges).min(i32::MAX as u32) as i32;
    let ny = max_height_tiles(&ranges).min(i32::MAX as u32) as i32;
    let area_km2 = effective_area_km2(
        request.bbox,
        request.cut_shape.as_deref(),
        request.polygon_points.as_deref(),
    );
    let gsd = gsd_at_zoom(zoom, (request.bbox.lat_min + request.bbox.lat_max) / 2.0);
    let too_large = tile_count_64 > DEFAULT_MAX_DOWNLOAD_TILES;
    let over_100_km2 = area_km2 > LARGE_AREA_WARNING_KM2;
    let mut warnings = Vec::new();
    if over_100_km2 {
        warnings.push(format!(
            "Selected area is {:.1} km2. Downloads over 100 km2 require explicit confirmation.",
            area_km2
        ));
    }
    if too_large {
        warnings.push(format!(
            "Selected area covers {} tiles at zoom {}. Large downloads may be slow and memory intensive.",
            tile_count_64, zoom
        ));
    }

    let provider_breakdown: Vec<_> = providers
        .iter()
        .map(|provider| {
            let source_mb = tile_count as f64 * provider.average_tile_kb / 1024.0;
            let disk_mb = source_mb + tile_count as f64 * 0.035;
            let overzoomed = zoom > provider.max_native_zoom;
            if overzoomed {
                warnings.push(format!(
                    "{} is overzoomed above native zoom {}.",
                    provider.label, provider.max_native_zoom
                ));
            }
            MapProviderBreakdown {
                provider_id: provider.id.clone(),
                label: provider.label.clone(),
                tile_count,
                estimated_source_mb: source_mb,
                estimated_disk_mb: disk_mb,
                gsd_m_per_px: gsd,
                overzoomed,
                key_required: provider.requires_api_key
                    && api_key_for_provider(&request.api_keys, &provider.id).is_none(),
                enabled: provider.enabled && provider.url_template.is_some(),
            }
        })
        .collect();
    let estimated_source_mb = provider_breakdown
        .first()
        .map(|item| item.estimated_source_mb)
        .unwrap_or(0.0);
    let estimated_disk_mb = provider_breakdown
        .first()
        .map(|item| item.estimated_disk_mb)
        .unwrap_or(0.0);

    MapUsageEstimate {
        bbox: request.bbox,
        zoom,
        area_km2,
        tile_count,
        nx,
        ny,
        estimated_source_mb,
        estimated_disk_mb,
        gsd_m_per_px: gsd,
        too_large,
        over_100_km2,
        warnings,
        provider_breakdown,
    }
}

fn tile_to_quadkey(x: i32, y: i32, zoom: u32) -> String {
    (1..=zoom)
        .rev()
        .map(|i| {
            let mut digit = 0u8;
            let mask = 1i32 << (i - 1);
            if x & mask != 0 {
                digit += 1;
            }
            if y & mask != 0 {
                digit += 2;
            }
            char::from_digit(digit as u32, 10).unwrap()
        })
        .collect()
}

fn tile_url(
    provider: &MapProvider,
    coord: TileCoord,
    zoom: u32,
    api_key: Option<&str>,
) -> Option<String> {
    let mut url = provider.url_template.clone()?;
    if provider.requires_api_key && api_key.is_none() {
        return None;
    }
    url = url
        .replace("{z}", &zoom.to_string())
        .replace("{x}", &coord.x.to_string())
        .replace("{y}", &coord.y.to_string())
        .replace("{key}", api_key.unwrap_or(""));
    if url.contains("{q}") {
        url = url.replace("{q}", &tile_to_quadkey(coord.x, coord.y, zoom));
    }
    if url.contains("{s}") {
        let subdomain = ((coord.x.abs() + coord.y.abs()) % 4).to_string();
        url = url.replace("{s}", &subdomain);
    }
    Some(url)
}

fn safe_provider_id(id: &str) -> String {
    id.chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '_'
            }
        })
        .collect()
}

fn cache_path(cache_dir: &Path, provider: &MapProvider, zoom: u32, coord: TileCoord) -> PathBuf {
    cache_dir
        .join(safe_provider_id(&provider.id))
        .join(zoom.to_string())
        .join(coord.x.to_string())
        .join(format!("{}.bin", coord.y))
}

async fn fetch_tile_bytes(
    client: &reqwest::Client,
    provider: &MapProvider,
    coord: TileCoord,
    zoom: u32,
    api_key: Option<&str>,
    cache_dir: &Path,
) -> Result<TileFetch> {
    let path = cache_path(cache_dir, provider, zoom, coord);
    if path.exists() {
        return Ok(TileFetch {
            status: 200,
            bytes: std::fs::read(path)?,
        });
    }
    let url = tile_url(provider, coord, zoom, api_key).ok_or_else(|| {
        anyhow!(
            "{} requires configuration before tiles can be fetched",
            provider.label
        )
    })?;
    let mut request = client.get(url);
    if provider.id == "esri-world-imagery" {
        request = request.header("Referer", "https://www.arcgis.com");
    }
    let response = request.send().await?;
    let status = response.status().as_u16();
    if !(200..300).contains(&status) {
        return Ok(TileFetch {
            status,
            bytes: Vec::new(),
        });
    }
    let bytes = response.bytes().await?.to_vec();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, &bytes)?;
    Ok(TileFetch { status, bytes })
}

fn classify_tile_bytes(status: u16, bytes: &[u8]) -> (String, f64, Option<String>) {
    if !(200..300).contains(&status) {
        return ("missing".to_string(), 0.0, Some(format!("HTTP {status}")));
    }
    if bytes.len() < 300 {
        return (
            "missing".to_string(),
            0.0,
            Some("tile payload too small".to_string()),
        );
    }
    let image = match image::load_from_memory(bytes) {
        Ok(image) => image.to_rgb8(),
        Err(error) => {
            return (
                "missing".to_string(),
                0.0,
                Some(format!("decode failed: {error}")),
            )
        }
    };
    let step_x = (image.width() / 32).max(1);
    let step_y = (image.height() / 32).max(1);
    let mut count: f64 = 0.0;
    let mut sum: f64 = 0.0;
    let mut sum_sq: f64 = 0.0;
    for y in (0..image.height()).step_by(step_y as usize) {
        for x in (0..image.width()).step_by(step_x as usize) {
            let pixel = image.get_pixel(x, y);
            let lum =
                pixel[0] as f64 * 0.2126 + pixel[1] as f64 * 0.7152 + pixel[2] as f64 * 0.0722;
            count += 1.0;
            sum += lum;
            sum_sq += lum * lum;
        }
    }
    let mean = sum / count.max(1.0);
    let variance = (sum_sq / count.max(1.0) - mean * mean).max(0.0);
    if variance < 2.0 {
        return (
            "blank".to_string(),
            0.05,
            Some("near-uniform tile".to_string()),
        );
    }
    if bytes.len() < 2_500 || variance < 18.0 {
        return (
            "low-detail".to_string(),
            0.45,
            Some("low byte size or low contrast".to_string()),
        );
    }
    let size_score = ((bytes.len() as f64 / 20_000.0).ln_1p() / 2.0).min(0.25);
    let variance_score = (variance / 800.0).min(0.25);
    (
        "valid".to_string(),
        0.55 + size_score + variance_score,
        None,
    )
}

fn sample_tiles(ranges: &[TileRange], sample_budget: u32) -> Vec<TileCoord> {
    let total = tile_count_for_ranges(ranges);
    if total <= sample_budget as u64 {
        return each_tile(ranges)
            .into_iter()
            .map(|(coord, _, _)| coord)
            .collect();
    }
    let mut selected = HashSet::new();
    let budget = sample_budget.max(8) as usize;
    let per_range = (budget / ranges.len().max(1)).max(4);
    for range in ranges {
        let width = (range.x_max - range.x_min + 1).max(1) as usize;
        let height = (range.y_max - range.y_min + 1).max(1) as usize;
        let grid = (per_range as f64).sqrt().ceil().max(2.0) as usize;
        let xs: Vec<_> = (0..grid)
            .map(|i| {
                range.x_min + ((width - 1) as f64 * i as f64 / (grid - 1) as f64).round() as i32
            })
            .collect();
        let ys: Vec<_> = (0..grid)
            .map(|i| {
                range.y_min + ((height - 1) as f64 * i as f64 / (grid - 1) as f64).round() as i32
            })
            .collect();
        for x in &xs {
            for y in &ys {
                selected.insert(TileCoord { x: *x, y: *y });
            }
        }
        selected.insert(TileCoord {
            x: range.x_min,
            y: range.y_min,
        });
        selected.insert(TileCoord {
            x: range.x_max,
            y: range.y_max,
        });
        selected.insert(TileCoord {
            x: (range.x_min + range.x_max) / 2,
            y: (range.y_min + range.y_max) / 2,
        });
    }
    selected.into_iter().take(sample_budget as usize).collect()
}

async fn inner_survey_map_coverage(request: MapCoverageSurveyRequest) -> Result<MapCoverageSurvey> {
    let registry = provider_registry(request.custom_providers.clone());
    let providers = selected_providers(&registry, request.provider_ids.clone());
    let api_keys = request.api_keys.clone();
    let sample_budget = request.sample_budget.unwrap_or(24).clamp(4, 128);
    let min_zoom = request.min_zoom.min(request.max_zoom).min(23);
    let max_zoom = request.min_zoom.max(request.max_zoom).min(23);
    let cache_root = std::env::temp_dir().join("drone-map-coverage-cache");
    let client = reqwest::Client::builder()
        .user_agent("Drone-Vision-Nav/0.1 coverage-survey")
        .timeout(std::time::Duration::from_secs(15))
        .build()?;
    let mut provider_results = Vec::new();

    for zoom in min_zoom..=max_zoom {
        let ranges = tile_ranges_for_bbox(request.bbox, zoom);
        let tile_count = tile_count_for_ranges(&ranges).min(u32::MAX as u64) as u32;
        let samples = sample_tiles(&ranges, sample_budget);
        for provider in &providers {
            let mut sample_results = Vec::new();
            let key = api_key_for_provider(&api_keys, &provider.id);
            for coord in &samples {
                let result = if !provider.enabled || provider.url_template.is_none() {
                    MapCoverageSample {
                        provider_id: provider.id.clone(),
                        zoom,
                        x: coord.x,
                        y: coord.y,
                        status: 0,
                        classification: "missing".to_string(),
                        byte_size: 0,
                        quality_score: 0.0,
                        error: Some("provider is not configured for direct download".to_string()),
                    }
                } else if provider.requires_api_key && key.is_none() {
                    MapCoverageSample {
                        provider_id: provider.id.clone(),
                        zoom,
                        x: coord.x,
                        y: coord.y,
                        status: 0,
                        classification: "missing".to_string(),
                        byte_size: 0,
                        quality_score: 0.0,
                        error: Some("API key required".to_string()),
                    }
                } else {
                    match fetch_tile_bytes(&client, provider, *coord, zoom, key, &cache_root).await
                    {
                        Ok(fetch) => {
                            let (classification, quality_score, error) =
                                classify_tile_bytes(fetch.status, &fetch.bytes);
                            MapCoverageSample {
                                provider_id: provider.id.clone(),
                                zoom,
                                x: coord.x,
                                y: coord.y,
                                status: fetch.status,
                                classification,
                                byte_size: fetch.bytes.len() as u64,
                                quality_score,
                                error,
                            }
                        }
                        Err(error) => MapCoverageSample {
                            provider_id: provider.id.clone(),
                            zoom,
                            x: coord.x,
                            y: coord.y,
                            status: 0,
                            classification: "missing".to_string(),
                            byte_size: 0,
                            quality_score: 0.0,
                            error: Some(error.to_string()),
                        },
                    }
                };
                sample_results.push(result);
            }
            provider_results.push(summarize_provider_zoom(
                provider,
                zoom,
                tile_count,
                sample_results,
            ));
        }
    }

    let mut ranked: Vec<_> = provider_results
        .iter()
        .filter(|result| result.zoom == max_zoom)
        .map(|result| (result.quality_score, result.provider_id.clone()))
        .collect();
    ranked.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    let recommended_provider_order = ranked.into_iter().map(|(_, id)| id).collect();

    Ok(MapCoverageSurvey {
        id: format!("survey-{}", unix_ms()),
        bbox: request.bbox,
        min_zoom,
        max_zoom,
        sample_budget,
        generated_unix_ms: unix_ms(),
        recommended_provider_order,
        provider_results,
    })
}

fn summarize_provider_zoom(
    provider: &MapProvider,
    zoom: u32,
    tile_count: u32,
    samples: Vec<MapCoverageSample>,
) -> MapCoverageProviderZoom {
    let sampled_count = samples.len() as u32;
    let valid_count = samples
        .iter()
        .filter(|sample| sample.classification == "valid")
        .count() as u32;
    let low_detail_count = samples
        .iter()
        .filter(|sample| sample.classification == "low-detail")
        .count() as u32;
    let blank_count = samples
        .iter()
        .filter(|sample| sample.classification == "blank")
        .count() as u32;
    let missing_count = samples
        .iter()
        .filter(|sample| sample.classification == "missing")
        .count() as u32;
    let available_count = valid_count + low_detail_count;
    let average_tile_kb = if sampled_count == 0 {
        provider.average_tile_kb
    } else {
        samples
            .iter()
            .map(|sample| sample.byte_size as f64 / 1024.0)
            .sum::<f64>()
            / sampled_count as f64
    };
    let quality_score = if sampled_count == 0 {
        0.0
    } else {
        samples
            .iter()
            .map(|sample| sample.quality_score)
            .sum::<f64>()
            / sampled_count as f64
    };
    let classification = if valid_count == sampled_count && sampled_count > 0 {
        "valid"
    } else if available_count > sampled_count / 2 {
        "available"
    } else if blank_count > 0 {
        "blank"
    } else {
        "missing"
    }
    .to_string();
    MapCoverageProviderZoom {
        provider_id: provider.id.clone(),
        label: provider.label.clone(),
        zoom,
        tile_count,
        sampled_count,
        available_count,
        valid_count,
        missing_count,
        blank_count,
        low_detail_count,
        average_tile_kb,
        quality_score,
        classification,
        samples,
    }
}

async fn select_tile_for_coord(
    client: &reqwest::Client,
    providers: &[MapProvider],
    coord: TileCoord,
    zoom: u32,
    api_keys: &Option<HashMap<String, String>>,
    cache_dir: &Path,
) -> (
    Option<(String, Vec<u8>, String, Option<String>)>,
    Option<String>,
) {
    let mut low_detail_candidate: Option<(String, Vec<u8>, String)> = None;
    let mut selected: Option<(String, Vec<u8>, String, Option<String>)> = None;
    let mut last_error = None;
    for provider in providers {
        let key = api_key_for_provider(api_keys, &provider.id);
        if !provider.enabled || provider.url_template.is_none() {
            continue;
        }
        if provider.requires_api_key && key.is_none() {
            last_error = Some(format!("{} requires an API key", provider.label));
            continue;
        }
        match fetch_tile_bytes(client, provider, coord, zoom, key, cache_dir).await {
            Ok(fetch) => {
                let (classification, _score, error) =
                    classify_tile_bytes(fetch.status, &fetch.bytes);
                if classification == "valid" {
                    selected = Some((provider.id.clone(), fetch.bytes, classification, None));
                    break;
                }
                if classification == "low-detail" && low_detail_candidate.is_none() {
                    low_detail_candidate = Some((provider.id.clone(), fetch.bytes, classification));
                }
                last_error = error;
            }
            Err(error) => last_error = Some(error.to_string()),
        }
    }
    if selected.is_none() {
        if let Some((provider_id, bytes, classification)) = low_detail_candidate {
            selected = Some((
                provider_id,
                bytes,
                classification,
                Some("no higher-quality provider tile available".to_string()),
            ));
        }
    }
    (selected, last_error)
}

async fn run_tile_download_job(
    client: reqwest::Client,
    providers: Arc<Vec<MapProvider>>,
    api_keys: Arc<Option<HashMap<String, String>>>,
    cache_dir: PathBuf,
    job: TileJob,
) -> TileDownloadOutcome {
    let (selected, last_error) = select_tile_for_coord(
        &client,
        providers.as_slice(),
        job.coord,
        job.zoom,
        api_keys.as_ref(),
        &cache_dir,
    )
    .await;
    TileDownloadOutcome {
        job,
        selected,
        last_error,
    }
}

async fn download_tile_jobs_parallel(
    app: &AppHandle,
    client: &reqwest::Client,
    providers: &[MapProvider],
    api_keys: &Option<HashMap<String, String>>,
    cache_dir: &Path,
    jobs: Vec<TileJob>,
    current: &mut u32,
    total: u32,
) -> Result<Vec<TileDownloadOutcome>> {
    let providers = Arc::new(providers.to_vec());
    let api_keys = Arc::new(api_keys.clone());
    let cache_dir = cache_dir.to_path_buf();
    let mut join_set = JoinSet::new();
    let mut outcomes = Vec::with_capacity(jobs.len());

    for job in jobs {
        let client = client.clone();
        let providers = Arc::clone(&providers);
        let api_keys = Arc::clone(&api_keys);
        let cache_dir = cache_dir.clone();
        join_set.spawn(async move {
            run_tile_download_job(client, providers, api_keys, cache_dir, job).await
        });

        if join_set.len() >= TILE_DOWNLOAD_CONCURRENCY {
            if let Some(outcome) = join_set.join_next().await {
                let outcome = outcome.map_err(|error| anyhow!("Tile download task failed: {error}"))?;
                *current += 1;
                let _ = app.emit(
                    "tile-progress",
                    DownloadProgress {
                        current: *current,
                        total,
                        percent: *current as f32 / total.max(1) as f32 * 100.0,
                        tile_x: outcome.job.coord.x,
                        tile_y: outcome.job.coord.y,
                    },
                );
                outcomes.push(outcome);
            }
        }
    }

    while let Some(outcome) = join_set.join_next().await {
        let outcome = outcome.map_err(|error| anyhow!("Tile download task failed: {error}"))?;
        *current += 1;
        let _ = app.emit(
            "tile-progress",
            DownloadProgress {
                current: *current,
                total,
                percent: *current as f32 / total.max(1) as f32 * 100.0,
                tile_x: outcome.job.coord.x,
                tile_y: outcome.job.coord.y,
            },
        );
        outcomes.push(outcome);
    }

    Ok(outcomes)
}

async fn inner_download_map_region(
    app: AppHandle,
    request: MapDownloadRequest,
) -> Result<DownloadResult> {
    let usage = estimate_usage(MapUsageEstimateRequest {
        bbox: request.bbox,
        zoom: request.zoom,
        cut_shape: request.cut_shape.clone(),
        polygon_points: request.polygon_points.clone(),
        provider_ids: request.provider_ids.clone(),
        custom_providers: request.custom_providers.clone(),
        api_keys: request.api_keys.clone(),
    });
    if usage.over_100_km2 && request.confirm_over_100_km2 != Some(true) {
        return Err(anyhow!(
            "Selected area is {:.1} km2. Confirm downloads over 100 km2 before continuing.",
            usage.area_km2
        ));
    }
    let ranges = tile_ranges_for_bbox(request.bbox, usage.zoom);
    let primary_tile_count_64 = tile_count_for_ranges(&ranges);
    let min_layer_zoom = request.min_zoom.unwrap_or(usage.zoom).min(usage.zoom).min(23);
    let layer_zooms: Vec<u32> = if request.multi_layer_map == Some(true) {
        (min_layer_zoom..usage.zoom).collect()
    } else {
        Vec::new()
    };
    let layer_tile_count_64: u64 = layer_zooms
        .iter()
        .map(|zoom| tile_count_for_ranges(&tile_ranges_for_bbox(request.bbox, *zoom)))
        .sum();
    let download_tile_count_64 = primary_tile_count_64 + layer_tile_count_64;
    let tile_limit = if request.allow_large_tile_count == Some(true) {
        HARD_MAX_DOWNLOAD_TILES
    } else {
        DEFAULT_MAX_DOWNLOAD_TILES
    };
    if download_tile_count_64 > tile_limit {
        return Err(anyhow!(
            "Region too large: {} tiles through zoom {} (max {}). Reduce area or zoom.",
            download_tile_count_64,
            usage.zoom,
            tile_limit
        ));
    }

    let registry = provider_registry(request.custom_providers.clone());
    let mut providers = selected_providers(&registry, request.provider_ids.clone());
    if let Some(survey) = &request.coverage_survey {
        let order = &survey.recommended_provider_order;
        providers.sort_by_key(|provider| {
            order
                .iter()
                .position(|id| id == &provider.id)
                .unwrap_or(provider.default_priority as usize)
        });
    }

    let out = Path::new(&request.output_dir);
    std::fs::create_dir_all(out)?;
    let cache_dir = out.join(".tile_cache");
    std::fs::create_dir_all(&cache_dir)?;

    let nx = total_width_tiles(&ranges);
    let ny = max_height_tiles(&ranges);
    let total = download_tile_count_64.min(u32::MAX as u64) as u32;
    let mosaic_w = nx * TILE_SIZE_PX;
    let mosaic_h = ny * TILE_SIZE_PX;
    let mut mosaic: ImageBuffer<Rgba<u8>, Vec<u8>> = ImageBuffer::new(mosaic_w, mosaic_h);
    let client = reqwest::Client::builder()
        .user_agent("Drone-Vision-Nav/0.1 patched-map-downloader")
        .timeout(std::time::Duration::from_secs(15))
        .build()?;

    let mut current = 0u32;
    let mut provider_tile_counts = HashMap::new();
    let mut tile_sources = Vec::new();
    let mut failed_tiles = Vec::new();
    let mut actual_bytes = 0u64;
    let primary_jobs: Vec<_> = each_tile(&ranges)
        .into_iter()
        .map(|(coord, col, row)| TileJob {
            coord,
            col,
            row,
            zoom: usage.zoom,
        })
        .collect();
    for outcome in download_tile_jobs_parallel(
        &app,
            &client,
            &providers,
            &request.api_keys,
            &cache_dir,
        primary_jobs,
        &mut current,
        total,
    )
    .await?
    {
        if let Some((provider_id, bytes, classification, fallback_reason)) = outcome.selected {
            if let Ok(img) = image::load_from_memory(&bytes) {
                let rgb = img.to_rgb8();
                let col0 = outcome.job.col * TILE_SIZE_PX;
                let row0 = outcome.job.row * TILE_SIZE_PX;
                for py in 0..TILE_SIZE_PX {
                    for px in 0..TILE_SIZE_PX {
                        let src_px = rgb.get_pixel(
                            px.min(rgb.width().saturating_sub(1)),
                            py.min(rgb.height().saturating_sub(1)),
                        );
                        mosaic.put_pixel(
                            col0 + px,
                            row0 + py,
                            Rgba([src_px[0], src_px[1], src_px[2], 255]),
                        );
                    }
                }
            }
            actual_bytes += bytes.len() as u64;
            *provider_tile_counts.entry(provider_id.clone()).or_insert(0) += 1;
            tile_sources.push(MapPatchTileRecord {
                x: outcome.job.coord.x,
                y: outcome.job.coord.y,
                zoom: outcome.job.zoom,
                provider_id: Some(provider_id),
                classification,
                byte_size: bytes.len() as u64,
                fallback_reason,
            });
        } else {
            let record = MapPatchTileRecord {
                x: outcome.job.coord.x,
                y: outcome.job.coord.y,
                zoom: outcome.job.zoom,
                provider_id: None,
                classification: "missing".to_string(),
                byte_size: 0,
                fallback_reason: outcome.last_error,
            };
            failed_tiles.push(record.clone());
            tile_sources.push(record);
        }
    }

    let mut layer_jobs = Vec::new();
    for layer_zoom in &layer_zooms {
        let layer_ranges = tile_ranges_for_bbox(request.bbox, *layer_zoom);
        for (coord, _col, _row) in each_tile(&layer_ranges) {
            layer_jobs.push(TileJob {
                coord,
                col: 0,
                row: 0,
                zoom: *layer_zoom,
            });
        }
    }
    for outcome in download_tile_jobs_parallel(
        &app,
        &client,
        &providers,
        &request.api_keys,
        &cache_dir,
        layer_jobs,
        &mut current,
        total,
    )
    .await?
    {
        if let Some((provider_id, bytes, classification, fallback_reason)) = outcome.selected {
            actual_bytes += bytes.len() as u64;
            *provider_tile_counts.entry(provider_id.clone()).or_insert(0) += 1;
            tile_sources.push(MapPatchTileRecord {
                x: outcome.job.coord.x,
                y: outcome.job.coord.y,
                zoom: outcome.job.zoom,
                provider_id: Some(provider_id),
                classification,
                byte_size: bytes.len() as u64,
                fallback_reason,
            });
        } else {
            let record = MapPatchTileRecord {
                x: outcome.job.coord.x,
                y: outcome.job.coord.y,
                zoom: outcome.job.zoom,
                provider_id: None,
                classification: "missing".to_string(),
                byte_size: 0,
                fallback_reason: outcome.last_error,
            };
            failed_tiles.push(record.clone());
            tile_sources.push(record);
        }
    }

    if request.cut_shape.as_deref() == Some("polygon") {
        if let Some(points) = request.polygon_points.as_deref() {
            apply_polygon_alpha_mask(&mut mosaic, &ranges, usage.zoom, points);
        }
    }

    let mosaic_path = out.join("satellite.png");
    mosaic
        .save(&mosaic_path)
        .map_err(|error| anyhow!("Failed to save mosaic: {error}"))?;
    let (origin_lat, origin_lon) = tile_to_latlon(ranges[0].x_min, ranges[0].y_min, usage.zoom);
    let actual_mb = actual_bytes as f64 / (1024.0 * 1024.0);
    let provider_ids: Vec<_> = providers
        .iter()
        .map(|provider| provider.id.clone())
        .collect();
    let zoom_levels: Vec<u32> = layer_zooms
        .iter()
        .copied()
        .chain(std::iter::once(usage.zoom))
        .collect();
    let coverage_manifest = MapPatchManifest {
        schema_version: "map_patch_manifest_v1".to_string(),
        bbox: request.bbox,
        cut_shape: request.cut_shape.clone(),
        polygon_points: request.polygon_points.clone(),
        zoom: usage.zoom,
        min_zoom: *zoom_levels.first().unwrap_or(&usage.zoom),
        zoom_levels: zoom_levels.clone(),
        multi_layer_map: request.multi_layer_map == Some(true),
        area_km2: usage.area_km2,
        provider_ids: provider_ids.clone(),
        survey_id: request
            .coverage_survey
            .as_ref()
            .map(|survey| survey.id.clone()),
        tile_count: total,
        actual_mb,
        provider_tile_counts: provider_tile_counts.clone(),
        failed_tiles,
        tile_sources,
        generated_assets: vec!["satellite.png".to_string(), "metadata.json".to_string()],
    };
    let coverage_manifest_path = out.join("coverage_manifest.json");
    std::fs::write(
        &coverage_manifest_path,
        serde_json::to_string_pretty(&coverage_manifest)?,
    )?;

    let meta = serde_json::json!({
        "origin_lat": origin_lat,
        "origin_lon": origin_lon,
        "origin_pixel_x": 0.0,
        "origin_pixel_y": 0.0,
        "gsd_m_per_px": usage.gsd_m_per_px,
        "rotation_deg": 0.0,
        "georef_source": WEB_TILE_GEOREF_SOURCE,
        "georef_confidence": WEB_TILE_GEOREF_CONFIDENCE,
        "georef_crs": WEB_TILE_GEOREF_CRS,
        "width_px": mosaic_w,
        "height_px": mosaic_h,
        "zoom": usage.zoom,
        "min_zoom": zoom_levels.first().copied().unwrap_or(usage.zoom),
        "zoom_levels": zoom_levels,
        "multi_layer_map": request.multi_layer_map == Some(true),
        "source": provider_ids.first().cloned().unwrap_or_else(|| "unknown".to_string()),
        "cut_shape": request.cut_shape,
        "polygon_points": request.polygon_points,
        "provider_ids": provider_ids,
        "coverage_manifest": "coverage_manifest.json",
        "area_km2": usage.area_km2,
        "actual_mb": actual_mb,
        "tile_count": total,
    });
    let meta_path = out.join("metadata.json");
    std::fs::write(&meta_path, serde_json::to_string_pretty(&meta)?)?;

    Ok(DownloadResult {
        mosaic_path: mosaic_path.to_string_lossy().into_owned(),
        metadata_path: meta_path.to_string_lossy().into_owned(),
        coverage_manifest_path: Some(coverage_manifest_path.to_string_lossy().into_owned()),
        width_px: mosaic_w,
        height_px: mosaic_h,
        gsd_m_per_px: usage.gsd_m_per_px,
        origin_lat,
        origin_lon,
        tile_count: total,
        georef_source: WEB_TILE_GEOREF_SOURCE.to_string(),
        georef_confidence: WEB_TILE_GEOREF_CONFIDENCE,
        georef_crs: WEB_TILE_GEOREF_CRS.to_string(),
        actual_mb,
        provider_tile_counts,
    })
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::Rgb;
    use std::io::Cursor;

    #[test]
    fn tile_math_clamps_high_zoom() {
        let (x, y) = latlon_to_tile(37.7749, -122.4194, 23);
        assert!(x > 0);
        assert!(y > 0);
        assert!(gsd_at_zoom(23, 0.0) < 0.02);
    }

    #[test]
    fn antimeridian_bbox_splits_ranges() {
        let ranges = tile_ranges_for_bbox(
            BBox {
                lat_min: -1.0,
                lat_max: 1.0,
                lon_min: 179.0,
                lon_max: -179.0,
            },
            4,
        );
        assert_eq!(ranges.len(), 2);
        assert!(tile_count_for_ranges(&ranges) > 0);
    }

    #[test]
    fn area_warning_uses_100_km2_threshold() {
        let estimate = estimate_usage(MapUsageEstimateRequest {
            bbox: BBox {
                lat_min: 37.0,
                lat_max: 37.2,
                lon_min: -122.2,
                lon_max: -122.0,
            },
            zoom: 15,
            cut_shape: None,
            polygon_points: None,
            provider_ids: None,
            custom_providers: None,
            api_keys: None,
        });
        assert!(estimate.area_km2 > LARGE_AREA_WARNING_KM2);
        assert!(estimate.over_100_km2);
        assert!(estimate
            .warnings
            .iter()
            .any(|warning| warning.contains("100 km2")));
    }

    #[test]
    fn polygon_estimate_uses_cut_area_not_bbox_area() {
        let bbox = BBox {
            lat_min: 0.0,
            lat_max: 1.0,
            lon_min: 0.0,
            lon_max: 1.0,
        };
        let polygon_points = vec![[0.0, 0.0], [0.0, 1.0], [0.5, 0.0]];
        let estimate = estimate_usage(MapUsageEstimateRequest {
            bbox,
            zoom: 15,
            cut_shape: Some("polygon".to_string()),
            polygon_points: Some(polygon_points),
            provider_ids: None,
            custom_providers: None,
            api_keys: None,
        });
        assert!(estimate.area_km2 < bbox_area_km2(bbox));
    }

    #[test]
    fn provider_registry_exposes_max_lod_23() {
        let registry = provider_registry(None);
        assert_eq!(registry["esri-world-imagery"].max_native_zoom, 23);
        assert_eq!(registry["usgs-imagery"].max_native_zoom, 23);
    }

    #[test]
    fn classifier_distinguishes_blank_and_valid_tiles() {
        let blank = vec![255u8; 1000];
        let (class, _, _) = classify_tile_bytes(200, &blank);
        assert_eq!(class, "missing");

        let mut image = ImageBuffer::<Rgb<u8>, Vec<u8>>::new(256, 256);
        for y in 0..256 {
            for x in 0..256 {
                let texture = ((x * 37 + y * 17 + (x ^ y) * 11) % 256) as u8;
                image.put_pixel(
                    x,
                    y,
                    Rgb([
                        texture,
                        texture.wrapping_add((x % 251) as u8),
                        texture.wrapping_mul(3).wrapping_add((y % 251) as u8),
                    ]),
                );
            }
        }
        let mut bytes = Cursor::new(Vec::new());
        image::DynamicImage::ImageRgb8(image)
            .write_to(&mut bytes, image::ImageFormat::Png)
            .expect("encode test tile");
        let (class, score, error) = classify_tile_bytes(200, bytes.get_ref());
        assert_eq!(class, "valid");
        assert!(score > 0.5);
        assert!(error.is_none());
    }

    #[test]
    fn survey_order_prefers_higher_quality_provider() {
        let provider = MapProvider {
            id: "a".to_string(),
            label: "A".to_string(),
            kind: "raster".to_string(),
            url_template: Some("https://example.com/{z}/{x}/{y}".to_string()),
            tile_scheme: "zxy".to_string(),
            attribution: "".to_string(),
            min_zoom: 0,
            max_native_zoom: 23,
            max_zoom: 23,
            requires_api_key: false,
            coverage_mode: "custom".to_string(),
            default_priority: 1,
            enabled: true,
            notes: "".to_string(),
            average_tile_kb: 1.0,
        };
        let summary = summarize_provider_zoom(
            &provider,
            18,
            10,
            vec![MapCoverageSample {
                provider_id: "a".to_string(),
                zoom: 18,
                x: 1,
                y: 1,
                status: 200,
                classification: "valid".to_string(),
                byte_size: 10_000,
                quality_score: 0.9,
                error: None,
            }],
        );
        assert_eq!(summary.classification, "valid");
        assert!(summary.quality_score > 0.8);
    }
}
