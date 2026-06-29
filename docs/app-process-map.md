# App Process Map

This document maps the desktop app as an operator workflow. It focuses on the current streamlined shell: map-first home surface, left navigator panes, right map and mission controls, bottom diagnostics, device connectivity, map installation, mission overlays, and future cloud boundaries.

## Overall App Flow

```mermaid
flowchart LR
  Operator["Operator"] --> App["Desktop App Shell"]
  App --> Boot["Load Local State"]
  Boot --> Home["Home Map Surface"]

  Boot --> Profile["Profile And Account Settings"]
  Boot --> Devices["Saved Devices"]
  Boot --> Maps["Saved Maps And Cuts"]
  Boot --> Missions["Saved Missions"]

  Home --> LeftNav["Left Navigator Panes"]
  Home --> MapControls["Map Controls"]
  Home --> CommandBar["Command And Location Search"]
  Home --> BottomDock["Bottom Diagnostics Dock"]

  LeftNav --> MapsPane["Maps Pane"]
  LeftNav --> MissionPane["Mission Pane"]
  LeftNav --> DevicePane["Device Pane"]
  LeftNav --> CameraPane["Camera And Calibration Pane"]
  LeftNav --> GroundControlPane["Ground Control Pane"]
  LeftNav --> SettingsPane["Settings Pane"]
  LeftNav --> AccountPane["Account Pane"]

  MapControls --> CutFlow["Map Cut And Install Flow"]
  MapControls --> MissionMode["Mission Mode Overlay Flow"]
  DevicePane --> EdgeApi["Companion Edge API"]
  GroundControlPane --> GcsBridge["QGroundControl And ArduPilot Bridge"]
  EdgeApi --> Telemetry["MAVLink And Position Telemetry"]
  GcsBridge --> Telemetry
  Telemetry --> Home

  CutFlow --> LocalMapStore["Local Map Library"]
  MissionMode --> LocalMissionStore["Local Mission Store"]
  LocalMapStore --> Home
  LocalMissionStore --> Home
```

## Launch And Navigation

```mermaid
flowchart TD
  Start["Open Desktop App"] --> LoadState["Load Profile Devices Maps Missions Settings"]
  LoadState --> CountryView["Center Map On User Country Placeholder United States"]
  CountryView --> HomeReady["Home Map Surface Ready"]

  HomeReady --> NavChoice{"Operator Selects"}
  NavChoice --> Search["Command Or Location Search"]
  NavChoice --> MapWork["Map Cut Map Select Or Mission Mode"]
  NavChoice --> DeviceWork["Device Connection"]
  NavChoice --> Diagnostics["Bottom Dock Diagnostics Parameters Messages Console"]
  NavChoice --> Settings["Settings Account Ground Control Camera"]

  Search --> FlyToPlace["Fly Map To Place Without Marker"]
  MapWork --> OverlayUpdate["Update Active Map Mission And Drawing Overlays"]
  DeviceWork --> LiveState["Update Connection Heartbeat And Telemetry State"]
  Diagnostics --> RuntimeEvidence["Inspect Runtime Device MAVLink And Logs"]
  Settings --> PersistPrefs["Persist Local Settings For Next Launch"]

  FlyToPlace --> HomeReady
  OverlayUpdate --> HomeReady
  LiveState --> HomeReady
  RuntimeEvidence --> HomeReady
  PersistPrefs --> HomeReady
```

## Map Cut And Installation

```mermaid
flowchart TD
  StartCut["Operator Starts Map Cut"] --> ShapeChoice{"Cut Shape"}
  ShapeChoice --> BoxCut["Box Cut Drag Rectangle"]
  ShapeChoice --> PolygonCut["Polygon Cut Place Points In Any Order"]

  BoxCut --> Boundary["Boundary Points Created"]
  PolygonCut --> OrderPoints["Points Ordered Around Center To Avoid Crossing"]
  OrderPoints --> Boundary

  Boundary --> ZoomSelect["Select Tile Zoom And Multi Layer Option"]
  ZoomSelect --> ProviderSelect["Select Download Providers"]
  ProviderSelect --> Estimate["Estimate Area Tiles Resolution Source Size Disk Size"]
  Estimate --> ThresholdCheck{"Over Limits"}

  ThresholdCheck -->|No| ReadySave["Save Cut Enabled"]
  ThresholdCheck -->|Yes| Override["Show Warning And Require Override"]
  Override --> ReadySave

  ReadySave --> Install["Run Provider Aware Map Download"]
  Install --> Progress["Show Tile Progress Meter"]
  Progress --> Patch["Patch Tiles From Provider Priority And Fallbacks"]
  Patch --> Assets["Write satellite.png metadata.json coverage_manifest.json"]
  Assets --> Region["Save Region Record"]
  Region --> Overlay["Show Saved Cut Boundary Overlay"]
  Overlay --> FitMap["Fit Map To Saved Cut"]
```

