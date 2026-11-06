# Proxigo API — Desktop App ↔ Website Integration

## Overview

The Drone desktop app communicates with `proxigo.us` (Next.js + Supabase) to:
- Authenticate users with their Proxigo account
- Fetch account info (plan, quota, registered modules)
- Report satellite map download usage so it reflects on the website dashboard

All communication is HTTPS. The desktop app uses **Bearer JWT authentication** — no cookies, no sessions, no browser.

The app can also connect to a **self-hosted Proxigo server** (on-prem or a
private cloud) instead of `proxigo.us`. Such a server must serve the discovery
document below and the same `/api/...` endpoints. Requests are made from the
app's Rust side, so self-hosted servers need no CORS headers.

---

## Discovery (self-hosted servers)

`GET {server}/.well-known/proxigo` (no auth):

```json
{
  "name": "ACME Proxigo",
  "api_version": "1",
  "api_base_url": "https://proxigo.acme.local",
  "auth": { "kind": "supabase", "url": "https://auth.acme.local", "anon_key": "<publishable key>" },
  "products": ["macula_vps", "airspace_weather"]
}
```

| Field | Required | Meaning |
|---|---|---|
| `name` | no | Shown on the sign-in screen and in Account. Defaults to the host name. |
| `api_version` | no | API contract version, currently `"1"`. |
| `api_base_url` | no | Root for `/api/...` paths. Defaults to the server address. |
| `auth` | yes | `{"kind":"supabase","url","anon_key"}` (supported now), or `{"kind":"oidc","issuer","client_id"}` (accepted, sign-in not implemented yet) |
| `products` | no | Product entitlement keys licensed for the whole installation. Used when `/api/account` has no `entitled_products`. |

Proxigo Cloud has no discovery request. The app builds the same settings from
`VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` and `VITE_PROXIGO_API_URL`.

---

## Authentication Flow

```
Desktop App                         Supabase Auth                 ProxigoWebsite
     │                                    │                              │
     │  POST /auth/v1/token               │                              │
     │  { email, password }               │                              │
     │ ─────────────────────────────────► │                              │
     │                                    │                              │
     │  { access_token, refresh_token,    │                              │
     │    expires_in, user: {id, email} } │                              │
     │ ◄───────────────────────────────── │                              │
     │                                    │                              │
     │  Tokens saved to OS credential store│                             │
     │                                    │                              │
     │  GET /api/account                  │                              │
     │  Authorization: Bearer <JWT>       │                              │
     │ ───────────────────────────────────┼─────────────────────────────►│
     │                                    │                              │
     │  { email, plan, km2_used, ... }    │                              │
     │ ◄──────────────────────────────────┼──────────────────────────────│
```

### Token Storage

Tokens are persisted in `profile.json` on the user's machine:

```json
{
  "proxigo_access_token": "eyJ...",
  "proxigo_refresh_token": "...",
  "proxigo_token_expires_at": 1751234567,
  "proxigo_user_id": "uuid",
  "proxigo_email": "user@example.com"
}
```

On startup, the app tries to restore the session. If the access token is expired, it calls the Supabase refresh endpoint automatically before loading.

### Token Lifecycle (Three-Layer Defense)

| Layer | Where | What it does |
|-------|-------|--------------|
| **Startup refresh** | `App.tsx` `useEffect` | If stored token is expired, refreshes before restoring session. |
| **Proactive refresh** | `App.tsx` `useEffect` | Schedules a refresh 5 minutes before the current token expires. Reschedules itself each time a new token is issued — creating a self-perpetuating refresh chain for the duration of the app session. |
| **Per-call refresh** | `proxigo.withSession()` | Before every critical API call, checks if the token is within 60 s of expiry and refreshes proactively. If a call still returns 401 (e.g., server-side revocation), refreshes and retries once automatically. |

This triple defense means a stale token should never silently drop a usage event.

---

## Endpoints

### GET `/api/account`

Returns the authenticated user's full account state for the desktop HUD.

**Request:**
```
Authorization: Bearer <supabase_access_token>
```

