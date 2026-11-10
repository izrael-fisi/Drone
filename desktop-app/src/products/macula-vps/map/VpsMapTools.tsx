import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PulseIcon as Activity, MapTrifoldIcon as MapIcon, NavigationArrowIcon as Navigation } from "@phosphor-icons/react";
import { listen } from "@tauri-apps/api/event";
import { homeDir, join } from "@tauri-apps/api/path";
import { createSavedMissionId, missionBoundsFromParts, upsertSavedMission } from "../../../lib/missions";
import { useShellStore } from "../../../lib/shellStore";
import { useAppStore } from "../../../lib/store";
import { cmd } from "../../../lib/tauri";
import { proxigo } from "../../../lib/proxigo";
import type { DownloadProgress, MapProvider, MapProviderId, MapUsageEstimate, Region, SavedMission } from "../../../lib/types";
import { cn, formatDate, formatMegabytes } from "../../../lib/utils";
import { usePlatformMapStore } from "../../../platform/mapStore";
import { clampMapZoom, MAP_MAX_ZOOM, PLACEHOLDER_ORG_COUNTRY_CENTER, PLACEHOLDER_ORG_COUNTRY_ZOOM, pointsBounds } from "../../../platform/map/geo";
import type { ProductMapOverlayProps } from "../../../platform/types";
import {
  bboxAreaKm2,
  combineLayeredEstimates,
  isDownloadProviderReady,
  orderPolygonPoints,
  polygonAreaKm2,
  positionLabel,
  positionLatLon,
  positionTone,
  providerApiKeys,
  regionPolygon,
  satellitePreviewUrl,
  slugifyPathSegment,
  sumSelectedProviderTotals,
  type MapToolMode,
  type MapSelectionValue,
} from "./helpers";
import { HudRow, MapRightControls } from "./MapControls";
import { useVpsMapStore, useVpsState } from "./store";

/**
 * Macula VPS tools on the shared map (map picker, map cuts, missions,
 * connection HUD). Rendered only while Macula VPS is the active product; the
 * state lives in `useVpsMapStore`.
 */
