# Proxigo API — Desktop App ↔ Website Integration

## Overview

The Drone desktop app communicates with `proxigo.us` (Next.js + Supabase) to:
- Authenticate users with their Proxigo account
- Fetch account info (plan, quota, registered modules)
- Report satellite map download usage so it reflects on the website dashboard

All communication is HTTPS. The desktop app uses **Bearer JWT authentication** — no cookies, no sessions, no browser.

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
     │  Tokens saved to profile.json      │                              │
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
  ]
}
```

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

## Key Files

### Desktop App (`Drone/desktop-app/`)
| File | Purpose |
|------|---------|
| `src/lib/proxigo.ts` | All API calls: login, refresh, getAccount, reportMapDownload |
| `src/lib/types.ts` | `Profile` interface with `proxigo_*` token fields |
| `src/lib/store.ts` | Zustand store: `proxigoSession`, `cloudAccount` |
| `src/App.tsx` | Session restore on startup + login gate |
| `src/pages/Maps.tsx` | Quota display + enforcement before download |
| `src/components/Layout.tsx` | AccountPanel: login form + usage HUD |
| `src-tauri/src/commands/profile.rs` | Rust: persists `proxigo_*` fields in `profile.json` |

### ProxigoWebsite (`ProxigoWebsite/`)
| File | Purpose |
|------|---------|
| `app/api/account/route.ts` | GET account info |
| `app/api/usage/route.ts` | POST/GET usage events |
| `lib/supabase/bearer.ts` | Creates Supabase client from Bearer JWT |
| `lib/rate-limit.ts` | In-memory rate limiter |
| `next.config.ts` | CORS headers for desktop app |
