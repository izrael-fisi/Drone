# Proxigo Desktop Platform

Branch: `platform/enterprise-shell`

The desktop app is a **multi-product enterprise platform** branded Proxigo.
Macula VPS (GNSS-denied visual positioning) is the first product module and
Airspace & Weather (a third-party data integration) the second. Other drone/robot products, third-party
integrations and data/analytics products plug into the same shell, so a client
sees everything they license on one global dashboard.

## Layers

```
App.tsx                    Proxigo sign-in gate, legacy route aliases
└─ platform/shell/PlatformShell.tsx
   ├─ title bar            Proxigo brand + ProductSwitcher (Overview | products)
   ├─ <Outlet/>            platform/map/GlobalMap.tsx: shared global map
   ├─ PlatformDock         rail + stacked panels
   │    Overview rail:     product launchers
   │    Product rail:      the active product's panels
   │    Always:            Settings, Account
   ├─ product bottom dock  supplied by the active product (optional)
   └─ CommandPalette       places + platform panels + product commands
```

| Path | Owns |
|---|---|
| `src/platform/types.ts` | `ProductModule` contract and related types |
| `src/platform/registry.ts` | `PRODUCT_MODULES`, the static list of compiled-in products |
| `src/platform/entitlements.ts` | Which products the signed-in organization may use |
| `src/platform/platformStore.ts` | Active product and map-layer visibility (per viewer, localStorage) |
| `src/platform/hooks.ts` | `useActiveProduct`, `usePlatformMapLayers`, `usePlatformContext`, … |
| `src/platform/platformPanels.tsx` | Platform panels: `overview`, `settings`, `account` |
| `src/platform/shell/*` | Shell UI: dock, switcher, palette, overview, account, settings |
| `src/platform/ui.tsx` | Shared panel primitives (`PanelStack`, `StateCard`, `StatusRow`, …) |
| `src/platform/mapStore.ts` | Shared-map camera API and product cursor (runtime only) |
| `src/products/macula-vps/` | The Macula VPS module, its panels and bottom dock |
| `src/products/airspace-weather/` | The Airspace & Weather module: FAA airspace, LAANC ceilings, METAR, weather, go/no-go |
| `src/platform/api/` | Backend connection, discovery and the HTTP layer every product uses |

The platform layer must not import from `src/products/*`. The only exception
is `registry.ts`, which lists the modules. Products may import
`src/platform/types`, `src/platform/ui`, `src/platform/hooks` and `src/lib/*`.

## Product module contract

A product exports one `ProductModule` (see `src/platform/types.ts`):

- **Identity:** `id`, `name`, `shortName`, `tagline`, `category`
  (`navigation | vehicle | integration | analytics`), `Icon` and `accent`.
- **Licensing:** `entitlementKey` is matched against
  `CloudAccount.entitled_products`. Set `legacyDefault: true` only for products
  every pre-existing account already has (currently just Macula VPS).
- **`panels`:** dock panels. Each `id` must be unique across the whole platform
  (the registry checks this at startup). `showInRail: false` with `railParent`
  makes a panel a drill-down. `command` adds the panel to the command palette.
  A panel receives `ctx: PlatformContext`, with `openPanel`, `pushPanel`,
  `closeDock` and `openBottomTab`.
- **`defaultPanelId`:** opened when the operator switches to the product.
- **`bottomDock`:** optional telemetry/console dock, shown only while the
  product is active.
- **`useStatus()`:** health and a headline for the Overview panel.
- **`useMapLayers()`:** renderer-agnostic overlays (points, lines, areas in
  `[lat, lon]`) for the shared map. The operator can toggle each layer in the
  Overview panel. Memoize the returned array.
- **`useCommands()`:** command-palette entries.
- **`relatedLayerIds`:** layers from other products that belong in this
  product's map view (see "Product separation" below).
- **`MapOverlay`:** on-map tools/HUD, rendered only while the product is active.
- **`onMapClick(click, ctx)`:** map clicks while the product is active, plus
  clicks on its point features from the Overview. `click.hit` names the
  product's own point feature under the cursor, if any.

The shell calls the `use*` hooks of every registered module on every render, in
registry order, whether or not the product is licensed. A module must therefore
always or never define each hook. The hooks must be cheap and must not fetch
unconditionally. Gate network polling on the product being entitled and active.

## Adding a product

1. Create `src/products/<id>/index.tsx` exporting a `ProductModule`.
2. Add it to `PRODUCT_MODULES` in `src/platform/registry.ts`.
3. Agree its `entitlementKey` with the Proxigo API owner.
4. Put product-specific backend calls in the product folder. If the product
   needs native access, add Tauri commands under
   `src-tauri/src/commands/<product>.rs`. Never add them to the VPS modules.
