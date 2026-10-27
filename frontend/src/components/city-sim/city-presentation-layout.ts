import type { CityMap } from "./city-contract";

export type CityPresentationLayout = "auto" | "native_camera" | "analysis";

/** Call only with the map from the verified retained scene, never a preset title. */
export function isNativeTownMap(map: CityMap): boolean {
  if (map.engine !== "carla" || map.external || (map.generated !== undefined && map.generated !== null && map.generated !== false)) return false;
  return typeof map.native_name === "string" && /(?:^|\/)Town\d+(?:HD)?(?:_Opt)?$/i.test(map.native_name);
}

export function resolveCityPresentationLayout(map: CityMap, requested: CityPresentationLayout = "auto"): Exclude<CityPresentationLayout, "auto"> {
  return requested === "auto" ? isNativeTownMap(map) ? "native_camera" : "analysis" : requested;
}
