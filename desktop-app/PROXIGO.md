# Proxigo Cloud Integration

The desktop app connects to `proxigo.us` to track map download usage and enforce subscription quotas.

---

## Key files

| File | Purpose |
|---|---|
| `src/lib/proxigo.ts` | All API calls, session types, `withSession` helper |
| `src/lib/store.ts` | Zustand store — `proxigoSession`, `cloudAccount`, `lastUsageEvent` |
| `src/App.tsx` | Session restore on startup + proactive token refresh |
| `src/pages/Dashboard.tsx` | `saveBorderCut()` — the real download handler; calls `reportMapDownload` after each tile download |
| `src/pages/Maps.tsx` | **Dead code** — exports a `Maps` component that is never imported or rendered. Do not add download logic here. |
| `src/components/Layout.tsx` | AccountPanel — shows quota, refresh button, test button |

---

## How usage reporting works

1. User draws a bounding box and clicks **Download** (in `Dashboard.tsx → saveBorderCut()`).
2. Tauri downloads tiles to disk via `cmd.downloadMapRegion()`.
3. `saveBorderCut` immediately calls `proxigo.reportMapDownload()` via the `withSession` wrapper.
4. On success, `cloudAccount` in the store is updated with the confirmed total from the API response — this makes the Account tab show the new number instantly without waiting for a full `/api/account` refresh.
5. A background `getAccount` call syncs any org-level totals from other members.

---

## Token lifetime and refresh

Supabase JWTs expire after **1 hour**. Two mechanisms prevent stale-token 401s:

**Proactive refresh (`App.tsx`):**
```
useEffect fires 5 min before expires_at
→ calls refreshSession()
→ saves new tokens to profile.json and store
```

**Call-site refresh (`proxigo.withSession`):**
```
Before fn() → if token within 60s of expiry → refresh
After fn() throws → if 401 → refresh + retry once
```

Every API call in the app goes through `withSession`. The `onRefresh` callback persists new tokens to disk so the refresh survives a restart.

---

## Diagnosing usage not reflecting

Open DevTools (`Ctrl+Shift+I` or via Tauri dev menu) and check the console after downloading a map. You should see:

```
[usage] download complete — proxigoSession: true moduleSerial: MAC-AAAA-AAAA-AAAA reportKm2: 12.5
[usage] firing reportMapDownload — km2: 12.5 serial: MAC-AAAA-AAAA-AAAA tokenExpiresAt: 2026-07-02T21:10:00.000Z
[usage] reportMapDownload success: { ok: true, total_km2_this_month: 14.35 }
```

If you see `Usage report failed:` in the Account tab banner, the message will tell you exactly what went wrong (401, module not found, rate limit, etc.).

**In the Account tab**, the "Last download recorded / failed" box shows the result of the most recent POST — it persists even after switching tabs.

Use the **"Test Cloud Connection"** button in the Account tab to send a 0.01 km² test event at any time.

---

## Profile storage

`%APPDATA%\DroneVisionNav\profile.json` contains:

- `proxigo_access_token` / `proxigo_refresh_token` / `proxigo_token_expires_at`
- `proxigo_user_id` / `proxigo_email`
- `proxigo_module_serial` — the module that usage events are attributed to

The active module serial is auto-selected from the account's first active module on login and can be changed in the Account tab.

---

## Rate limits

| Endpoint | Limit |
|---|---|
| `POST /api/usage` | 60 req / min / IP |
| `GET /api/account` | 30 req / min / IP |
