use anyhow::{anyhow, Context, Result};
use image::ImageReader;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::Command;
use tiff::decoder::Decoder as TiffDecoder;
use tiff::tags::Tag;

const EARTH_RADIUS_M: f64 = 6_378_137.0;
const MANUAL_GEOREF_CONFIDENCE: f64 = 0.75;
const WEB_TILE_GEOREF_CRS: &str = "EPSG:3857";
const WEB_TILE_GEOREF_SOURCE: &str = "web_mercator_tiles";
const WEB_TILE_GEOREF_CONFIDENCE: f64 = 0.85;

#[derive(Deserialize)]
pub struct BuildDroneBundleRequest {
    pub region_dir: String,
    pub output_dir: String,
    pub repo_path: String,
    pub pipeline: String,
    pub feature_method: String,
    pub max_features: u32,
    #[serde(default)]
    pub mission_plan_json: Option<String>,
    #[serde(default)]
    pub qgc_plan_json: Option<String>,
}

#[derive(Deserialize)]
pub struct ImportMapFileRequest {
    pub map_path: String,
    pub output_dir: String,
    pub name: String,
    #[serde(default)]
    pub origin_lat: Option<f64>,
    #[serde(default)]
    pub origin_lon: Option<f64>,
    #[serde(default)]
    pub gsd_m_per_px: Option<f64>,
    #[serde(default)]
    pub origin_pixel_x: Option<f64>,
    #[serde(default)]
    pub origin_pixel_y: Option<f64>,
    #[serde(default)]
    pub rotation_deg: Option<f64>,
}

#[derive(Deserialize)]
pub struct ImportElevationAssetsRequest {
    pub region_dir: String,
    #[serde(default)]
    pub dem_path: Option<String>,
    #[serde(default)]
    pub dsm_path: Option<String>,
}

#[derive(Deserialize)]
struct RegionMetadata {
    origin_lat: f64,
    origin_lon: f64,
    gsd_m_per_px: f64,
    width_px: u64,
    height_px: u64,
    #[serde(default)]
    origin_pixel_x: Option<f64>,
    #[serde(default)]
    origin_pixel_y: Option<f64>,
    #[serde(default)]
    rotation_deg: Option<f64>,
    #[serde(default)]
    georef_source: Option<String>,
    #[serde(default)]
    georef_confidence: Option<f64>,
    #[serde(default)]
    georef_crs: Option<String>,
    #[serde(default)]
    zoom: Option<u32>,
    #[serde(default)]
    source: Option<String>,
    #[serde(default)]
    original_file: Option<String>,
}

fn region_metadata_is_web_tile_mosaic(metadata: &RegionMetadata) -> bool {
    matches!(
        metadata.source.as_deref(),
        Some("esri")
            | Some("mapbox")
            | Some("bing")
            | Some("usgs-imagery")
            | Some("esri-world-imagery")
            | Some("mapbox-satellite")
            | Some("bing-aerial")
    )
}

fn region_metadata_georef_source(metadata: &RegionMetadata) -> &str {
    metadata.georef_source.as_deref().unwrap_or_else(|| {
        if region_metadata_is_web_tile_mosaic(metadata) {
            WEB_TILE_GEOREF_SOURCE
        } else {
            "manual"
        }
    })
}

fn region_metadata_georef_confidence(metadata: &RegionMetadata) -> f64 {
    metadata.georef_confidence.unwrap_or_else(|| {
        if region_metadata_is_web_tile_mosaic(metadata) {
            WEB_TILE_GEOREF_CONFIDENCE
        } else {
            1.0
        }
    })
}

fn region_metadata_georef_crs(metadata: &RegionMetadata) -> Option<&str> {
    metadata.georef_crs.as_deref().or_else(|| {
        if region_metadata_is_web_tile_mosaic(metadata) {
            Some(WEB_TILE_GEOREF_CRS)
        } else {
            None
        }
    })
}

#[derive(Serialize)]
pub struct BuildDroneBundleResult {
    pub bundle_dir: String,
    pub manifest_path: String,
    pub stac_manifest_path: Option<String>,
    pub orthophoto_path: String,
    pub features_path: String,
    pub terrain_index_path: Option<String>,
    pub terrain_config_path: Option<String>,
    pub terrain_tile_count: Option<u64>,
    pub terrain_feature_count: Option<u64>,
    pub terrain_gsd_m: Option<f64>,
    pub terrain_tile_size_px: Option<u32>,
    pub geospatial_health: Option<serde_json::Value>,
    pub checksums_path: String,
    pub mission_plan_path: Option<String>,
    pub qgc_plan_path: Option<String>,
    pub command: String,
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
}

#[derive(Serialize)]
pub struct ImportMapFileResult {
    pub output_dir: String,
    pub mosaic_path: String,
    pub metadata_path: String,
    pub width_px: u32,
    pub height_px: u32,
    pub gsd_m_per_px: f64,
    pub origin_lat: f64,
    pub origin_lon: f64,
    pub origin_pixel_x: f64,
    pub origin_pixel_y: f64,
    pub rotation_deg: f64,
    pub georef_source: String,
    pub georef_confidence: f64,
    pub georef_crs: Option<String>,
    pub source: String,
}

#[derive(Serialize)]
pub struct ImportElevationAssetsResult {
    pub region_dir: String,
    pub dem_path: Option<String>,
    pub dsm_path: Option<String>,
    pub asset_count: u8,
    pub metadata_path: String,
}

#[derive(Clone, Debug)]
struct MapGeoref {
    origin_lat: f64,
    origin_lon: f64,
    origin_pixel_x: f64,
    origin_pixel_y: f64,
    gsd_m_per_px: f64,
    rotation_deg: f64,
    source: String,
    confidence: f64,
    crs: Option<String>,
    notes: Vec<String>,
}

#[derive(Clone, Copy, Debug, Default)]
struct GeoTiffKeys {
    model_type: Option<u16>,
    geographic_epsg: Option<u16>,
    projected_epsg: Option<u16>,
}

#[derive(Clone, Copy, Debug)]
enum GeoTiffCrs {
    Geographic { epsg: Option<u16> },
    WebMercator,
    Utm { zone: u8, northern: bool },
}

