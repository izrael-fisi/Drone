# Map Artifact And Usage Model

## Purpose

Define how cloud map artifacts, mission overlays, signed upload/download URLs,
usage events, quota checks, and data classification should work.

## Artifact Types

Supported future cloud artifacts:

- Raw downloaded tile cache.
- Patched map region.
- `satellite.png`.
- `metadata.json`.
- `coverage_manifest.json`.
- PMTiles or MBTiles archive.
- Mission overlays.
- Waypoints.
- Terrain/elevation assets.
- Support bundles.

## Data Classification

Every map, mission, and artifact must carry one classification:

- `public`
- `commercial_sensitive`
- `cui`
- `itar`
- `restricted`

Commercial cloud may store:

- `public`
- `commercial_sensitive`

Commercial cloud must not store without explicit compliance approval:

- `cui`
- `itar`
- `restricted`

GovCloud is the default target for regulated classifications.

## Map Metadata

Minimum cloud map fields:

- `map_id`
- `org_id`
- `owner_user_id`
- `name`
- `classification`
- `bbox`
- `area_km2`
- `cut_shape`
- `polygon_points`
- `zoom_min`
- `zoom_max`
- `zoom_levels`
- `provider_ids`
- `artifact_bytes`
- `estimated_download_mb`
- `estimated_disk_mb`
- `checksum_sha256`
- `storage_backend`
- `storage_key`
- `created_at`
- `updated_at`

## Signed Upload Flow

1. Desktop calculates area, selected providers, zoom levels, estimated bytes,
   and classification.
2. Desktop calls `POST /maps/upload/initiate`.
3. API validates user, org, device, quota, environment, size, and classification.
4. API returns short-lived signed upload URL and artifact id.
5. Desktop uploads directly to object storage.
6. Desktop calls `POST /maps/upload/complete` with checksum and manifest.
7. API verifies checksum, final size, and idempotency key.
8. API records map artifact and audit event.

## Signed Download Flow

1. Desktop calls `POST /maps/download-token`.
2. API validates user, org, classification, entitlement, and quota policy.
3. API records audit event.
4. API returns short-lived signed download URL.
5. Desktop downloads directly from object storage.

## Usage Events

Map usage event fields:

- `event_id`
- `idempotency_key`
- `org_id`
- `user_id`
- `device_id`
- `module_serial`
- `map_id`
- `mission_id`
- `area_km2`
- `artifact_bytes`
- `zoom_min`
- `zoom_max`
- `provider_ids`
- `client_generated_at`
- `server_recorded_at`

Usage events are append-only. Corrections should be represented as adjustment
events, not destructive edits.

## Quota Rules

Personal plan:

- Usage counts against the user monthly quota.

Organization plan:

- Usage counts against the org pool.
- Optional member caps limit individual users.
- Admins can review member usage.

Quota enforcement points:

- Upload initiation.
- Download token issuance.
- Usage event ingestion.

## Retention

Default retention should be decided before launch.

Suggested starting defaults:

- Map metadata: retained while account/org is active.
- Map artifacts: retained until deleted by org admin or retention policy.
- Audit events: retained at least 1 year for commercial, longer for regulated.
- Support bundles: short-lived by default because they may contain sensitive
  logs.