**Response:**
```json
{
  "user_id": "uuid",
  "email": "user@example.com",
  "name": "Jane Smith",
  "plan": "starter",
  "subscription_active": true,
  "km2_used": 142.5,
  "km2_limit": 500,
  "km2_remaining": 357.5,
  "modules": [
    { "serial": "MAC-ABCD-1234-WXYZ", "nickname": "Drone 1", "status": "active" }
  ],
  "entitled_products": ["macula_vps"]
}
```

`entitled_products` (**proposed, not yet served by the API**) lists the
platform product entitlement keys licensed to the user's organization. The
desktop shell shows only those product modules. When the field is absent the
app treats the account as a legacy Macula VPS customer (`["macula_vps"]`).
The desktop gate is presentation only: each product's API must enforce the
same entitlement server-side. See `docs/desktop-platform.md` in the repo root.

**Rate limit:** 30 requests / IP / minute

---

### POST `/api/usage`

Reports a satellite map download event. Called after every successful map download.

**Request:**
```
Authorization: Bearer <supabase_access_token>
Content-Type: application/json

{
  "km2": 23.4,
  "module_serial": "MAC-ABCD-1234-WXYZ",
  "session_id": "region-uuid"
}
```

**Validation:**
- `km2` must be a positive number ≤ 50,000
- `module_serial` must be registered to the authenticated user
- Module must have `status = "active"`

**Response:**
```json
{ "ok": true, "total_km2_this_month": 165.9 }
```

**Rate limit:** 60 requests / IP / minute

---

### GET `/api/usage`

Returns the current month's usage summary.

**Request:**
```
Authorization: Bearer <supabase_access_token>
```

**Response:**
```json
{
  "plan": "starter",
  "km2_used": 142.5,
  "km2_limit": 500,
  "km2_remaining": 357.5
}
```

---

## Plan Limits

| Plan       | Monthly km² limit |
|------------|-------------------|
| Starter    | 500 km²           |
| Pro        | 2,500 km²         |
| Enterprise | Custom            |

The desktop app enforces limits **client-side** before download (blocks the download button and shows a warning in the area estimator). The server records usage but does not enforce the cap — enforcement is UI-layer to keep the experience smooth for legitimate users.

---

## Security Protections

### Authentication
- Every API endpoint requires a valid Supabase JWT. No token → 401.
- The JWT is validated server-side by Supabase on every request — it cannot be forged.
- Bearer tokens expire in ~1 hour; the desktop app auto-refreshes using the stored refresh token.

### Module Ownership Check
Before recording usage, `/api/usage` verifies:
1. The `module_serial` exists in the `modules` table
2. It belongs to the authenticated user (`user_id = auth.uid()`)
3. Its status is `active`

This prevents one user from posting usage against another user's module.

### Rate Limiting
| Endpoint       | Limit                   |
|----------------|-------------------------|
| `/api/account` | 30 req / IP / minute    |
| `/api/usage`   | 60 req / IP / minute    |

Rate limiting is implemented in `lib/rate-limit.ts` using an in-memory sliding window. For production scale, swap the store to Redis.

### Input Validation
- `km2` is capped at 50,000 per event — no single report can claim an unrealistic download.
- `module_serial` is a string; the DB query uses parameterized values (no injection risk).

### CORS
`Access-Control-Allow-Origin: *` is set only on `/api/*` routes. This is safe because all requests must still carry a valid Bearer JWT — an unauthenticated cross-origin request gets a 401, not data.

### Supabase Row-Level Security (RLS)
The `usage_events` table has an RLS insert policy: `auth.uid() = user_id`. Even if the API layer were bypassed, Supabase itself would reject inserts for the wrong user.

---

## Testing the API

### 1. Get a token

```bash
curl -s -X POST \
  "https://anwkkdzdxfcufxijqxff.supabase.co/auth/v1/token?grant_type=password" \
  -H "apikey: sb_publishable_Ezq_RSeF65_vBfZVPeLNtg_nTBvmrAN" \
  -H "Content-Type: application/json" \
  -d '{"email":"your@email.com","password":"yourpassword"}' | jq .
```

Copy the `access_token` from the response.

### 2. Fetch account info

