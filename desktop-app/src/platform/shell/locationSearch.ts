export type LocationResult = {
  id: string;
  label: string;
  detail: string;
  lat: number;
  lon: number;
};

export type PopularLocation = LocationResult & {
  aliases?: string[];
  popularity: number;
};

export type ArcGisGeocodeCandidate = {
  address?: string;
  score?: number;
  location?: {
    x?: number;
    y?: number;
  };
  attributes?: {
    LongLabel?: string;
    ShortLabel?: string;
    Addr_type?: string;
    Type?: string;
    PlaceName?: string;
    City?: string;
    Region?: string;
    RegionAbbr?: string;
    Country?: string;
    CountryCode?: string;
    Rank?: number;
    PopRank?: number;
  };
};

export type ArcGisGeocodeResponse = {
  candidates?: ArcGisGeocodeCandidate[];
};

const POPULAR_LOCATION_HINTS: Record<string, string[]> = {
  "san francisco": ["san francisco", "california", "ca", "united states", "usa"],
  "new york": ["new york", "ny", "united states", "usa"],
  "los angeles": ["los angeles", "california", "ca", "united states", "usa"],
  chicago: ["chicago", "illinois", "il", "united states", "usa"],
  houston: ["houston", "texas", "tx", "united states", "usa"],
  phoenix: ["phoenix", "arizona", "az", "united states", "usa"],
  philadelphia: ["philadelphia", "pennsylvania", "pa", "united states", "usa"],
  "san antonio": ["san antonio", "texas", "tx", "united states", "usa"],
  "san diego": ["san diego", "california", "ca", "united states", "usa"],
  dallas: ["dallas", "texas", "tx", "united states", "usa"],
  "san jose": ["san jose", "california", "ca", "united states", "usa"],
  austin: ["austin", "texas", "tx", "united states", "usa"],
  jacksonville: ["jacksonville", "florida", "fl", "united states", "usa"],
  "fort worth": ["fort worth", "texas", "tx", "united states", "usa"],
  columbus: ["columbus", "ohio", "oh", "united states", "usa"],
  charlotte: ["charlotte", "north carolina", "nc", "united states", "usa"],
  indianapolis: ["indianapolis", "indiana", "in", "united states", "usa"],
  seattle: ["seattle", "washington", "wa", "united states", "usa"],
  denver: ["denver", "colorado", "co", "united states", "usa"],
  boston: ["boston", "massachusetts", "ma", "united states", "usa"],
  "washington dc": ["washington", "district of columbia", "dc", "united states", "usa"],
  washington: ["washington", "district of columbia", "dc", "united states", "usa"],
  portland: ["portland", "oregon", "or", "united states", "usa"],
  paris: ["paris", "france"],
  london: ["london", "england", "united kingdom", "uk"],
  tokyo: ["tokyo", "japan"],
  berlin: ["berlin", "germany"],
  rome: ["rome", "italy"],
  madrid: ["madrid", "spain"],
  sydney: ["sydney", "new south wales", "australia"],
  melbourne: ["melbourne", "victoria", "australia"],
  toronto: ["toronto", "ontario", "canada"],
  vancouver: ["vancouver", "british columbia", "canada"],
  montreal: ["montreal", "quebec", "canada"],
};

