# Cloud API Contract

## Purpose

Define a provider-neutral `/v1` API used by:

- Desktop app.
- Website dashboard.
- Future web app.
- Commercial backend implementation.
- Future GovCloud backend implementation.

The desktop app must not call Supabase tables, Supabase Storage, R2 buckets, or
GovCloud services directly for account, org, map, quota, or usage workflows.

## Base URLs

Commercial:

```text
https://api.proxigo.io/v1
```

Staging:

```text
https://staging-api.proxigo.io/v1
```

Future GovCloud:

```text
https://gov-api.proxigo.io/v1
```

Desktop environment modes:

- `commercial`
- `staging`
- `govcloud`
- `custom`

## Required Headers

Authenticated requests:

```text
Authorization: Bearer <access_token>
X-Proxigo-Client: desktop | web
X-Proxigo-Client-Version: <semver>
```

Mutating idempotent requests:

```text
Idempotency-Key: <uuid>
```

## Common Response Envelope

Success:

```json
{
  "ok": true,
  "data": {}
}
```

Error:

```json
{
  "ok": false,
  "error": {
    "code": "forbidden",
    "message": "User is not allowed to access this organization."
  }
}
```

## Core Endpoints

### Health

```text
GET /health
```

Returns API status, version, deployment environment, and server time.

### Identity

```text
GET /me
```

Returns current user, active org, roles, plan, and feature entitlements.

### Organizations

```text
GET /orgs/current
POST /orgs
POST /orgs/join
POST /orgs/members/approve
POST /orgs/members/reject
POST /orgs/members/quota
```

Org APIs must enforce membership and role checks server-side.

### Devices

```text
GET /devices
POST /devices/register
POST /devices/pairing-token
POST /devices/revoke
```

Device registration links a hardware module, desktop install, or companion
computer identity to an org/user.

### Maps

```text
GET /maps
POST /maps/upload/initiate
POST /maps/upload/complete
POST /maps/download-token
POST /maps/archive
```

Upload/download APIs issue short-lived signed URLs only after authorization,
quota, data classification, and artifact-size checks pass.

### Usage

```text
POST /usage/map-events
GET /usage/summary
```

Usage events must be idempotent and attributed to user, org, device, map, and
mission where available.

### Entitlements

```text
GET /entitlements
```

Returns plan limits, org quota, feature flags, map limits, cloud environment
policy, and disabled reasons.

### Audit

```text
GET /audit/events
```

Admin-only endpoint for audit event review.

## Shared Entity Fields

All org-owned records:

- `org_id`
- `created_at`
- `updated_at`

All user-owned records:

- `user_id`

All map artifacts:

- `map_id`
- `artifact_id`
- `classification`
- `storage_backend`
- `artifact_bytes`
- `checksum_sha256`
- `bbox`
- `area_km2`
- `zoom_min`
- `zoom_max`

All billable events:

- `event_id`
- `idempotency_key`
- `client_generated_at`
- `server_recorded_at`

## Auth Policy

Commercial prototype can use Supabase Auth tokens behind the API facade.

GovCloud can use AWS-native identity, customer SAML/OIDC, Cognito where
appropriate, or another approved IdP. The desktop app should not care which
provider issued the token as long as the `/v1` API contract remains stable.

## Versioning

Breaking API changes require a new prefix:

```text
/v2
```

Non-breaking additions may be added to `/v1` as optional fields.
