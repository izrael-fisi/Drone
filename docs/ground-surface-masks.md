# Ground-Surface Support Masks

Bare-earth elevation is not roof height. A correct image correspondence on an
elevated structure can become a wrong 3D point, and correlated structural errors
can survive robust fitting. AeroMap3D motivates filtering unsuitable surfaces
before DEM lifting; its own reported limits include missing annotations and
dominant unmapped structures. [Primary research](https://arxiv.org/html/2607.14009v1).

Our implementation adds a conservative, offline map prior to XFeat/LighterGlue
and existing PnP. It does not reproduce the paper's dense matcher, automatically
classify ground, promise its accuracy or enable aircraft position authority.

## Prepare Reviewed Geometry

Export an RFC7946 GeoJSON `FeatureCollection` in WGS84 longitude/latitude.
Each feature must be a valid 2D Polygon or MultiPolygon with a `surface` property:

- `ground_support`: reviewed regions whose visible surfaces may use ground height.
- `exclude`: buildings, vegetation, water, uncertain surfaces, stale-map regions
  or any other area to remove from that support.

Holes and separate polygons are supported. Outside explicit ground support is
unknown and rejected. Building footprints alone are not enough: the builder
will not treat every unannotated pixel as verified ground. Review the actual
imagery, acquisition dates, visibility, map alignment and expected camera view.
Annotations are still fallible; masks do not remove the remaining geometry,
temporal ambiguity or uncertainty gates.

## Build Offline

Install the existing map-production extra, which now includes Shapely for
polygon validity, reprojection and buffering. Rasterio and PyProj remain the
raster/coordinate dependencies. No network lookup occurs during mask creation
or flight-time use.

```bash
vision-nav-build-surface-mask \
  --imagery /data/maps/test-area/ortho.tif \
  --annotations /data/maps/test-area/reviewed-ground.geojson \
  --output /data/maps/test-area/ground-mask \
  --margin-m 3 \
  --source "Reviewed local imagery and survey polygons" \
  --acquisition-date "2026-09-05" \
  --license "Record actual imagery/annotation usage permissions" \
  --alignment-evidence "Record the measured co-registration report" \
  --support-basis "Record who/what established these ground-support regions"
```

Paths, date, margin and provenance above are examples, not qualified settings.
Choose a margin using measured georeferencing uncertainty, object lean and
visibility. The builder shrinks support and expands exclusions by that margin
plus half an imagery pixel diagonal. The added pixel allowance protects the
cell footprint; it does not account for unknown structures or camera parallax.

Input imagery must be north-up in a projected metre CRS. Reproject geographic
or rotated imagery first, then build the tile layout against that exact source.
Limits are 10 MiB/1,000 polygon features and 268 million imagery pixels. Output
is written in bounded raster windows, with exclusions applied after support.

The new output directory contains `ground-support.tif` and `surface-mask.json`.
The JSON binds imagery, annotations and raster hashes, source/date/license,
support basis, margins and supported-pixel counts. It is written only after
successful, nonempty raster generation and final input checksum checks. A failed
build may leave an unmanifested partial raster; do not activate it. Retry into a
new directory after correcting the inputs.

Module entrypoint: `python -m vision_nav.neural.surface_mask`.

## Elevation Contract

Existing `vision_nav_elevation_source_v1` remains **DSM-only**. It is unchanged
for existing sources without a support mask.

For an explicit masked source:

1. Use `schema_version: vision_nav_elevation_source_v2`.
2. Set `surface_type` to `dtm` for bare earth or `dsm` for visible-surface heights.
3. Embed the complete generated `surface-mask.json` object as `surface_mask`.
4. Resolve its `raster_path` relative to the elevation JSON, for example
   `ground-mask/ground-support.tif`. Keep the original mask/imagery checksums.
5. Retain all existing elevation fields: raster checksum, source/date/license,
   alignment evidence, positive declared horizontal/vertical error priors,
   `units: m`, and WGS84 ellipsoidal height/origin declarations.

`dtm` requires this mask. `dsm` may optionally use it to restrict matching to
reviewed ground regions. There is no orthometric/geoid conversion or assumed
height plane. An unmasked DTM is still rejected.

The embedded mask uses `vision_nav_surface_support_mask_v1`,
`supported_surface: bare_earth_ground`, `valid_value: 1` and
`unknown_policy: reject`. It must be a self-contained georeferenced uint8
GeoTIFF. Nodata cannot mean valid, and unchecksummed external raster sidecars
are rejected. Only an unmasked value of exactly 1 is supported; 0, 255, other
labels, nodata and coordinates outside the mask are excluded. A different mask
CRS is allowed and transformed explicitly by the sampler.

## Pipeline Behavior

The same sampler serves learned-map compilation and live/replay local geometry:

```text
Learned map pixels
  -> exact tile/resize/CRS transform
  -> explicit ground-support mask
  -> valid surface height
  -> ENU landmarks + retained feature/descriptor pairs
  -> learned correspondence and PnP
```

Compilation records mask provenance and per-tile rejection counts. Insufficient
support fails compilation; live PnP with insufficient correspondences is withheld,
not silently changed into a plane. Changed local source files invalidate cached
replay tiles. This uses file identity checks between reads to avoid hashing
large rasters every frame; it detects ordinary local changes, not adversarial
filesystem tampering or a signed remote release.

The compiler's landmark provenance travels with existing checksummed map assets;
compiled feature caches do not need to resample the original mask on Pi. The
separate live/replay verifier that computes map features on demand still needs
its bound source rasters. Moving those inputs requires a new checked configuration.

Descriptor/landmark arrays remain aligned after filtering. Results distinguish
out-of-imagery coordinates, excluded/unknown support and invalid elevation.
Declared map error priors remain map priors, **not calibrated pose covariance**.
Every navigation output stays shadow-only. Hardware measurements and held-out
routes are still required before this prior can support a field release.

## Verification

September 5, 2026, local macOS CPU environments:

- 37 map/mask tests passed, including the CLI builder, polygon buffering,
  source/hash/CRS checks, mask/descriptor alignment, cached-source invalidation,
  empty support rejection and existing DSM compatibility.
- One of those tests runs the actual XFeat/LighterGlue ONNX exports using their
  checksum-bound parity image, synthetic ground heights and an exclusion mask.
  It verifies retained correspondence/PnP execution, not UAV-to-map accuracy.
- 64 existing model/native/correspondence regressions and all 104 main checks
  passed. Three optional direct-PyTorch checkpoint tests were skipped because
  their reference configuration was not supplied; ONNX/native tests ran.
- CLI help and `git diff --check` passed; no owned child processes remained.

Generated evidence is under `research/outputs/surface-mask-tests-20260905-r2/`
and `research/outputs/surface-mask-model-regression-20260905-r1/` (not committed).
The actual-model mask result is `actual-mask-evidence.json` inside its test
directory. These tests do not validate real semantic labels, mask margins,
height accuracy, sustained Pi runtime or aircraft performance.