export function VpsMapTools({ leftInsetClass }: ProductMapOverlayProps) {
  const { profile, setProfile, devices, regions, activeDeviceId, addRegion, proxigoSession, setProxigoSession, cloudAccount, setCloudAccount, setLastUsageEvent } = useAppStore();
  const { resetRightDock, pushRightDock, mapSearchTarget, selectedMissionId, setSelectedMissionId, setMapSearchTarget } = useShellStore();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const mapApi = usePlatformMapStore((state) => state.api);
  const setTool = usePlatformMapStore((state) => state.setTool);
  const setHome = usePlatformMapStore((state) => state.setHome);
  const position = useVpsMapStore((state) => state.position);
  const telemetryMessage = useVpsMapStore((state) => state.telemetryMessage);
  const readyMaps = regions.filter((region) => region.last_downloaded);
  const [selectedMap, setSelectedMap] = useVpsState("selectedMap");
  const selectedRegion = selectedMap !== "world" ? regions.find((region) => region.id === selectedMap) : undefined;
  const activeMap = selectedRegion;
  const [toolMode, setToolMode] = useVpsState("toolMode");
  const [borderPoints, setBorderPoints] = useVpsState("borderPoints");
  const [borderCutShape, setBorderCutShape] = useVpsState("borderCutShape");
  const [waypoints, setWaypoints] = useVpsState("waypoints");
  const [drawingMessage, setDrawingMessage] = useVpsState("drawingMessage");
  const [cutEstimate, setCutEstimate] = useState<MapUsageEstimate | null>(null);
  const [cutEstimateLoading, setCutEstimateLoading] = useState(false);
  const [cutEstimateError, setCutEstimateError] = useState<string | null>(null);
  const [cutDownloadZoom, setCutDownloadZoom] = useVpsState("cutDownloadZoom");
  const [multiLayerMap, setMultiLayerMap] = useVpsState("multiLayerMap");
  const [downloadProviders, setDownloadProviders] = useState<MapProvider[]>([]);
  const [selectedProviderIds, setSelectedProviderIds] = useVpsState("selectedProviderIds");
  const [cutLimitOverride, setCutLimitOverride] = useVpsState("cutLimitOverride");
  const [cutInstalling, setCutInstalling] = useVpsState("cutInstalling");
  const [cutInstallProgress, setCutInstallProgress] = useVpsState("cutInstallProgress");
  const [defaultOutputRoot, setDefaultOutputRoot] = useState("");
  const [savedMissions, setSavedMissions] = useVpsState("savedMissions");
  const [missionMode, setMissionMode] = useVpsState("missionMode");
  const [selectedMissionIds, setSelectedMissionIds] = useVpsState("selectedMissionIds");
  const [editingMissionId, setEditingMissionId] = useVpsState("editingMissionId");
  const positionPort = Number(localStorage.getItem("vision_nav_position_udp_port") || 17660);
  const currentPosition = positionLatLon(position);
  const positionState = positionTone(position);
  const mapState = selectedMap === "world" ? "ready" : activeMap?.active_bundle_path || activeMap?.lifecycle_state === "active" ? "active" : readyMaps.length ? "ready" : "warning";
  const orderedBorderPoints = useMemo(
    () => borderCutShape === "polygon" ? orderPolygonPoints(borderPoints) : borderPoints,
    [borderCutShape, borderPoints],
  );
  const cutEstimateBounds = useMemo(
    () => orderedBorderPoints.length ? pointsBounds(orderedBorderPoints) : null,
    [orderedBorderPoints],
  );
  const cutEstimateReady = borderCutShape === "box" ? orderedBorderPoints.length === 4 : orderedBorderPoints.length >= 3;
  const cutEstimateZoom = Math.max(15, Math.min(MAP_MAX_ZOOM, Math.round(cutDownloadZoom)));
  const fallbackCutAreaKm2 = cutEstimateBounds
    ? borderCutShape === "polygon" ? polygonAreaKm2(orderedBorderPoints) : bboxAreaKm2(cutEstimateBounds)
    : 0;
  const cutPreviewPoint: [number, number] | null = cutEstimateBounds
    ? [
        (cutEstimateBounds.lat_min + cutEstimateBounds.lat_max) / 2,
        (cutEstimateBounds.lon_min + cutEstimateBounds.lon_max) / 2,
      ]
    : null;
  const cutPreviewUrl = satellitePreviewUrl(cutPreviewPoint, cutEstimateZoom);
  const apiKeys = useMemo(() => providerApiKeys(profile), [profile]);
  const readyDownloadProviders = useMemo(
    () => downloadProviders.filter((provider) => provider.kind !== "vector" && provider.enabled && Boolean(provider.url_template)),
    [downloadProviders],
  );
  const selectedReadyProviderIds = useMemo(
    () => selectedProviderIds.filter((id) => {
      const provider = readyDownloadProviders.find((item) => item.id === id);
      return provider && isDownloadProviderReady(provider, apiKeys);
    }),
    [apiKeys, readyDownloadProviders, selectedProviderIds],
  );
  const maxMapAreaKm2 = profile?.max_map_area_km2;
  const maxDownloadSizeGb = profile?.max_map_download_size_gb ?? 20;
  const mapLimitWarnings = useMemo(() => {
    if (!cutEstimate) return [];
    const warnings: string[] = [];
    if (typeof maxMapAreaKm2 === "number" && maxMapAreaKm2 > 0 && cutEstimate.area_km2 > maxMapAreaKm2) {
      warnings.push(`Map area ${cutEstimate.area_km2.toFixed(2)} km2 exceeds settings limit ${maxMapAreaKm2.toFixed(2)} km2.`);
    }
    if (typeof maxDownloadSizeGb === "number" && maxDownloadSizeGb > 0 && cutEstimate.estimated_disk_mb > maxDownloadSizeGb * 1024) {
      warnings.push(`Estimated disk size ${formatMegabytes(cutEstimate.estimated_disk_mb)} exceeds settings limit ${formatMegabytes(maxDownloadSizeGb * 1024)}.`);
    }
    return warnings;
  }, [cutEstimate, maxDownloadSizeGb, maxMapAreaKm2]);
  const mapLimitExceeded = mapLimitWarnings.length > 0;
  const missionsForActiveMap = useMemo(
    () => activeMap ? savedMissions.filter((mission) => mission.map_id === activeMap.id) : [],
    [activeMap, savedMissions],
  );
  const editingMission = editingMissionId ? savedMissions.find((mission) => mission.id === editingMissionId) : undefined;
  const canSaveMission = Boolean(activeMap) && (borderPoints.length >= 3 || waypoints.length > 0);
  const selectedMissionIdSet = useMemo(() => new Set(selectedMissionIds), [selectedMissionIds]);
  const activeMissionOverlays = missionMode && activeMap
    ? missionsForActiveMap.filter((mission) => selectedMissionIdSet.has(mission.id))
    : [];

  useEffect(() => {
    if (!activeMap || !selectedMissionId || selectedMissionIds.length > 0) return;
    if (missionsForActiveMap.some((mission) => mission.id === selectedMissionId)) {
      setSelectedMissionIds([selectedMissionId]);
    }
  }, [activeMap, missionsForActiveMap, selectedMissionId, selectedMissionIds.length]);

  useEffect(() => {
    setSelectedMissionIds((current) => {
      const next = current.filter((id) => missionsForActiveMap.some((mission) => mission.id === id));
      return next.length === current.length ? current : next;
    });
    if (editingMissionId && !savedMissions.some((mission) => mission.id === editingMissionId)) {
      setEditingMissionId(null);
    }
    if (missionMode && (!activeMap || missionsForActiveMap.length === 0)) {
      setMissionMode(false);
    }
  }, [activeMap, editingMissionId, missionMode, missionsForActiveMap, savedMissions]);

  useEffect(() => {
    if (selectedMap !== "world" && !regions.some((region) => region.id === selectedMap)) {
      setSelectedMap("world");
    }
  }, [regions, selectedMap]);

  useEffect(() => {
    let cancelled = false;
    async function loadDefaultOutputRoot() {
      try {
        const home = await homeDir();
        const root = await join(home, "DroneVisionNav", "maps");
        if (!cancelled) setDefaultOutputRoot(root);
      } catch {
        if (!cancelled) setDefaultOutputRoot("~/DroneVisionNav/maps");
      }
    }
    void loadDefaultOutputRoot();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    cmd.listMapProviders()
      .then((items) => setDownloadProviders(items.filter((provider) => provider.kind !== "vector")))
      .catch(() => setDownloadProviders([]));
  }, []);

  useEffect(() => {
    if (!readyDownloadProviders.length) return;
    setSelectedProviderIds((current) => {
      const validCurrent = current.filter((id) => {
        const provider = readyDownloadProviders.find((item) => item.id === id);
        return provider && isDownloadProviderReady(provider, apiKeys);
      });
      if (validCurrent.length) return validCurrent;
      const defaults = ["usgs-imagery", "esri-world-imagery"]
        .map((id) => readyDownloadProviders.find((provider) => provider.id === id && isDownloadProviderReady(provider, apiKeys))?.id)
        .filter((id): id is MapProviderId => Boolean(id));
      return defaults.length ? defaults : readyDownloadProviders.filter((provider) => isDownloadProviderReady(provider, apiKeys)).slice(0, 1).map((provider) => provider.id);
    });
  }, [apiKeys, readyDownloadProviders]);

  useEffect(() => {
    if (!mapLimitExceeded) setCutLimitOverride(false);
  }, [mapLimitExceeded]);

  // Fit the map when the operator picks another map, not when switching products.
  const fittedSelection = useRef(selectedMap);
  useEffect(() => {
    if (!mapApi || mapSearchTarget || fittedSelection.current === selectedMap) return;
    fittedSelection.current = selectedMap;
    if (activeMap) {
      mapApi.fitPoints(regionPolygon(activeMap), 14);
      return;
    }
    if (selectedMap === "world") {
      mapApi.focus(PLACEHOLDER_ORG_COUNTRY_CENTER, PLACEHOLDER_ORG_COUNTRY_ZOOM);
    }
  }, [activeMap, mapApi, mapSearchTarget, selectedMap]);

  useEffect(() => {
    if (!cutEstimateReady || !cutEstimateBounds) {
      setCutEstimate(null);
      setCutEstimateError(null);
      setCutEstimateLoading(false);
      return;
    }
    if (!selectedReadyProviderIds.length) {
      setCutEstimate(null);
      setCutEstimateError(null);
      setCutEstimateLoading(false);
      return;
    }

    let cancelled = false;
    setCutEstimateLoading(true);
    setCutEstimateError(null);
    const zoomLevels = multiLayerMap
      ? Array.from({ length: cutEstimateZoom - 15 + 1 }, (_, index) => 15 + index)
      : [cutEstimateZoom];
    Promise.all(zoomLevels.map((zoomLevel) => cmd.estimateMapUsage({
        bbox: cutEstimateBounds,
        zoom: zoomLevel,
        cut_shape: borderCutShape,
        polygon_points: borderCutShape === "polygon" ? orderedBorderPoints : undefined,
        provider_ids: selectedReadyProviderIds,
        api_keys: apiKeys,
      })))
      .then((estimate) => {
        if (cancelled) return;
        setCutEstimate(multiLayerMap ? combineLayeredEstimates(estimate) : sumSelectedProviderTotals(estimate[0]));
      })
      .catch((error) => {
        if (!cancelled) {
          setCutEstimate(null);
          setCutEstimateError(String(error));
        }
      })
      .finally(() => {
        if (!cancelled) setCutEstimateLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiKeys, borderCutShape, cutEstimateBounds, cutEstimateReady, cutEstimateZoom, multiLayerMap, orderedBorderPoints, selectedReadyProviderIds]);

  const openMapsPanel = () => {
    resetRightDock();
    pushRightDock("maps");
  };

  const saveBorderCut = async () => {
    if (cutInstalling) return;
    if (!cutEstimateBounds || !cutEstimateReady) return;
    if (!selectedReadyProviderIds.length) {
      setCutInstallProgress({
        label: "Select at least one ready provider before installing the cut.",
        percent: 0,
        tone: "error",
      });
      return;
    }
    if (cutEstimate?.over_100_km2) {
      const confirmed = window.confirm(`Selected map cut is ${cutEstimate.area_km2.toFixed(1)} km2. Continue installing this large map?`);
      if (!confirmed) return;
    }
    const id = `manual-border-${Date.now()}`;
    const savedPoints = borderCutShape === "polygon" ? orderPolygonPoints(borderPoints) : borderPoints;
    const bounds = pointsBounds(savedPoints);
    const name = `Map cut ${new Date().toLocaleString()}`;
    const outputDir = `${defaultOutputRoot || "~/DroneVisionNav/maps"}/${slugifyPathSegment(name)}-${id.replace(/^manual-border-/, "")}`;
    const nextRegion: Region = {
      id,
      name,
      ...bounds,
      zoom: cutEstimateZoom,
      source: "folder",
      output_path: outputDir,
      cut_shape: borderCutShape,
      polygon_points: savedPoints,
      lifecycle_state: "local",
      tile_count: cutEstimate?.tile_count ?? savedPoints.length,
      file_size_mb: cutEstimate?.estimated_disk_mb,
      gsd_m_per_px: cutEstimate?.gsd_m_per_px,
      multi_layer_map: multiLayerMap,
      min_zoom: multiLayerMap ? 15 : cutEstimateZoom,
      zoom_levels: multiLayerMap
        ? Array.from({ length: cutEstimateZoom - 15 + 1 }, (_, index) => 15 + index)
        : [cutEstimateZoom],
      location_label: borderCutShape === "box" ? "Manual box cut" : "Manual polygon cut",
    };
    setCutInstalling(true);
    setCutInstallProgress({
      label: "Preparing map install...",
      percent: 4,
      tone: "active",
    });
    let unlisten: (() => void) | null = null;
    try {
      unlisten = await listen<DownloadProgress>("tile-progress", (event) => {
        setCutInstallProgress({
          label: "Installing map tiles...",
          percent: Math.max(4, Math.min(98, event.payload.percent)),
          current: event.payload.current,
          total: event.payload.total,
          tone: "active",
        });
      });
      const result = await cmd.downloadMapRegion({
        bbox: bounds,
        zoom: cutEstimateZoom,
        min_zoom: multiLayerMap ? 15 : cutEstimateZoom,
        multi_layer_map: multiLayerMap,
        output_dir: outputDir,
        cut_shape: borderCutShape,
        polygon_points: borderCutShape === "polygon" ? savedPoints : undefined,
        provider_ids: selectedReadyProviderIds,
        api_keys: apiKeys,
        confirm_over_100_km2: true,
        allow_large_tile_count: cutLimitOverride,
      });
      const installedRegion: Region = {
        ...nextRegion,
        last_downloaded: new Date().toISOString(),
        tile_count: result.tile_count,
        file_size_mb: result.actual_mb ?? cutEstimate?.estimated_disk_mb,
        gsd_m_per_px: result.gsd_m_per_px,
        georef_source: result.georef_source,
        georef_confidence: result.georef_confidence,
        georef_crs: result.georef_crs,
      };
      const nextRegions = [...regions, installedRegion];
      addRegion(installedRegion);
      await cmd.saveRegions(nextRegions);
      setSelectedMap(installedRegion.id);
      setMapSearchTarget(null);
      mapApi?.fitPoints(regionPolygon(installedRegion), 14);
      setBorderPoints([]);
      setToolMode("idle");
      setDrawingMessage(null);
      setCutInstallProgress({
        label: "Map cut installed and saved.",
        percent: 100,
        current: result.tile_count,
        total: result.tile_count,
        tone: "success",
      });

      // Report usage to Proxigo cloud
      const moduleSerial = profile?.proxigo_module_serial;
      if (proxigoSession && moduleSerial) {
        const reportKm2 = cutEstimate?.area_km2 ?? (borderCutShape === "polygon" ? polygonAreaKm2(savedPoints) : bboxAreaKm2(bounds));
        // Optimistically update quota display
        const orgCtx = cloudAccount?.org ?? null;
        if (cloudAccount) {
          const delta = reportKm2;
          setCloudAccount(orgCtx
            ? { ...cloudAccount, org: { ...orgCtx, org_km2_used: orgCtx.org_km2_used + delta, org_km2_remaining: Math.max(0, orgCtx.org_km2_remaining - delta), my_km2_used: orgCtx.my_km2_used + delta } }
            : { ...cloudAccount, km2_used: cloudAccount.km2_used + delta, km2_remaining: Math.max(0, cloudAccount.km2_remaining - delta) });
        }
        let activeSession = proxigoSession;
        proxigo.withSession(
          proxigoSession,
          async (fresh) => {
            activeSession = fresh;
            setProxigoSession(fresh);
            if (profile) {
              const updated = { ...profile, proxigo_access_token: fresh.access_token, proxigo_refresh_token: fresh.refresh_token, proxigo_token_expires_at: fresh.expires_at };
              setProfile(updated);
              cmd.saveProfile(updated).catch(() => {});
            }
          },
          (s) => proxigo.reportMapDownload(s, reportKm2, moduleSerial, installedRegion.id, bounds)
        )
          .then((usageResult) => {
            const msg = `Recorded ${reportKm2.toFixed(2)} km² — total this month: ${usageResult.total_km2_this_month.toFixed(2)} km²`;
            setLastUsageEvent({ status: "ok", message: msg, timestamp: Date.now() });
            if (cloudAccount) {
              const confirmed = usageResult.total_km2_this_month;
              setCloudAccount(orgCtx
                ? { ...cloudAccount, org: { ...orgCtx, org_km2_used: confirmed, org_km2_remaining: Math.max(0, orgCtx.org_km2_limit - confirmed), my_km2_used: confirmed } }
                : { ...cloudAccount, km2_used: confirmed, km2_remaining: Math.max(0, cloudAccount.km2_limit - confirmed) });
            }
            proxigo.getAccount(activeSession).then(setCloudAccount).catch(() => {});
          })
          .catch((err) => {
            const msg = err instanceof Error ? err.message : String(err);
            setLastUsageEvent({ status: "error", message: `Usage report failed: ${msg}`, timestamp: Date.now() });
            proxigo.getAccount(activeSession).then(setCloudAccount).catch(() => {});
          });
      }
    } catch (error) {
      console.warn("Failed to install manual border cut", error);
      setCutInstallProgress({
        label: `Map install failed: ${String(error)}`,
        percent: 100,
        tone: "error",
      });
    } finally {
      unlisten?.();
      setCutInstalling(false);
    }
  };

  const saveMission = () => {
    if (!activeMap) {
      setDrawingMessage("Select or install a saved map before saving a mission.");
      openMapsPanel();
      return;
    }
    if (!canSaveMission) return;
    const now = new Date().toISOString();
    const existing = editingMissionId ? savedMissions.find((mission) => mission.id === editingMissionId) : undefined;
    const bounds = missionBoundsFromParts(borderPoints, waypoints);
    const defaultName = existing?.name ?? `Mission ${new Date().toLocaleString()}`;
    const name = window.prompt("Mission name", defaultName)?.trim();
    if (!name) return;
    const view = mapApi?.getView();
    const center: [number, number] = bounds
      ? [(bounds.lat_min + bounds.lat_max) / 2, (bounds.lon_min + bounds.lon_max) / 2]
      : view?.center ?? PLACEHOLDER_ORG_COUNTRY_CENTER;
    const mission: SavedMission = {
      id: existing?.id ?? createSavedMissionId(),
      name,
      created_at: existing?.created_at ?? now,
      updated_at: now,
      source: existing?.source ?? "dashboard",
      map_id: activeMap.id,
      map_label: activeMap.name,
      center,
      zoom: clampMapZoom(view?.zoom ?? 14),
      border_points: borderCutShape === "polygon" ? orderPolygonPoints(borderPoints) : borderPoints,
      waypoints,
      bounds,
    };

    upsertSavedMission(mission);
    setSavedMissions((current) => [mission, ...current.filter((item) => item.id !== mission.id)]);
    setSelectedMissionId(mission.id);
    setSelectedMissionIds([mission.id]);
    setMissionMode(true);
    setEditingMissionId(null);
    setToolMode("idle");
    setDrawingMessage(null);
    resetRightDock();
    pushRightDock("missions");
  };

  const clearMapTool = () => {
    if (cutInstalling) return;
    setToolMode("idle");
    setBorderCutShape("polygon");
    setBorderPoints([]);
    setWaypoints([]);
    setDrawingMessage(null);
    setCutInstallProgress(null);
    setEditingMissionId(null);
  };

  const handleToolModeChange = (mode: MapToolMode) => {
    if (cutInstalling) return;
    if (mode === "waypoint" && !activeMap) {
      setDrawingMessage("Select or install a saved map before plotting mission waypoints.");
      openMapsPanel();
      return;
    }
    setCutInstallProgress(null);
    setDrawingMessage(null);
    setToolMode(mode);
    if (mode === "box") {
      setBorderCutShape("box");
      setBorderPoints([]);
    } else if (mode === "polygon") {
      setBorderCutShape("polygon");
      setBorderPoints([]);
    }
  };

  const startMissionDraft = () => {
    if (!activeMap) {
      setDrawingMessage("Select or install a saved map before creating a mission.");
      openMapsPanel();
      return;
    }
    if (editingMissionId) {
      setMissionMode(false);
      setToolMode("waypoint");
      setDrawingMessage(`Editing ${editingMission?.name ?? "mission"}. Add waypoints or select a different map, then Save Mission.`);
      return;
    }
    setMissionMode(false);
    setEditingMissionId(null);
    setBorderCutShape("polygon");
    setBorderPoints([]);
    setWaypoints([]);
    setToolMode("waypoint");
    setSelectedMissionIds([]);
    setSelectedMissionId(null);
    setDrawingMessage(`Creating mission for ${activeMap.name}. Click the map to add waypoints, then Save Mission.`);
    mapApi?.fitPoints(regionPolygon(activeMap), 14);
  };

  const editMission = (missionId: string) => {
    const mission = savedMissions.find((item) => item.id === missionId);
    if (!mission) return;
    const missionMap = mission.map_id ? regions.find((region) => region.id === mission.map_id) : undefined;
    if (missionMap && selectedMap !== missionMap.id) {
      setSelectedMap(missionMap.id);
      setMapSearchTarget(null);
      mapApi?.fitPoints(regionPolygon(missionMap), 14);
    }
    setMissionMode(false);
    setEditingMissionId(mission.id);
    setSelectedMissionIds([mission.id]);
    setSelectedMissionId(mission.id);
    setBorderCutShape("polygon");
    setBorderPoints(mission.border_points ?? []);
    setWaypoints(mission.waypoints ?? []);
    setToolMode("waypoint");
    setDrawingMessage(`Editing ${mission.name}. Add waypoints or select a different map, then Save Mission.`);
  };

  const undoWaypoint = () => {
    setWaypoints((current) => current.slice(0, -1));
  };

  const toggleMissionMode = () => {
    if (missionMode) {
      setMissionMode(false);
      return;
    }
    if (!activeMap) {
      setMissionMode(false);
      setDrawingMessage("Create, import, or select a saved map before Mission Mode.");
      openMapsPanel();
      return;
    }
    if (missionsForActiveMap.length === 0) {
      startMissionDraft();
      return;
    }
    setToolMode("idle");
    setBorderCutShape("polygon");
    setBorderPoints([]);
    setWaypoints([]);
    setDrawingMessage(null);
    setMissionMode(true);
    const currentValid = selectedMissionIds.find((id) => missionsForActiveMap.some((mission) => mission.id === id));
    const nextMissionId = currentValid ?? missionsForActiveMap[0]?.id;
    if (nextMissionId) {
      setSelectedMissionIds([nextMissionId]);
      setSelectedMissionId(nextMissionId);
    }
  };

  const toggleMissionSelection = (missionId: string) => {
    if (!missionsForActiveMap.some((mission) => mission.id === missionId)) return;
    setSelectedMissionIds((current) => {
      const next = current.includes(missionId)
        ? current.filter((id) => id !== missionId)
        : [...current, missionId];
      setSelectedMissionId(next[next.length - 1] ?? null);
      return next;
    });
  };

  const handleDrawBox = useCallback((points: [number, number][]) => {
    setBorderCutShape("box");
    setBorderPoints(points);
    setDrawingMessage(null);
  }, []);

  const handleDrawPoint = useCallback((point: [number, number]) => {
    if (toolMode === "polygon") {
      setBorderCutShape("polygon");
      setBorderPoints((current) => {
        setDrawingMessage(null);
        return orderPolygonPoints([...current, point]);
      });
    }
    if (toolMode === "waypoint") {
      setDrawingMessage(null);
      setWaypoints((current) => [...current, point]);
    }
  }, [toolMode]);

  const toggleDownloadProvider = useCallback((providerId: MapProviderId) => {
    setSelectedProviderIds((current) => (
      current.includes(providerId)
        ? current.filter((id) => id !== providerId)
        : [...current, providerId]
    ));
    setCutLimitOverride(false);
  }, []);

  const handleMapSelectionChange = useCallback((id: MapSelectionValue) => {
    setMapSearchTarget(null);
    setSelectedMap(id);
    setMissionMode(false);
    setSelectedMissionIds([]);
    setSelectedMissionId(null);
    const region = id !== "world" ? regions.find((item) => item.id === id) : undefined;
    if (region) {
      setDrawingMessage(editingMissionId ? `Editing mission map changed to ${region.name}. Save Mission to update.` : null);
      mapApi?.fitPoints(regionPolygon(region), 14);
    } else if (id === "world") {
      setDrawingMessage(editingMissionId ? "Select a saved map before saving this mission edit." : null);
      mapApi?.focus(PLACEHOLDER_ORG_COUNTRY_CENTER, PLACEHOLDER_ORG_COUNTRY_ZOOM);
    }
  }, [editingMissionId, mapApi, regions, setMapSearchTarget, setSelectedMissionId]);

  // Drawing tools and the Recenter target live in the platform map.
  useEffect(() => {
    if (toolMode === "idle") setTool(null);
    else if (toolMode === "box") setTool({ mode: "box", color: "#FF6600", onBox: handleDrawBox });
    else setTool({ mode: "point", onPoint: handleDrawPoint });
  }, [handleDrawBox, handleDrawPoint, setTool, toolMode]);
  useEffect(() => {
    setHome(activeMap ? regionPolygon(activeMap) : null);
  }, [activeMap, setHome]);
  useEffect(() => () => {
    setTool(null);
    setHome(null);
  }, [setHome, setTool]);

  const missionKey = activeMissionOverlays.map((mission) => mission.id).join("|");
  useEffect(() => {
    if (!missionMode || !activeMissionOverlays.length) return;
    mapApi?.fitPoints(activeMissionOverlays.flatMap((mission) => [...mission.border_points, ...mission.waypoints]), 16);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missionKey, missionMode]);

  return (
    <>
      <MapRightControls
        regions={regions}
        activeMap={activeMap}
        selectedMap={selectedMap}
        onMapChange={handleMapSelectionChange}
        onOpenMapsPanel={openMapsPanel}
        mode={toolMode}
        cutShape={borderCutShape}
        borderPointCount={borderPoints.length}
        waypointCount={waypoints.length}
        drawingMessage={drawingMessage}
        onModeChange={handleToolModeChange}
        onSaveBorder={saveBorderCut}
        onSaveMission={saveMission}
        onClearDrawing={clearMapTool}
        canSaveMission={canSaveMission}
        missionDraftActive={toolMode === "waypoint" || Boolean(editingMissionId)}
        editingMissionName={editingMission?.name}
        onStartMissionDraft={startMissionDraft}
        onEditMission={editMission}
        onUndoWaypoint={undoWaypoint}
        cutEstimate={cutEstimate}
        cutEstimateLoading={cutEstimateLoading}
        cutEstimateError={cutEstimateError}
        cutEstimateZoom={cutEstimateZoom}
        onCutEstimateZoomChange={setCutDownloadZoom}
        cutInstalling={cutInstalling}
        cutInstallProgress={cutInstallProgress}
        multiLayerMap={multiLayerMap}
        onMultiLayerMapChange={setMultiLayerMap}
        providers={readyDownloadProviders}
        selectedProviderIds={selectedProviderIds}
        apiKeys={apiKeys}
        onToggleProvider={toggleDownloadProvider}
        fallbackCutAreaKm2={fallbackCutAreaKm2}
        cutPreviewUrl={cutPreviewUrl}
        mapLimitWarnings={mapLimitWarnings}
        mapLimitOverride={cutLimitOverride}
        onMapLimitOverrideChange={setCutLimitOverride}
        cutSaveBlocked={mapLimitExceeded && !cutLimitOverride}
        missions={missionsForActiveMap}
        missionMode={missionMode}
        missionModeDisabled={!activeMap || missionsForActiveMap.length === 0}
        missionCreateRequired={Boolean(activeMap && missionsForActiveMap.length === 0)}
        activeMapName={activeMap?.name}
        selectedMissionIds={selectedMissionIds}
        onMissionModeToggle={toggleMissionMode}
        onToggleMissionSelection={toggleMissionSelection}
        onSelectAllMissions={() => setSelectedMissionIds(missionsForActiveMap.map((mission) => mission.id))}
        onClearMissionSelection={() => setSelectedMissionIds([])}
      />
      <section className={cn("pointer-events-none absolute top-3 z-[1100] w-[320px] rounded-lg border border-border bg-bg-base/92 p-3 shadow-2xl backdrop-blur-sm transition-[left] duration-200", leftInsetClass)}>
        <div className="flex items-center gap-2">
          <span className={cn("ops-led", activeDevice ? "ops-led-ready" : "ops-led-offline")} />
          <Activity size={13} className={activeDevice ? "text-status-ready" : "text-slate-500"} />
          <span className="text-[11px] font-semibold text-slate-500">Connection</span>
          <span className="ml-auto truncate font-data-mono text-[11px] text-slate-400">{activeDevice?.name ?? "Offline"}</span>
        </div>
        <div className="mt-3 grid gap-2">
          <HudRow
            Icon={MapIcon}
            label="Active Map"
            value={selectedMap === "world" ? "World Map" : activeMap?.name ?? "No map selected"}
            detail={selectedMap === "world" ? "Global imagery base layer" : activeMap?.last_downloaded ? formatDate(activeMap.last_downloaded) : "Use Maps panel"}
            tone={mapState}
          />
          <HudRow
            Icon={Navigation}
            label="Position"
            value={positionLabel(position)}
            detail={currentPosition ? `${currentPosition[0].toFixed(6)}, ${currentPosition[1].toFixed(6)}` : `UDP ${positionPort} ${telemetryMessage}`}
            tone={positionState}
          />
        </div>
      </section>
    </>
  );
}