## Mission Mode

```mermaid
flowchart TD
  MissionStart["Mission Mode Button"] --> MapGate{"Saved Map Exists"}
  MapGate -->|No| CreateMap["Create Or Import Map First"]
  CreateMap --> MapCut["Cut Install Or Import Map"]
  MapCut --> MapGate

  MapGate -->|Yes| MissionGate{"Saved Mission Exists For Map"}
  MissionGate -->|No| CreateMission["Draw Cut Waypoints And Save Mission"]
  CreateMission --> MissionGate

  MissionGate -->|Yes| Filter["Mission Selector Filter"]
  Filter --> SelectMissions["Select One Or More Saved Missions"]
  SelectMissions --> ClearContext["Clear Drawing Context"]
  ClearContext --> DrawMissionOverlays["Draw Selected Map Boundary Mission Areas Waypoints And Paths"]
  DrawMissionOverlays --> LiveTelemetry{"Telemetry Available"}

  LiveTelemetry -->|No| StaticMission["Show Saved Mission Geometry Only"]
  LiveTelemetry -->|Yes| LiveMission["Update Drone Position From MAVLink Stream"]

  StaticMission --> MissionReview["Review Mission On Map"]
  LiveMission --> MissionReview
  MissionReview --> ExportOrGcs["Use Ground Control Or Export Workflow"]
```

## Device Connectivity And Telemetry

```mermaid
flowchart LR
  DevicePane["Device Pane"] --> DeviceSetup{"Connection Method"}
  DeviceSetup --> Automatic["Automatic Discovery"]
  DeviceSetup --> Custom["Custom IP Username Password"]

  Automatic --> EdgeCandidate["Find Companion Computer"]
  Custom --> EdgeCandidate
  EdgeCandidate --> SaveDevice["Save Active Device"]
  SaveDevice --> EdgeHealth["Check Edge API Health"]
  EdgeHealth --> Heartbeat["Heartbeat Probe"]
  Heartbeat --> Mavlink["MAVLink Endpoint"]

  Mavlink --> PositionBridge["Position UDP Or Edge API Position"]
  PositionBridge --> Dashboard["Dashboard Aircraft Position"]
  Mavlink --> Gcs["QGroundControl Mission Planner ArduPilot"]
  Gcs --> PositionBridge
  EdgeHealth --> Runtime["Runtime Services Camera Calibration Diagnostics"]
  Runtime --> BottomDock["Bottom Dock Evidence"]
```

## Data And Storage Boundaries

```mermaid
flowchart TD
  LocalApp["Desktop App"] --> LocalState["Local State Profile Devices Regions Missions"]
  LocalApp --> MapArtifacts["Local Map Artifacts"]
  LocalApp --> RuntimeLogs["Runtime Logs And Support Bundles"]

  MapArtifacts --> RegionRecord["Region Metadata"]
  MapArtifacts --> TileCache["Raw Tile Cache And Patched Mosaic"]
  MapArtifacts --> Manifest["Coverage Manifest"]

  LocalApp --> ProviderApis["External Map Providers"]
  ProviderApis --> EstimateSurvey["Estimate Survey Download"]

  LocalApp -. "Future Provider Neutral API" .-> CloudApi["Cloud API v1"]
  CloudApi --> OrgData["Organization Users Devices Quotas"]
  CloudApi --> CloudMaps["Cloud Map Metadata And Usage"]

  Regulated["Regulated Mission Or CUI Data"] -. "Hold Until Approved" .-> GovCloud["Future GovCloud Environment"]
  CloudApi -. "No Direct Supabase Tables From Desktop" .-> Rule["Desktop Uses API Contract Only"]
```

## Primary Operator Path

```mermaid
flowchart LR
  A["Launch App"] --> B["Connect Device"]
  B --> C["Confirm Heartbeat"]
  C --> D["Create Import Or Select Map"]
  D --> E["Estimate And Install Map"]
  E --> F["Draw Waypoints And Save Mission"]
  F --> G["Enable Mission Mode For Saved Map"]
  G --> H["Monitor Live Telemetry"]
  H --> I["Review Logs And Diagnostics"]
  I --> J["Export Support Or Mission Artifacts"]
```

## Process Notes

- The home map is the primary work surface.
- Left navigator panes expose workflows, but the map remains the operational context.
- Mission Mode is gated: at least one saved map must exist before Mission Mode can activate.
- A saved mission should be tied to a saved map so mission overlays always have a known map boundary.
- Saved map cuts should always render their boundary overlay when selected.
- Save Cut is an installation process, not just metadata persistence.
- Mission Mode shows saved mission geometry and then layers live telemetry when available.
- Ground Control integrations should feed the app telemetry state rather than becoming a separate operator silo.
- Cloud integration should remain provider-neutral through `/v1` APIs so commercial prototype services can later migrate to GovCloud.