```bash
TOKEN="eyJ..."

curl -s https://proxigo.us/api/account \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Expected: your plan, km² used, modules list.

### 3. Report a test download

```bash
curl -s -X POST https://proxigo.us/api/usage \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"km2":5.0,"module_serial":"MAC-YOUR-SERIAL","session_id":"test-001"}' | jq .
```

Expected: `{"ok":true,"total_km2_this_month":...}`

### 4. Verify on the website

Log in at `https://proxigo.us/dashboard` — the usage chart should reflect the test event within seconds.

### 5. Test quota enforcement in the app

In the Drone app, draw a region in the Maps planner that exceeds your remaining km² quota. The download button should be disabled and the quota bar should turn red.

### 6. Test token expiry

Delete `proxigo_token_expires_at` from `profile.json` or set it to `0`, then restart the app. It should silently refresh the session using the stored refresh token.

---

## Usage Report Flow (Desktop App)

When the user downloads a map, the following happens in sequence:

```
1. Download completes (Rust backend returns tile_count, file_size_mb, etc.)
2. Region saved to local library (regions.json)
3. Optimistic UI update — quota bar decreases immediately (before API call)
4. proxigo.withSession() wrapper:
   a. Checks if token is within 60 s of expiry → refreshes if so
   b. Calls POST /api/usage with { km2, module_serial, session_id, bbox }
   c. If 401 → refreshes token and retries once
5. On success: updates quota bar to confirmed total from POST response
   - Background: calls GET /api/account to sync any org-wide discrepancies
6. On failure: rolls back optimistic update, shows error in quota section
```

The Account tab quota display updates at step 3 (instant), then again at step 5 (confirmed). The website reflects step 5 on the next page load.

### In-App Diagnostics

**Account tab → "Test Cloud Connection"** posts 0.01 km² to `/api/usage` and shows either:
- `✓ API OK — total this month: X.XXX km²` (green) — API is working
- `✗ API FAILED: <reason>` (red) — exact error from the server

**Account tab → "Refresh Account"** re-fetches `/api/account` and shows the current km² tally.

Both buttons use the same `withSession` auto-refresh wrapper, so they work even if the in-memory session has expired.

---

## Key Files

### Desktop App (`Drone/desktop-app/`)
| File | Purpose |
|------|---------|
| `src/lib/proxigo.ts` | All API calls: login, refresh, getAccount, reportMapDownload, `withSession` wrapper |
| `src/platform/api/backend.ts` | Active server (cloud / self-hosted), discovery, offline discovery cache |
| `src/platform/api/http.ts` | HTTP layer; routes requests through Rust `http_request` |
| `src/platform/shell/ServerPicker.tsx` | Server picker on the sign-in screen |
| `src/lib/types.ts` | `Profile` interface with `proxigo_*` token fields |
| `src/lib/store.ts` | Zustand store: `proxigoSession`, `cloudAccount` |
| `src/App.tsx` | Session restore on startup, proactive refresh chain, login gate |
| `src/products/macula-vps/map/VpsMapTools.tsx` | `saveBorderCut()` — real download handler; usage reporting with `withSession` lives here |
| `src/platform/shell/AccountPanel.tsx` | AccountPanel: login form, usage HUD, Test/Refresh buttons |
| `src/platform/entitlements.ts` | Product entitlements from `GET /api/account` → `entitled_products` |
| `src-tauri/src/commands/profile.rs` | Rust: persists `proxigo_*` fields; access/refresh tokens go to the OS credential store, not `profile.json` |
| `src-tauri/src/commands/backend.rs` | Rust: backend connection (`backend.json`), private CA, `http_request` |
| `src-tauri/src/commands/secrets.rs` | Rust: OS credential store access (`keyring`) |

### ProxigoWebsite (`ProxigoWebsite/`)
| File | Purpose |
|------|---------|
| `app/api/account/route.ts` | GET account info |
| `app/api/usage/route.ts` | POST/GET usage events |
| `lib/supabase/bearer.ts` | Creates Supabase client from Bearer JWT |
| `lib/rate-limit.ts` | In-memory rate limiter |
| `next.config.ts` | CORS headers for desktop app |
