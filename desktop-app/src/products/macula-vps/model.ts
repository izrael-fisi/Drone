import { useMemo } from "react";
import { deriveOperatorRuntimeModel, type OperatorRuntimeModel } from "../../lib/operatorRuntimeAdapter";
import { useAppStore } from "../../lib/store";

/** Macula VPS runtime view model (devices, maps, edge state, flights). */
export function useMaculaRuntimeModel(): OperatorRuntimeModel {
  const { devices, regions, activeDeviceId } = useAppStore();
  return useMemo(
    () => deriveOperatorRuntimeModel({ devices, regions, activeDeviceId }),
    [activeDeviceId, devices, regions],
  );
}