5. To test it locally before the API returns entitlements, run
   `VITE_PLATFORM_PRODUCTS=macula_vps,<key> npm run dev`. This override only
   works in dev builds.

## Entitlements

`resolveEntitlements()` checks these sources in order:

1. Dev override `VITE_PLATFORM_PRODUCTS` (dev builds only).
2. `CloudAccount.entitled_products`, the proposed field on
   `GET /api/account` (see `desktop-app/docs/proxigo-api.md`).
3. While the account can't be fetched (offline field use, API outage), the
   last `entitled_products` seen on this machine. Operators are never locked
   out of a product mid-operation.
4. A legacy fallback: accounts without the field get the `legacyDefault`
   modules, which today means Macula VPS. Existing customers therefore see no
   change until the API ships the field.

When a product is not licensed, its panels and commands are hidden. It appears
locked in the switcher and in the Overview's "More From Proxigo" list. **This
is a UI gate only.** Each product's backend must enforce access itself.

## Global map

`src/platform/map/GlobalMap.tsx` is the shared map. It knows no product:

- It draws the layers in view (`useMapLayersInView()`) in both renderers:
  Leaflet as the fallback, MapLibre through the `platform-features` source and
  the `platform-*` layers. A feature can set its own `color` and `style`
  (fill, opacity, width, dashed, radius, halo, permanent label).
- `activeOnly` layers (working state such as drafts or a live vehicle) are
  drawn only while their product is active. They never appear in the Overview
  or as layer toggles.
- A product enables a drawing tool with `usePlatformMapStore().setTool`
  (`point` clicks or `box` drags). While a tool is set, clicks go to the tool,
  not to `onMapClick`. `setHome(points)` sets what Recenter fits.
- It renders the active product's `MapOverlay` with `ctx` and a
  `leftInsetClass` that keeps HUDs clear of the dock.
- `ProductModule.useBackground(entitled)` runs for every product whatever is in
  view (Macula VPS telemetry polling).

Macula VPS: `src/products/macula-vps/map/`. It contains `VpsMapTools` (map
picker, map-cut and mission tools, connection HUD) and `store.ts` (tool state
that survives switching products, including an in-progress map install).
`layers.ts` holds the active map outline, drafts, missions and aircraft as
`activeOnly` layers, and `telemetry.ts` holds the background position polling.
It also publishes the "VPS map coverage" layer of its completed maps.

## Product separation

Each product is a separate view of the same global map:

- The title bar switcher, a 2px accent strip under the title bar, the dock
  border, rail badge and panel header chip all take the active product's accent
  (platform views use `PLATFORM_ACCENT`).
- The map shows the active product's own layers plus its `relatedLayerIds`.
  The Overview shows every visible layer from every licensed product.
- Macula VPS map tooling (map selector, map-cut and mission tools,
  connection/position HUD, active-map outline, drawings, mission overlays,
  aircraft marker) is the VPS `MapOverlay` plus `activeOnly` layers, so it
  appears only in the Macula VPS view. VPS telemetry polling keeps running in
  the background, so switching views mid-flight loses nothing.
- Airspace & Weather declares `macula-vps.map-coverage` as related: its view
  shows where VPS maps exist, and the briefing says whether the location is
  inside VPS coverage. The VPS view shows no airspace or weather data.

## Airspace & Weather

Entitlement key `airspace_weather`. While the product is active, every map
click briefs that location (a click on a weather station opens it instead).

| Panel | Shows |
|---|---|
| Briefing | Go / caution / no-go from wind, gusts, visibility, precipitation, temperature, airspace class and LAANC ceiling; 12-hour wind forecast; VPS coverage; source status |
| Airspace | Class B, C, D and E-surface areas within 30 km, floor and ceiling, which contain the point |
| Weather Stations | METAR observations within 80 km with flight category |
| Flight Limits | Per-machine thresholds used by the assessment |

Sources (public, keyless): Open-Meteo (weather, global), aviationweather.gov
(METAR), FAA ArcGIS open data `Class_Airspace` and
`FAA_UAS_FacilityMap_Data` (US only; skipped elsewhere). Requests go through
the platform HTTP layer. Map layers: controlled airspace (colored by class),
LAANC ceilings (colored by ceiling), weather stations (colored by flight
category) and the briefing point.

The briefing is advisory only. TFRs and NOTAMs are not included yet.

## Projects (site layouts)