const POPULAR_LOCATION_AUTOCOMPLETE: PopularLocation[] = [
  { id: "popular-dubai", label: "Dubai", detail: "Dubai, United Arab Emirates", lat: 25.2048, lon: 55.2708, aliases: ["dxb", "dub"], popularity: 1000 },
  { id: "popular-shanghai", label: "Shanghai", detail: "Shanghai, China", lat: 31.2304, lon: 121.4737, aliases: ["sha", "shanghai china"], popularity: 995 },
  { id: "popular-new-york", label: "New York", detail: "New York, United States", lat: 40.7128, lon: -74.006, aliases: ["nyc", "new york city"], popularity: 990 },
  { id: "popular-london", label: "London", detail: "England, United Kingdom", lat: 51.5072, lon: -0.1276, aliases: ["lhr", "london uk"], popularity: 985 },
  { id: "popular-tokyo", label: "Tokyo", detail: "Tokyo, Japan", lat: 35.6762, lon: 139.6503, aliases: ["tokyo japan"], popularity: 980 },
  { id: "popular-paris", label: "Paris", detail: "Ile-de-France, France", lat: 48.8566, lon: 2.3522, aliases: ["paris france"], popularity: 975 },
  { id: "popular-singapore", label: "Singapore", detail: "Singapore", lat: 1.3521, lon: 103.8198, aliases: ["sin"], popularity: 970 },
  { id: "popular-hong-kong", label: "Hong Kong", detail: "Hong Kong, China", lat: 22.3193, lon: 114.1694, aliases: ["hk", "hkg"], popularity: 965 },
  { id: "popular-beijing", label: "Beijing", detail: "Beijing, China", lat: 39.9042, lon: 116.4074, aliases: ["pek"], popularity: 960 },
  { id: "popular-seoul", label: "Seoul", detail: "Seoul, South Korea", lat: 37.5665, lon: 126.978, aliases: ["sel"], popularity: 955 },
  { id: "popular-los-angeles", label: "Los Angeles", detail: "California, United States", lat: 34.0522, lon: -118.2437, aliases: ["la", "lax"], popularity: 950 },
  { id: "popular-san-francisco", label: "San Francisco", detail: "California, United States", lat: 37.7749, lon: -122.4194, aliases: ["sf", "sfo", "san francisco ca"], popularity: 945 },
  { id: "popular-mumbai", label: "Mumbai", detail: "Maharashtra, India", lat: 19.076, lon: 72.8777, aliases: ["bom"], popularity: 940 },
  { id: "popular-delhi", label: "Delhi", detail: "Delhi, India", lat: 28.6139, lon: 77.209, aliases: ["new delhi", "del"], popularity: 935 },
  { id: "popular-bangkok", label: "Bangkok", detail: "Bangkok, Thailand", lat: 13.7563, lon: 100.5018, aliases: ["bkk"], popularity: 930 },
  { id: "popular-istanbul", label: "Istanbul", detail: "Istanbul, Turkey", lat: 41.0082, lon: 28.9784, aliases: ["ist"], popularity: 925 },
  { id: "popular-berlin", label: "Berlin", detail: "Berlin, Germany", lat: 52.52, lon: 13.405, aliases: ["berlin germany"], popularity: 920 },
  { id: "popular-rome", label: "Rome", detail: "Lazio, Italy", lat: 41.9028, lon: 12.4964, aliases: ["roma"], popularity: 915 },
  { id: "popular-madrid", label: "Madrid", detail: "Madrid, Spain", lat: 40.4168, lon: -3.7038, aliases: ["madrid spain"], popularity: 910 },
  { id: "popular-sydney", label: "Sydney", detail: "New South Wales, Australia", lat: -33.8688, lon: 151.2093, aliases: ["syd"], popularity: 905 },
  { id: "popular-melbourne", label: "Melbourne", detail: "Victoria, Australia", lat: -37.8136, lon: 144.9631, aliases: ["mel"], popularity: 900 },
  { id: "popular-toronto", label: "Toronto", detail: "Ontario, Canada", lat: 43.6532, lon: -79.3832, aliases: ["yyz"], popularity: 895 },
  { id: "popular-vancouver", label: "Vancouver", detail: "British Columbia, Canada", lat: 49.2827, lon: -123.1207, aliases: ["yvr"], popularity: 890 },
  { id: "popular-chicago", label: "Chicago", detail: "Illinois, United States", lat: 41.8781, lon: -87.6298, aliases: ["ord"], popularity: 885 },
  { id: "popular-miami", label: "Miami", detail: "Florida, United States", lat: 25.7617, lon: -80.1918, aliases: ["mia"], popularity: 880 },
  { id: "popular-seattle", label: "Seattle", detail: "Washington, United States", lat: 47.6062, lon: -122.3321, aliases: ["sea"], popularity: 875 },
  { id: "popular-boston", label: "Boston", detail: "Massachusetts, United States", lat: 42.3601, lon: -71.0589, aliases: ["bos"], popularity: 870 },
  { id: "popular-washington-dc", label: "Washington DC", detail: "District of Columbia, United States", lat: 38.9072, lon: -77.0369, aliases: ["dc", "washington"], popularity: 865 },
  { id: "popular-mexico-city", label: "Mexico City", detail: "Mexico", lat: 19.4326, lon: -99.1332, aliases: ["cdmx", "mex"], popularity: 860 },
  { id: "popular-sao-paulo", label: "Sao Paulo", detail: "Sao Paulo, Brazil", lat: -23.5558, lon: -46.6396, aliases: ["sao"], popularity: 855 },
  { id: "popular-buenos-aires", label: "Buenos Aires", detail: "Argentina", lat: -34.6037, lon: -58.3816, aliases: ["buenos"], popularity: 850 },
  { id: "popular-cairo", label: "Cairo", detail: "Cairo, Egypt", lat: 30.0444, lon: 31.2357, aliases: ["cai"], popularity: 845 },
  { id: "popular-johannesburg", label: "Johannesburg", detail: "Gauteng, South Africa", lat: -26.2041, lon: 28.0473, aliases: ["jnb", "joburg"], popularity: 840 },
  { id: "popular-lagos", label: "Lagos", detail: "Lagos, Nigeria", lat: 6.5244, lon: 3.3792, aliases: ["los nigeria"], popularity: 835 },
  { id: "popular-nairobi", label: "Nairobi", detail: "Nairobi, Kenya", lat: -1.2921, lon: 36.8219, aliases: ["nbo"], popularity: 830 },
  { id: "popular-houston", label: "Houston", detail: "Texas, United States", lat: 29.7604, lon: -95.3698, aliases: ["iah"], popularity: 825 },
  { id: "popular-dallas", label: "Dallas", detail: "Texas, United States", lat: 32.7767, lon: -96.797, aliases: ["dfw"], popularity: 820 },
];