#[tauri::command]
pub async fn build_drone_bundle(
    request: BuildDroneBundleRequest,
) -> Result<BuildDroneBundleResult, String> {
    tokio::task::spawn_blocking(move || inner_build_drone_bundle(request))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn import_map_file(request: ImportMapFileRequest) -> Result<ImportMapFileResult, String> {
    tokio::task::spawn_blocking(move || inner_import_map_file(request))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn import_elevation_assets(
    request: ImportElevationAssetsRequest,
) -> Result<ImportElevationAssetsResult, String> {
    tokio::task::spawn_blocking(move || inner_import_elevation_assets(request))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}

fn inner_import_map_file(request: ImportMapFileRequest) -> Result<ImportMapFileResult> {
    let map_path = PathBuf::from(&request.map_path);
    let output_dir = PathBuf::from(&request.output_dir);
    if !map_path.exists() {
        return Err(anyhow!("Map file not found: {}", map_path.display()));
    }
    if !map_path.is_file() {
        return Err(anyhow!("Map path is not a file: {}", map_path.display()));
    }
    let extension = map_path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    validate_map_extension(&extension)?;

    let mut embedded_error: Option<String> = None;
    let embedded_georef = if is_tiff_extension(&extension) {
        match extract_geotiff_georef(&map_path) {
            Ok(value) => value,
            Err(error) => {
                embedded_error = Some(error.to_string());
                None
            }
        }
    } else {
        None
    };
    let georef = resolve_import_georef(
        &request,
        embedded_georef,
        embedded_error.as_deref(),
        is_tiff_extension(&extension),
    )?;

    std::fs::create_dir_all(&output_dir)?;
    let decoded = ImageReader::open(&map_path)
        .with_context(|| format!("Cannot open map file {}", map_path.display()))?
        .with_guessed_format()
        .context("Cannot determine map image format")?
        .decode()
        .with_context(|| format!("Cannot decode map file {}", map_path.display()))?;
    let rgb = decoded.to_rgb8();
    let width_px = rgb.width();
    let height_px = rgb.height();
    let mosaic_path = output_dir.join("satellite.png");
    rgb.save(&mosaic_path)
        .with_context(|| format!("Cannot write {}", mosaic_path.display()))?;

    let meta = json!({
        "name": request.name,
        "origin_lat": georef.origin_lat,
        "origin_lon": georef.origin_lon,
        "origin_pixel_x": georef.origin_pixel_x,
        "origin_pixel_y": georef.origin_pixel_y,
        "gsd_m_per_px": georef.gsd_m_per_px,
        "rotation_deg": georef.rotation_deg,
        "georef_source": georef.source.clone(),
        "georef_confidence": georef.confidence,
        "georef_crs": georef.crs.clone(),
        "georef_notes": georef.notes.clone(),
        "width_px": width_px,
        "height_px": height_px,
        "zoom": null,
        "source": "uploaded",
        "original_file": map_path.to_string_lossy(),
        "original_extension": extension,
    });
    let metadata_path = output_dir.join("metadata.json");
    std::fs::write(&metadata_path, serde_json::to_string_pretty(&meta)? + "\n")?;

    Ok(ImportMapFileResult {
        output_dir: output_dir.to_string_lossy().into_owned(),
        mosaic_path: mosaic_path.to_string_lossy().into_owned(),
        metadata_path: metadata_path.to_string_lossy().into_owned(),
        width_px,
        height_px,
        gsd_m_per_px: georef.gsd_m_per_px,
        origin_lat: georef.origin_lat,
        origin_lon: georef.origin_lon,
        origin_pixel_x: georef.origin_pixel_x,
        origin_pixel_y: georef.origin_pixel_y,
        rotation_deg: georef.rotation_deg,
        georef_source: georef.source,
        georef_confidence: georef.confidence,
        georef_crs: georef.crs,
        source: "uploaded".to_string(),
    })
}

fn inner_import_elevation_assets(
    request: ImportElevationAssetsRequest,
) -> Result<ImportElevationAssetsResult> {
    let region_dir = PathBuf::from(&request.region_dir);
    let metadata_path = region_dir.join("metadata.json");
    if !region_dir.exists() {
        return Err(anyhow!("Region folder not found: {}", region_dir.display()));
    }
    if !metadata_path.exists() {
        return Err(anyhow!(
            "Region folder is missing metadata.json: {}",
            metadata_path.display()
        ));
    }
    if request.dem_path.is_none() && request.dsm_path.is_none() {
        return Err(anyhow!("Choose at least one DEM or DSM GeoTIFF."));
    }

    let elevation_dir = region_dir.join("elevation");
    std::fs::create_dir_all(&elevation_dir)?;
    let dem_rel = match request.dem_path.as_deref() {
        Some(path) if !path.trim().is_empty() => {
            Some(copy_elevation_asset(path, &elevation_dir, "dem")?)
        }
        _ => existing_elevation_asset(&region_dir, "dem"),
    };
    let dsm_rel = match request.dsm_path.as_deref() {
        Some(path) if !path.trim().is_empty() => {
            Some(copy_elevation_asset(path, &elevation_dir, "dsm")?)
        }
        _ => existing_elevation_asset(&region_dir, "dsm"),
    };

    let mut metadata: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(&metadata_path)
            .with_context(|| format!("Cannot read {}", metadata_path.display()))?,
    )
    .with_context(|| format!("Cannot parse {}", metadata_path.display()))?;
    let object = metadata
        .as_object_mut()
        .ok_or_else(|| anyhow!("metadata.json must contain a JSON object"))?;
    let mut elevation_assets = serde_json::Map::new();
    if let Some(path) = dem_rel.as_deref() {
        elevation_assets.insert("dem".to_string(), json!(path));
    }
    if let Some(path) = dsm_rel.as_deref() {
        elevation_assets.insert("dsm".to_string(), json!(path));
    }
    object.insert(
        "elevation_assets".to_string(),
        serde_json::Value::Object(elevation_assets),
    );
    std::fs::write(
        &metadata_path,
        serde_json::to_string_pretty(&metadata)? + "\n",
    )?;

    Ok(ImportElevationAssetsResult {
        region_dir: region_dir.to_string_lossy().into_owned(),
        dem_path: dem_rel.clone(),
        dsm_path: dsm_rel.clone(),
        asset_count: u8::from(dem_rel.is_some()) + u8::from(dsm_rel.is_some()),
        metadata_path: metadata_path.to_string_lossy().into_owned(),
    })
}

fn resolve_import_georef(
    request: &ImportMapFileRequest,
    embedded_georef: Option<MapGeoref>,
    embedded_error: Option<&str>,
    is_tiff: bool,
) -> Result<MapGeoref> {
    if let Some(manual) = manual_georef_from_request(request)? {
        return Ok(manual);
    }
    if let Some(embedded) = embedded_georef {
        return Ok(embedded);
    }

    if let Some(error) = embedded_error {
        return Err(anyhow!(
            "Could not read a supported GeoTIFF georeference: {error}. Enter origin latitude/longitude and GSD manually to import this map."
        ));
    }
    if is_tiff {
        return Err(anyhow!(
            "This TIFF does not contain supported GeoTIFF tags. Enter origin latitude/longitude and GSD manually to import it."
        ));
    }
    Err(anyhow!(
        "Enter origin latitude, origin longitude, and GSD for non-GeoTIFF map images."
    ))
}

fn manual_georef_from_request(request: &ImportMapFileRequest) -> Result<Option<MapGeoref>> {
    let has_manual_core = request.origin_lat.is_some()
        || request.origin_lon.is_some()
        || request.gsd_m_per_px.is_some();
    if !has_manual_core {
        return Ok(None);
    }

    let origin_lat = request
        .origin_lat
        .ok_or_else(|| anyhow!("Manual georef requires origin_lat."))?;
    let origin_lon = request
        .origin_lon
        .ok_or_else(|| anyhow!("Manual georef requires origin_lon."))?;
    let gsd_m_per_px = request
        .gsd_m_per_px
        .ok_or_else(|| anyhow!("Manual georef requires gsd_m_per_px."))?;
    validate_georef_values(origin_lat, origin_lon, gsd_m_per_px)?;

    let rotation_deg = request.rotation_deg.unwrap_or(0.0);
    if !rotation_deg.is_finite() {
        return Err(anyhow!("rotation_deg must be finite"));
    }

    Ok(Some(MapGeoref {
        origin_lat,
        origin_lon,
        origin_pixel_x: request.origin_pixel_x.unwrap_or(0.0),
        origin_pixel_y: request.origin_pixel_y.unwrap_or(0.0),
        gsd_m_per_px,
        rotation_deg,
        source: "manual".to_string(),
        confidence: MANUAL_GEOREF_CONFIDENCE,
        crs: Some("EPSG:4326".to_string()),
        notes: vec!["Manual origin/GSD entered in desktop app.".to_string()],
    }))
}

fn extract_geotiff_georef(path: &Path) -> Result<Option<MapGeoref>> {
    let file = File::open(path)
        .with_context(|| format!("Cannot open TIFF metadata {}", path.display()))?;
    let mut decoder = TiffDecoder::new(file).context("Cannot read TIFF directory")?;
    let keys = decoder
        .get_tag_u16_vec(Tag::GeoKeyDirectoryTag)
        .ok()
        .map(|values| parse_geotiff_keys(&values))
        .unwrap_or_default();

    if let Ok(transform) = decoder.get_tag_f64_vec(Tag::ModelTransformationTag) {
        if transform.len() >= 16 {
            return georef_from_model_vectors(
                keys,
                "ModelTransformationTag",
                0.0,
                0.0,
                (transform[3], transform[7]),
                (transform[0], transform[4]),
                (transform[1], transform[5]),
            )
            .map(Some);
        }
    }

    let scale = decoder.get_tag_f64_vec(Tag::ModelPixelScaleTag).ok();
    let tiepoints = decoder.get_tag_f64_vec(Tag::ModelTiepointTag).ok();
    match (scale, tiepoints) {
        (Some(scale), Some(tiepoints)) if scale.len() >= 2 && tiepoints.len() >= 6 => {
            georef_from_model_vectors(
                keys,
                "ModelTiepointTag+ModelPixelScaleTag",
                tiepoints[0],
                tiepoints[1],
                (tiepoints[3], tiepoints[4]),
                (scale[0], 0.0),
                (0.0, -scale[1]),
            )
            .map(Some)
        }
        _ => Ok(None),
    }
}

fn parse_geotiff_keys(values: &[u16]) -> GeoTiffKeys {
    if values.len() < 4 {
        return GeoTiffKeys::default();
    }
    let key_count = usize::from(values[3]);
    let mut keys = GeoTiffKeys::default();
    for index in 0..key_count {
        let offset = 4 + index * 4;
        if offset + 3 >= values.len() {
            break;
        }
        let key_id = values[offset];
        let tag_location = values[offset + 1];
        let count = values[offset + 2];
        let value = values[offset + 3];
        if tag_location != 0 || count != 1 {
            continue;
        }
        match key_id {
            1024 => keys.model_type = Some(value),
            2048 => keys.geographic_epsg = Some(value),
            3072 => keys.projected_epsg = Some(value),
            _ => {}
        }
    }
    keys
}

fn georef_from_model_vectors(
    keys: GeoTiffKeys,
    tag_source: &str,
    origin_pixel_x: f64,
    origin_pixel_y: f64,
    model_origin: (f64, f64),
    pixel_x_vector: (f64, f64),
    pixel_y_vector: (f64, f64),
) -> Result<MapGeoref> {
    let crs = resolve_geotiff_crs(keys, model_origin)?;
    let (origin_lat, origin_lon) = model_to_latlon(model_origin.0, model_origin.1, crs)?;
    validate_georef_values(origin_lat, origin_lon, 1.0)?;

    let pixel_x_end = (
        model_origin.0 + pixel_x_vector.0,
        model_origin.1 + pixel_x_vector.1,
    );
    let pixel_y_end = (
        model_origin.0 + pixel_y_vector.0,
        model_origin.1 + pixel_y_vector.1,
    );
    let pixel_x_latlon = model_to_latlon(pixel_x_end.0, pixel_x_end.1, crs)?;
    let pixel_y_latlon = model_to_latlon(pixel_y_end.0, pixel_y_end.1, crs)?;
    let x_enu = local_delta_m(origin_lat, origin_lon, pixel_x_latlon.0, pixel_x_latlon.1);
    let y_enu = local_delta_m(origin_lat, origin_lon, pixel_y_latlon.0, pixel_y_latlon.1);

    let gsd_x = vector_norm(x_enu);
    let gsd_y = vector_norm(y_enu);
    if !gsd_x.is_finite() || !gsd_y.is_finite() || gsd_x <= 0.0 || gsd_y <= 0.0 {
        return Err(anyhow!("GeoTIFF pixel scale is invalid."));
    }
    let gsd_m_per_px = (gsd_x + gsd_y) / 2.0;
    validate_georef_values(origin_lat, origin_lon, gsd_m_per_px)?;
    let rotation_deg = x_enu.1.atan2(x_enu.0).to_degrees();
    let confidence = affine_georef_confidence(crs, x_enu, y_enu);
    let crs_label = geotiff_crs_label(keys, crs);

    Ok(MapGeoref {
        origin_lat,
        origin_lon,
        origin_pixel_x,
        origin_pixel_y,
        gsd_m_per_px,
        rotation_deg,
        source: "geotiff_embedded".to_string(),
        confidence,
        crs: Some(crs_label),
        notes: vec![format!("Resolved from {tag_source}.")],
    })
}

fn resolve_geotiff_crs(keys: GeoTiffKeys, model_origin: (f64, f64)) -> Result<GeoTiffCrs> {
    if let Some(epsg) = keys.projected_epsg {
        if epsg == 3857 {
            return Ok(GeoTiffCrs::WebMercator);
        }
        if (32601..=32660).contains(&epsg) {
            return Ok(GeoTiffCrs::Utm {
                zone: (epsg - 32600) as u8,
                northern: true,
            });
        }
        if (32701..=32760).contains(&epsg) {
            return Ok(GeoTiffCrs::Utm {
                zone: (epsg - 32700) as u8,
                northern: false,
            });
        }
        return Err(anyhow!(
            "Projected GeoTIFF EPSG:{epsg} is not supported yet. Supported projected CRSs are EPSG:3857 and UTM EPSG:32601-32660 / 32701-32760."
        ));
    }

    if let Some(epsg) = keys.geographic_epsg {
        return Ok(GeoTiffCrs::Geographic { epsg: Some(epsg) });
    }
    if keys.model_type == Some(2) || looks_like_lon_lat(model_origin) {
        return Ok(GeoTiffCrs::Geographic { epsg: None });
    }
    Err(anyhow!(
        "GeoTIFF CRS is missing or unsupported. Export as EPSG:4326, EPSG:3857, or WGS84 UTM, or enter manual georef values."
    ))
}

fn geotiff_crs_label(keys: GeoTiffKeys, crs: GeoTiffCrs) -> String {
    match crs {
        GeoTiffCrs::Geographic { epsg } => epsg
            .or(keys.geographic_epsg)
            .map(|value| format!("EPSG:{value}"))
            .unwrap_or_else(|| "EPSG:4326-assumed".to_string()),
        GeoTiffCrs::WebMercator => "EPSG:3857".to_string(),
        GeoTiffCrs::Utm { zone, northern } => {
            let base: u16 = if northern { 32600 } else { 32700 };
            format!("EPSG:{}", base + u16::from(zone))
        }
    }
}

fn model_to_latlon(x: f64, y: f64, crs: GeoTiffCrs) -> Result<(f64, f64)> {
    let (lat, lon) = match crs {
        GeoTiffCrs::Geographic { .. } => (y, x),
        GeoTiffCrs::WebMercator => web_mercator_to_latlon(x, y),
        GeoTiffCrs::Utm { zone, northern } => utm_to_latlon(x, y, zone, northern),
    };
    if !lat.is_finite()
        || !lon.is_finite()
        || !(-90.0..=90.0).contains(&lat)
        || !(-180.0..=180.0).contains(&lon)
    {
        return Err(anyhow!(
            "GeoTIFF georeference produced invalid WGS84 coordinates."
        ));
    }
    Ok((lat, lon))
}

fn looks_like_lon_lat(model_origin: (f64, f64)) -> bool {
    (-180.0..=180.0).contains(&model_origin.0) && (-90.0..=90.0).contains(&model_origin.1)
}

fn web_mercator_to_latlon(x: f64, y: f64) -> (f64, f64) {
    let lon = (x / EARTH_RADIUS_M).to_degrees();
    let lat = (std::f64::consts::FRAC_PI_2 - 2.0 * (-y / EARTH_RADIUS_M).exp().atan()).to_degrees();
    (lat, lon)
}

fn utm_to_latlon(easting: f64, northing: f64, zone: u8, northern: bool) -> (f64, f64) {
    let a = 6_378_137.0_f64;
    let f = 1.0 / 298.257_223_563_f64;
    let e2 = f * (2.0 - f);
    let ep2 = e2 / (1.0 - e2);
    let e1 = (1.0 - (1.0 - e2).sqrt()) / (1.0 + (1.0 - e2).sqrt());
    let k0 = 0.9996_f64;

    let x = easting - 500_000.0;
    let mut y = northing;
    if !northern {
        y -= 10_000_000.0;
    }

    let lon_origin = ((f64::from(zone) - 1.0) * 6.0 - 180.0 + 3.0).to_radians();
    let m = y / k0;
    let mu = m / (a * (1.0 - e2 / 4.0 - 3.0 * e2.powi(2) / 64.0 - 5.0 * e2.powi(3) / 256.0));
    let phi1 = mu
        + (3.0 * e1 / 2.0 - 27.0 * e1.powi(3) / 32.0) * (2.0 * mu).sin()
        + (21.0 * e1.powi(2) / 16.0 - 55.0 * e1.powi(4) / 32.0) * (4.0 * mu).sin()
        + (151.0 * e1.powi(3) / 96.0) * (6.0 * mu).sin()
        + (1097.0 * e1.powi(4) / 512.0) * (8.0 * mu).sin();

    let sin_phi1 = phi1.sin();
    let cos_phi1 = phi1.cos();
    let tan_phi1 = phi1.tan();
    let n1 = a / (1.0 - e2 * sin_phi1.powi(2)).sqrt();
    let t1 = tan_phi1.powi(2);
    let c1 = ep2 * cos_phi1.powi(2);
    let r1 = a * (1.0 - e2) / (1.0 - e2 * sin_phi1.powi(2)).powf(1.5);
    let d = x / (n1 * k0);

    let lat = phi1
        - (n1 * tan_phi1 / r1)
            * (d.powi(2) / 2.0
                - (5.0 + 3.0 * t1 + 10.0 * c1 - 4.0 * c1.powi(2) - 9.0 * ep2) * d.powi(4) / 24.0
                + (61.0 + 90.0 * t1 + 298.0 * c1 + 45.0 * t1.powi(2)
                    - 252.0 * ep2
                    - 3.0 * c1.powi(2))
                    * d.powi(6)
                    / 720.0);
    let lon = lon_origin
        + (d - (1.0 + 2.0 * t1 + c1) * d.powi(3) / 6.0
            + (5.0 - 2.0 * c1 + 28.0 * t1 - 3.0 * c1.powi(2) + 8.0 * ep2 + 24.0 * t1.powi(2))
                * d.powi(5)
                / 120.0)
            / cos_phi1;
    (lat.to_degrees(), lon.to_degrees())
}

fn local_delta_m(origin_lat: f64, origin_lon: f64, lat: f64, lon: f64) -> (f64, f64) {
    let east =
        (lon - origin_lon).to_radians() * EARTH_RADIUS_M * origin_lat.to_radians().cos().max(1e-9);
    let north = (lat - origin_lat).to_radians() * EARTH_RADIUS_M;
    (east, north)
}

fn vector_norm(value: (f64, f64)) -> f64 {
    (value.0.powi(2) + value.1.powi(2)).sqrt()
}

fn affine_georef_confidence(crs: GeoTiffCrs, x_enu: (f64, f64), y_enu: (f64, f64)) -> f64 {
    let gsd_x = vector_norm(x_enu);
    let gsd_y = vector_norm(y_enu);
    let anisotropy = gsd_x.max(gsd_y) / gsd_x.min(gsd_y).max(1e-9);
    let dot = (x_enu.0 * y_enu.0 + x_enu.1 * y_enu.1).abs() / (gsd_x * gsd_y).max(1e-9);
    let base = match crs {
        GeoTiffCrs::Geographic { epsg: None } => 0.85,
        GeoTiffCrs::Geographic { epsg: Some(4326) }
        | GeoTiffCrs::WebMercator
        | GeoTiffCrs::Utm { .. } => 0.98,
        GeoTiffCrs::Geographic { epsg: Some(_) } => 0.90,
    };
    let anisotropy_penalty = ((anisotropy - 1.0) / 0.25).clamp(0.0, 0.25);
    let skew_penalty = (dot / 0.20).clamp(0.0, 0.25);
    (base - anisotropy_penalty - skew_penalty).clamp(0.5, 1.0)
}

fn inner_build_drone_bundle(request: BuildDroneBundleRequest) -> Result<BuildDroneBundleResult> {
    validate_feature_method(&request.feature_method)?;
    validate_pipeline(&request.pipeline)?;

    let region_dir = PathBuf::from(&request.region_dir);
    let output_dir = PathBuf::from(&request.output_dir);
    let repo_path = PathBuf::from(&request.repo_path);
    let satellite_path = region_dir.join("satellite.png");
    let metadata_path = region_dir.join("metadata.json");

    if !satellite_path.exists() {
        return Err(anyhow!(
            "Missing region satellite.png: {}",
            satellite_path.display()
        ));
    }
    if !metadata_path.exists() {
        return Err(anyhow!(
            "Missing region metadata.json: {}",
            metadata_path.display()
        ));
    }
    if !repo_path.join("src").join("vision_nav").exists() {
        return Err(anyhow!(
            "Drone repo path does not look right: {}",
            repo_path.display()
        ));
    }

    let metadata_text = std::fs::read_to_string(&metadata_path)
        .with_context(|| format!("Cannot read {}", metadata_path.display()))?;
    let metadata: RegionMetadata = serde_json::from_str(&metadata_text)
        .with_context(|| format!("Cannot parse {}", metadata_path.display()))?;

    let bundle_dir = output_dir;
    let ortho_dir = bundle_dir.join("ortho");
    let elevation_bundle_dir = bundle_dir.join("elevation");
    let features_dir = bundle_dir.join("features");
    let calibration_dir = bundle_dir.join("calibration");
    let mission_dir = bundle_dir.join("mission");
    let high_compute_region_dir = bundle_dir.join("high_compute_region");
    std::fs::create_dir_all(&ortho_dir)?;
    std::fs::create_dir_all(&features_dir)?;
    std::fs::create_dir_all(&calibration_dir)?;
    std::fs::create_dir_all(&mission_dir)?;
    std::fs::create_dir_all(&high_compute_region_dir)?;

    let orthophoto_path = ortho_dir.join("map.png");
    std::fs::copy(&satellite_path, &orthophoto_path)
        .with_context(|| format!("Cannot copy {}", satellite_path.display()))?;
    std::fs::copy(
        &satellite_path,
        high_compute_region_dir.join("satellite.png"),
    )?;
    std::fs::copy(
        &metadata_path,
        high_compute_region_dir.join("metadata.json"),
    )?;
    if elevation_bundle_dir.exists() {
        std::fs::remove_dir_all(&elevation_bundle_dir)
            .with_context(|| format!("Cannot clear {}", elevation_bundle_dir.display()))?;
    }
    if region_dir.join("elevation").exists() {
        copy_dir_recursive(&region_dir.join("elevation"), &elevation_bundle_dir)?;
    }

    let down_camera_rel = copy_if_exists(
        &repo_path
            .join("config")
            .join("camera")
            .join("down_camera.yaml"),
        &calibration_dir.join("down_camera.yaml"),
        "calibration/down_camera.yaml",
    )?;
    let camera_to_body_rel = copy_if_exists(
        &repo_path
            .join("config")
            .join("camera")
            .join("camera_to_body.yaml"),
        &calibration_dir.join("camera_to_body.yaml"),
        "calibration/camera_to_body.yaml",
    )?;

    let mut calibration = serde_json::Map::new();
    if let Some(path) = down_camera_rel {
        calibration.insert("down_camera".to_string(), json!(path));
    }
    if let Some(path) = camera_to_body_rel {
        calibration.insert("camera_to_body".to_string(), json!(path));
    }

    let mut mission_plan_path: Option<PathBuf> = None;
    if let Some(text) = request.mission_plan_json.as_deref() {
        serde_json::from_str::<serde_json::Value>(text)
            .context("mission_plan_json is not valid JSON")?;
        let path = mission_dir.join("mission_plan.json");
        std::fs::write(&path, text.trim_end().to_string() + "\n")?;
        mission_plan_path = Some(path);
    }

    let mut qgc_plan_path: Option<PathBuf> = None;
    if let Some(text) = request.qgc_plan_json.as_deref() {
        serde_json::from_str::<serde_json::Value>(text)
            .context("qgc_plan_json is not valid JSON")?;
        let path = mission_dir.join("qgc.plan");
        std::fs::write(&path, text.trim_end().to_string() + "\n")?;
        qgc_plan_path = Some(path);
    }

    let manifest_path = bundle_dir.join("manifest.json");
    let mission_plan_rel = mission_plan_path
        .as_ref()
        .and_then(|path| path.strip_prefix(&bundle_dir).ok())
        .map(|path| path.to_string_lossy().replace('\\', "/"));
    let qgc_plan_rel = qgc_plan_path
        .as_ref()
        .and_then(|path| path.strip_prefix(&bundle_dir).ok())
        .map(|path| path.to_string_lossy().replace('\\', "/"));
    let manifest = json!({
        "bundle_id": "desktop-region",
        "description": "Mission bundle generated by the Drone desktop app from satellite region tiles.",
        "version": "0.1.0",
        "coordinate_frame": "simple_local_tangent",
        "mission": {
            "desktop_plan_path": mission_plan_rel,
            "qgc_plan_path": qgc_plan_rel,
            "mavlink_upload_ready": qgc_plan_path.is_some()
        },
        "pipeline": {
            "selected": request.pipeline,
            "low_compute": {
                "name": "Classical ORB/AKAZE",
                "features_path": "features/map_features.npz"
            },
            "high_compute": {
                "name": "SuperPoint + LightGlue",
                "region_path": "high_compute_region"
            }
        },
        "terrain_bundle": {
            "version": "0.1.0",
            "tile_index_path": "index/tiles.sqlite",
            "tile_size_px": 512,
            "overlap_px": 64,
            "local_origin": {
                "latitude": metadata.origin_lat,
                "longitude": metadata.origin_lon,
                "east_m": 0.0,
                "north_m": 0.0
            },
            "crs": region_metadata_georef_crs(&metadata),
            "gsd_m": metadata.gsd_m_per_px,
            "coordinate_frame": "local_enu",
            "vertical_source": "barometer_optional",
            "sensors": {
                "barometer": {
                    "enabled_optional": true,
                    "source": "mavlink_or_replay",
                    "required": false
                }
            },
            "runtime": "vision_imu_map"
        },
        "orthophoto": {
            "path": "ortho/map.png",
            "origin_lat": metadata.origin_lat,
            "origin_lon": metadata.origin_lon,
            "origin_pixel_x": metadata.origin_pixel_x.unwrap_or(0.0),
            "origin_pixel_y": metadata.origin_pixel_y.unwrap_or(0.0),
            "gsd_m": metadata.gsd_m_per_px,
            "rotation_deg": metadata.rotation_deg.unwrap_or(0.0),
            "georef_source": region_metadata_georef_source(&metadata),
            "georef_confidence": region_metadata_georef_confidence(&metadata),
            "georef_crs": region_metadata_georef_crs(&metadata)
        },
        "features": {
            "path": "features/map_features.npz",
            "method": request.feature_method,
            "max_features": request.max_features
        },
        "calibration": calibration,
        "source_region": {
            "path": request.region_dir,
            "metadata_path": "metadata.json",
            "origin_lat": metadata.origin_lat,
            "origin_lon": metadata.origin_lon,
            "gsd_m_per_px": metadata.gsd_m_per_px,
            "width_px": metadata.width_px,
            "height_px": metadata.height_px,
            "zoom": metadata.zoom,
            "source": metadata.source,
            "original_file": metadata.original_file,
            "georef_source": region_metadata_georef_source(&metadata),
            "georef_confidence": region_metadata_georef_confidence(&metadata),
            "georef_crs": region_metadata_georef_crs(&metadata)
        },
        "notes": [
            "Low-compute Pi runtime uses the classical feature index.",
            "Terrain runtime uses tiled map descriptors for coarse-to-local vision matching.",
            "High-compute runtimes can use high_compute_region/satellite.png and metadata.json with SuperPoint + LightGlue."
        ]
    });
    std::fs::write(
        &manifest_path,
        serde_json::to_string_pretty(&manifest)? + "\n",
    )?;

    let python = std::env::var("DRONE_DESKTOP_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let command_display = format!(
        "{} -m vision_nav.build_terrain_bundle --bundle {} --write-checksums",
        python,
        shellish(&bundle_dir)
    );
    let python_path = repo_path.join("src");
    let output = Command::new(&python)
        .current_dir(&repo_path)
        .env("PYTHONPATH", python_path)
        .arg("-m")
        .arg("vision_nav.build_terrain_bundle")
        .arg("--bundle")
        .arg(&bundle_dir)
        .arg("--write-checksums")
        .output()
        .with_context(|| format!("Failed to run {}", python))?;

    let exit_code = output.status.code().unwrap_or(-1);
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    if !output.status.success() {
        return Err(anyhow!(
            "Bundle manifest was written, but feature build failed with exit code {}.\n{}\n{}",
            exit_code,
            stdout,
            stderr
        ));
    }

    let terrain_output: Option<serde_json::Value> = serde_json::from_str(&stdout).ok();
    let terrain_index_path = terrain_output
        .as_ref()
        .and_then(|value| value.pointer("/tile_index/path"))
        .and_then(|value| value.as_str())
        .map(|value| value.to_string());
    let terrain_config_path = terrain_output
        .as_ref()
        .and_then(|value| value.get("config_path"))
        .and_then(|value| value.as_str())
        .map(|value| value.to_string());
    let stac_manifest_path = terrain_output
        .as_ref()
        .and_then(|value| value.get("stac_manifest_path"))
        .and_then(|value| value.as_str())
        .map(|value| value.to_string());
    let terrain_tile_count = terrain_output
        .as_ref()
        .and_then(|value| value.pointer("/tile_index/tile_count"))
        .and_then(|value| value.as_u64());
    let terrain_feature_count = terrain_output
        .as_ref()
        .and_then(|value| value.pointer("/tile_index/feature_count"))
        .and_then(|value| value.as_u64());
    let terrain_gsd_m = terrain_output
        .as_ref()
        .and_then(|value| value.pointer("/terrain_bundle/gsd_m"))
        .and_then(|value| value.as_f64())
        .or(Some(metadata.gsd_m_per_px));
    let terrain_tile_size_px = terrain_output
        .as_ref()
        .and_then(|value| value.pointer("/terrain_bundle/tile_size_px"))
        .and_then(|value| value.as_u64())
        .map(|value| value as u32)
        .or(Some(512));
    let geospatial_health = terrain_output
        .as_ref()
        .and_then(|value| value.get("geospatial_health"))
        .cloned();

    Ok(BuildDroneBundleResult {
        bundle_dir: bundle_dir.to_string_lossy().into_owned(),
        manifest_path: manifest_path.to_string_lossy().into_owned(),
        stac_manifest_path,
        orthophoto_path: orthophoto_path.to_string_lossy().into_owned(),
        features_path: bundle_dir
            .join("features")
            .join("map_features.npz")
            .to_string_lossy()
            .into_owned(),
        terrain_index_path,
        terrain_config_path,
        terrain_tile_count,
        terrain_feature_count,
        terrain_gsd_m,
        terrain_tile_size_px,
        geospatial_health,
        checksums_path: bundle_dir
            .join("checksums.sha256")
            .to_string_lossy()
            .into_owned(),
        mission_plan_path: mission_plan_path.map(|path| path.to_string_lossy().into_owned()),
        qgc_plan_path: qgc_plan_path.map(|path| path.to_string_lossy().into_owned()),
        command: command_display,
        stdout,
        stderr,
        exit_code,
    })
}

fn validate_georef_values(origin_lat: f64, origin_lon: f64, gsd_m_per_px: f64) -> Result<()> {
    if !origin_lat.is_finite() || !(-90.0..=90.0).contains(&origin_lat) {
        return Err(anyhow!("origin_lat must be between -90 and 90 degrees"));
    }
    if !origin_lon.is_finite() || !(-180.0..=180.0).contains(&origin_lon) {
        return Err(anyhow!("origin_lon must be between -180 and 180 degrees"));
    }
    if !gsd_m_per_px.is_finite() || gsd_m_per_px <= 0.0 {
        return Err(anyhow!("gsd_m_per_px must be greater than zero"));
    }
    Ok(())
}

fn is_tiff_extension(extension: &str) -> bool {
    matches!(extension, "tif" | "tiff")
}

fn validate_map_extension(extension: &str) -> Result<()> {
    match extension {
        "png" | "jpg" | "jpeg" | "tif" | "tiff" | "bmp" | "webp" | "gif" => Ok(()),
        "" => Err(anyhow!("Map file has no extension. Supported formats: PNG, JPEG, TIFF/GeoTIFF, BMP, WebP, GIF.")),
        other => Err(anyhow!(
            "Unsupported map file extension .{other}. Supported formats: PNG, JPEG, TIFF/GeoTIFF, BMP, WebP, GIF."
        )),
    }
}

fn validate_elevation_extension(extension: &str) -> Result<()> {
    if is_tiff_extension(extension) {
        Ok(())
    } else if extension.is_empty() {
        Err(anyhow!(
            "Elevation file has no extension. Use a TIFF/GeoTIFF file."
        ))
    } else {
        Err(anyhow!(
            "Unsupported elevation file extension .{extension}. Use a TIFF/GeoTIFF file."
        ))
    }
}

fn existing_elevation_asset(region_dir: &Path, kind: &str) -> Option<String> {
    for extension in ["tif", "tiff"] {
        let rel = format!("elevation/{kind}.{extension}");
        if region_dir.join(&rel).exists() {
            return Some(rel);
        }
    }
    None
}

fn copy_elevation_asset(src: &str, elevation_dir: &Path, kind: &str) -> Result<String> {
    let src_path = PathBuf::from(src);
    if !src_path.exists() {
        return Err(anyhow!("Elevation file not found: {}", src_path.display()));
    }
    if !src_path.is_file() {
        return Err(anyhow!(
            "Elevation path is not a file: {}",
            src_path.display()
        ));
    }
    let extension = src_path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    validate_elevation_extension(&extension)?;
    for stale_extension in ["tif", "tiff"] {
        let stale = elevation_dir.join(format!("{kind}.{stale_extension}"));
        if stale.exists() {
            std::fs::remove_file(&stale)
                .with_context(|| format!("Cannot replace {}", stale.display()))?;
        }
    }
    let dest_name = format!("{kind}.{extension}");
    let dest = elevation_dir.join(&dest_name);
    std::fs::copy(&src_path, &dest)
        .with_context(|| format!("Cannot copy {} to {}", src_path.display(), dest.display()))?;
    Ok(format!("elevation/{dest_name}"))
}

fn copy_if_exists(src: &Path, dst: &Path, rel: &str) -> Result<Option<String>> {
    if !src.exists() {
        return Ok(None);
    }
    std::fs::copy(src, dst)
        .with_context(|| format!("Cannot copy {} to {}", src.display(), dst.display()))?;
    Ok(Some(rel.to_string()))
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<()> {
    std::fs::create_dir_all(dst).with_context(|| format!("Cannot create {}", dst.display()))?;
    for entry in std::fs::read_dir(src).with_context(|| format!("Cannot read {}", src.display()))? {
        let entry = entry?;
        let source_path = entry.path();
        let dest_path = dst.join(entry.file_name());
        if source_path.is_dir() {
            copy_dir_recursive(&source_path, &dest_path)?;
        } else if source_path.is_file() {
            std::fs::copy(&source_path, &dest_path).with_context(|| {
                format!(
                    "Cannot copy {} to {}",
                    source_path.display(),
                    dest_path.display()
                )
            })?;
        }
    }
    Ok(())
}

fn validate_feature_method(method: &str) -> Result<()> {
    match method {
        "orb" | "akaze" | "sift" => Ok(()),
        other => Err(anyhow!("Unsupported feature method: {other}")),
    }
}

fn validate_pipeline(pipeline: &str) -> Result<()> {
    match pipeline {
        "classical" | "neural" => Ok(()),
        other => Err(anyhow!("Unsupported pipeline: {other}")),
    }
}

fn shellish(path: &Path) -> String {
    path.to_string_lossy().replace(' ', "\\ ")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_close(actual: f64, expected: f64, tolerance: f64) {
        assert!(
            (actual - expected).abs() <= tolerance,
            "expected {actual} to be within {tolerance} of {expected}"
        );
    }

    #[test]
    fn parses_geotiff_epsg_keys() {
        let keys = parse_geotiff_keys(&[
            1, 1, 0, 3, 1024, 0, 1, 2, 2048, 0, 1, 4326, 3072, 0, 1, 32618,
        ]);

        assert_eq!(keys.model_type, Some(2));
        assert_eq!(keys.geographic_epsg, Some(4326));
        assert_eq!(keys.projected_epsg, Some(32618));
    }

    #[test]
    fn converts_epsg4326_tiepoint_scale_to_runtime_georef() {
        let georef = georef_from_model_vectors(
            GeoTiffKeys {
                model_type: Some(2),
                geographic_epsg: Some(4326),
                projected_epsg: None,
            },
            "test",
            0.0,
            0.0,
            (-75.0, 40.0),
            (0.00001, 0.0),
            (0.0, -0.00000766044),
        )
        .expect("EPSG:4326 georef should resolve");

        assert_close(georef.origin_lat, 40.0, 1e-9);
        assert_close(georef.origin_lon, -75.0, 1e-9);
        assert!(georef.gsd_m_per_px > 0.8);
        assert!(georef.gsd_m_per_px < 1.2);
        assert_close(georef.rotation_deg, 0.0, 0.01);
        assert_eq!(georef.source, "geotiff_embedded");
        assert_eq!(georef.crs.as_deref(), Some("EPSG:4326"));
        assert!(georef.confidence > MANUAL_GEOREF_CONFIDENCE);
    }

    #[test]
    fn converts_utm_origin_to_wgs84() {
        let (lat, lon) = utm_to_latlon(500_000.0, 0.0, 31, true);

        assert_close(lat, 0.0, 1e-6);
        assert_close(lon, 3.0, 1e-6);
    }

    #[test]
    fn infers_web_tile_region_metadata_georef() {
        let metadata = RegionMetadata {
            origin_lat: 37.0,
            origin_lon: -122.0,
            gsd_m_per_px: 0.5,
            width_px: 256,
            height_px: 256,
            origin_pixel_x: None,
            origin_pixel_y: None,
            rotation_deg: None,
            georef_source: None,
            georef_confidence: None,
            georef_crs: None,
            zoom: Some(17),
            source: Some("esri".to_string()),
            original_file: None,
        };

        assert_eq!(
            region_metadata_georef_source(&metadata),
            "web_mercator_tiles"
        );
        assert_eq!(region_metadata_georef_confidence(&metadata), 0.85);
        assert_eq!(region_metadata_georef_crs(&metadata), Some("EPSG:3857"));

        let explicit = RegionMetadata {
            georef_source: Some("manual_override".to_string()),
            georef_confidence: Some(0.72),
            georef_crs: Some("LOCAL_ENU_WGS84".to_string()),
            ..metadata
        };
        assert_eq!(region_metadata_georef_source(&explicit), "manual_override");
        assert_eq!(region_metadata_georef_confidence(&explicit), 0.72);
        assert_eq!(
            region_metadata_georef_crs(&explicit),
            Some("LOCAL_ENU_WGS84")
        );
    }

    #[test]
    fn extracts_georef_from_real_geotiff_tags() {
        use tiff::encoder::{colortype, TiffEncoder};

        let path = std::env::temp_dir().join(format!(
            "drone_vision_nav_geotiff_test_{}_{}.tif",
            std::process::id(),
            "epsg4326"
        ));
        {
            let file = File::create(&path).expect("create test GeoTIFF");
            let mut encoder = TiffEncoder::new(file).expect("create TIFF encoder");
            let mut image = encoder
                .new_image::<colortype::Gray8>(2, 2)
                .expect("create TIFF image");
            image
                .encoder()
                .write_tag(
                    Tag::ModelPixelScaleTag,
                    &[0.00001_f64, 0.00000766044, 0.0][..],
                )
                .expect("write pixel scale");
            image
                .encoder()
                .write_tag(
                    Tag::ModelTiepointTag,
                    &[0.0_f64, 0.0, 0.0, -75.0, 40.0, 0.0][..],
                )
                .expect("write tiepoint");
            image
                .encoder()
                .write_tag(
                    Tag::GeoKeyDirectoryTag,
                    &[1_u16, 1, 0, 2, 1024, 0, 1, 2, 2048, 0, 1, 4326][..],
                )
                .expect("write geokeys");
            image
                .write_data(&[0_u8, 32, 128, 255])
                .expect("write pixels");
        }

        let georef = extract_geotiff_georef(&path)
            .expect("extract georef")
            .expect("expected embedded georef");
        let _ = std::fs::remove_file(path);

        assert_close(georef.origin_lat, 40.0, 1e-9);
        assert_close(georef.origin_lon, -75.0, 1e-9);
        assert_eq!(georef.crs.as_deref(), Some("EPSG:4326"));
        assert_eq!(georef.source, "geotiff_embedded");
    }

    #[test]
    fn imports_elevation_assets_and_updates_metadata() {
        let root = std::env::temp_dir().join(format!(
            "drone_vision_nav_elevation_test_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system time")
                .as_nanos()
        ));
        let region_dir = root.join("region");
        std::fs::create_dir_all(&region_dir).expect("create region dir");
        std::fs::write(
            region_dir.join("metadata.json"),
            r#"{
  "origin_lat": 40.0,
  "origin_lon": -75.0,
  "gsd_m_per_px": 0.25,
  "width_px": 64,
  "height_px": 64
}
"#,
        )
        .expect("write metadata");
        let dem_src = root.join("source_dem.tif");
        let dsm_src = root.join("source_dsm.tiff");
        std::fs::write(&dem_src, b"dem").expect("write dem");
        std::fs::write(&dsm_src, b"dsm").expect("write dsm");

        let result = inner_import_elevation_assets(ImportElevationAssetsRequest {
            region_dir: region_dir.to_string_lossy().into_owned(),
            dem_path: Some(dem_src.to_string_lossy().into_owned()),
            dsm_path: Some(dsm_src.to_string_lossy().into_owned()),
        })
        .expect("import elevation assets");

        assert_eq!(result.asset_count, 2);
        assert_eq!(result.dem_path.as_deref(), Some("elevation/dem.tif"));
        assert_eq!(result.dsm_path.as_deref(), Some("elevation/dsm.tiff"));
        assert!(region_dir.join("elevation").join("dem.tif").exists());
        assert!(region_dir.join("elevation").join("dsm.tiff").exists());

        let metadata: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(region_dir.join("metadata.json")).expect("read metadata"),
        )
        .expect("parse metadata");
        assert_eq!(
            metadata
                .pointer("/elevation_assets/dem")
                .and_then(|value| value.as_str()),
            Some("elevation/dem.tif")
        );
        assert_eq!(
            metadata
                .pointer("/elevation_assets/dsm")
                .and_then(|value| value.as_str()),
            Some("elevation/dsm.tiff")
        );

        let _ = std::fs::remove_dir_all(root);
    }
}
