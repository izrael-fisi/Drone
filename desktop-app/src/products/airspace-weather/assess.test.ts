import { describe, expect, it } from "vitest";
import type { LatLon } from "../../platform/types";
import type { AirspaceArea, UasGridCell, WeatherReport } from "./sources";
import { assess, DEFAULT_LIMITS } from "./store";

const POINT: LatLon = [39.75, -104.95]; // inside FAA coverage
const SQUARE: LatLon[] = [[39.7, -105.0], [39.8, -105.0], [39.8, -104.9], [39.7, -104.9]];

const loaded = <T,>(data: T) => ({ data, loading: false, error: null });
const loading = { data: null, loading: true, error: null };

function weather(over: Partial<WeatherReport["now"]> = {}) {
  return loaded<WeatherReport>({
    timezone: "UTC",
    hours: [],
    now: {
      time: "2026-11-05T12:00",
      temperatureC: 15,
      windMs: 3,
      gustMs: 5,
      windDirDeg: 180,
      precipitationMm: 0,
      cloudCoverPct: 0,
      visibilityM: 20_000,
      weatherCode: 0,
      ...over,
    },
  });
}

const classD: AirspaceArea = { id: "d", name: "TEST CLASS D", airspaceClass: "D", localType: "CLASS_D", floor: "surface", ceiling: "8,000 ft MSL", rings: [SQUARE] };
const cell = (ceilingFt: number): UasGridCell => ({ id: "c", ceilingFt, airport: "TST Test", rings: [SQUARE] });

function run(over: Partial<Parameters<typeof assess>[0]> = {}) {
  return assess({
    point: POINT,
    weather: weather(),
    airspace: loaded<AirspaceArea[]>([]),
    uasGrid: loaded<UasGridCell[]>([]),
    limits: DEFAULT_LIMITS,
    ...over,
  });
}

describe("airspace & weather go/no-go", () => {
  it("is Go in calm weather in uncontrolled airspace", () => {
    expect(run().overall).toBe("go");
  });

  it("is No-go when wind exceeds the limit", () => {
    expect(run({ weather: weather({ windMs: DEFAULT_LIMITS.maxWindMs + 1 }) }).overall).toBe("nogo");
  });

  it("is Caution inside surface Class D", () => {
    const result = run({ airspace: loaded([classD]) });
    expect(result.overall).toBe("caution");
    expect(result.airspaceHere).toHaveLength(1);
  });

  it("is No-go where the LAANC ceiling is 0 ft", () => {
    expect(run({ uasGrid: loaded([cell(0)]) }).overall).toBe("nogo");
  });

  it("never shows a verdict while a source is still loading", () => {
    expect(run({ airspace: loading }).overall).toBe("info");
    expect(run({ weather: loading }).overall).toBe("info");
  });
});