function normalizeLocationText(value: string) {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/\b(st|saint)\b/g, "saint")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

export function popularLocationAutocomplete(query: string) {
  const normalizedQuery = normalizeLocationText(query);
  if (normalizedQuery.length < 2) return [];

  return POPULAR_LOCATION_AUTOCOMPLETE.map((location) => {
    const label = normalizeLocationText(location.label);
    const terms = [location.label, location.detail, ...(location.aliases ?? [])].map(normalizeLocationText);
    const matchesWordPrefix = terms.some((term) => term.split(" ").some((part) => part.startsWith(normalizedQuery)));
    const matchesTermPrefix = terms.some((term) => term.startsWith(normalizedQuery));
    const matchesTerm = terms.some((term) => term.includes(normalizedQuery));
    if (!matchesWordPrefix && !matchesTermPrefix && !matchesTerm) return null;

    const score =
      location.popularity +
      (label.startsWith(normalizedQuery) ? 600 : 0) +
      (matchesTermPrefix ? 360 : 0) +
      (matchesWordPrefix ? 180 : 0) +
      (matchesTerm ? 40 : 0);

    return { location, score };
  })
    .filter((match): match is { location: PopularLocation; score: number } => Boolean(match))
    .sort((left, right) => right.score - left.score)
    .slice(0, 5)
    .map(({ location }) => location);
}

export function mergeLocationResults(primary: LocationResult[], secondary: LocationResult[]) {
  const seen = new Set<string>();

  return [...primary, ...secondary]
    .filter((result) => {
      const key = `${normalizeLocationText(result.label)}|${result.lat.toFixed(3)}|${result.lon.toFixed(3)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 5);
}

function locationCandidateText(candidate: ArcGisGeocodeCandidate) {
  const attributes = candidate.attributes;
  return [
    candidate.address,
    attributes?.LongLabel,
    attributes?.ShortLabel,
    attributes?.PlaceName,
    attributes?.City,
    attributes?.Region,
    attributes?.RegionAbbr,
    attributes?.Country,
    attributes?.CountryCode,
  ]
    .filter(Boolean)
    .join(" ");
}

export function locationPopularityScore(candidate: ArcGisGeocodeCandidate, query: string) {
  const normalizedQuery = normalizeLocationText(query);
  const attributes = candidate.attributes;
  const label = normalizeLocationText(attributes?.ShortLabel ?? attributes?.PlaceName ?? candidate.address ?? "");
  const placeName = normalizeLocationText(attributes?.PlaceName ?? attributes?.City ?? "");
  const detail = normalizeLocationText(locationCandidateText(candidate));
  const type = normalizeLocationText(`${attributes?.Addr_type ?? ""} ${attributes?.Type ?? ""}`);
  const rank = attributes?.PopRank ?? attributes?.Rank;
  let score = candidate.score ?? 0;

  if (label === normalizedQuery) score += 260;
  if (placeName === normalizedQuery) score += 220;
  if (label.startsWith(`${normalizedQuery} `)) score += 90;
  if (detail.includes(normalizedQuery)) score += 30;
  if (/\b(locality|city|municipality|populated place|metro area|admin1|admin2)\b/.test(type)) score += 90;
  if (/\b(point address|street address|street name|postal|poi)\b/.test(type)) score -= 80;
  if (detail.includes("united states") || detail.includes(" usa ")) score += 12;
  if (typeof rank === "number" && Number.isFinite(rank)) score += Math.max(0, 90 - rank);

  const hints = POPULAR_LOCATION_HINTS[normalizedQuery];
  if (hints) {
    const hintMatches = hints.filter((hint) => detail.includes(normalizeLocationText(hint))).length;
    if (hintMatches >= Math.min(2, hints.length)) score += 280 + hintMatches * 120;
  }

  return score;
}