Platform feature, available in every product (`src/platform/projects/`). A
project is a named site with a saved map view and placed items: cameras (facing,
field of view and range drawn as a view cone), sensors (coverage circle),
markers, launch pads, geofences and keep-out zones (polygons).

- Saved to `DroneVisionNav/projects.json` by Rust (`commands/projects.rs`,
  written through a temp file and renamed). The store saves shortly after each
  change and never before the first load, so an unreadable file is never
  overwritten. `model.ts` owns the schema; `parseDocument` drops malformed
  entries instead of failing the whole load (tested in `model.test.ts`).
- Placing uses the platform map tool and takes over from the active product's
  tool. `PlacingBar` shows instructions; Enter finishes an area, Backspace
  removes the last corner, Esc cancels.
- Site layouts are a `global` layer: visible in every view and toggleable in
  Layers. Clicking a placed point opens it in the Projects panel.

## Map view

The map reopens where it was left: `GlobalMap` saves the center and zoom
(`platformStore.mapView`, per machine) about 0.6 s after the map settles. Esri
imagery is requested up to zoom 19 (`IMAGERY_NATIVE_MAX_ZOOM`) and scaled
beyond that up to zoom 22. Above 19, Esri serves grey "Map data not yet
available" tiles. Double-click zoom is off while a placement tool is active.

## Sidebar

`PlatformDock` is one glass sidebar: project chip, Overview, Layers, the active
product's tools (or the product list in the Overview), then Settings and
Account. It is labelled when nothing is open. It compacts to an icon rail
while a panel is open or when collapsed. Widths come from `shell/layout.ts`.

## Backend connection and API

The app talks to one Proxigo server at a time: **Proxigo Cloud** or an
organization's **self-hosted** server (on-prem or private cloud). The sign-in
screen has a server picker.

- `src/platform/api/http.ts`: every request goes through the Rust
  `http_request` command (`src-tauri/src/commands/backend.rs`), so
  self-hosted servers need no CORS setup and can use a private CA
  certificate. Errors are `ApiError` with kind `network | http | parse |
  config`, and a non-JSON reply gives a readable message instead of a JSON
  parse failure.
- `src/platform/api/backend.ts`: the connection (stored by Rust in
  `DroneVisionNav/backend.json`) and **discovery**. Proxigo Cloud settings
  come from the build's `VITE_*` variables. A self-hosted server is
  configured from its address alone: the app reads
  `GET {server}/.well-known/proxigo` (format in
  `desktop-app/docs/proxigo-api.md`) and caches it, so the app keeps working
  offline in the field.
- `src/lib/proxigo.ts` keeps its public functions, but takes the API and auth
  addresses from discovery.
- **Session tokens** are stored in the OS credential store (Windows Credential
  Manager) per connection, not in `profile.json`. Rust moves them on profile
  save/load and migrates tokens left by older versions. If the credential
  store is unavailable they stay in the file. Changing server clears the old
  server's tokens.
- Device SSH passwords and key passphrases are stored the same way
  (`device:<id>:ssh-secret`), not in `devices.json`. Older plain-text entries
  are migrated on load.
- Discovery may list `products` to license a whole self-hosted installation.
  This applies when the account itself has no `entitled_products`.
- Products call their own endpoints with `requestJson` from
  `src/platform/api/http.ts`. Proxigo endpoints use `backendTls: true` and the
  `apiBaseUrl` from discovery.

Not built yet: OIDC sign-in (Cognito or customer IdP). Discovery already
accepts `auth.kind: "oidc"`, and the app reports it as unsupported. Also not
built: the offline cache/outbox for product data, and live updates over
WebSocket/SSE.

## Behavior kept from the single-product app

- An organization licensed for exactly one product lands directly in that
  product until the operator picks Overview. VPS-only clients see the VPS rail
  as before.
- Legacy routes (`/maps`, `/devices`, `/diagnostics`, …) still open the same
  panels. Opening a product's panel or bottom tab brings that product to the
  front. `/overview` opens the global overview.
- Profile/devices storage paths and the Tauri `identifier` are unchanged. Only
  the window title and `productName` became "Proxigo".

## Known follow-ups

- OIDC sign-in (system browser + PKCE + loopback redirect) for Cognito and
  customer identity providers.
- Route third-party data (airspace, weather) through the Proxigo backend for
  caching, rate limiting and air-gapped installs.
- `CloudAccount.entitled_products` must be implemented in the Proxigo API.
- `pages/Settings.tsx` (full-page settings) is still VPS-oriented and should
  become platform settings plus per-product sections.
