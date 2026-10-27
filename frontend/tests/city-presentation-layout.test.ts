import test from "node:test";
import assert from "node:assert/strict";
import { isNativeTownMap, resolveCityPresentationLayout } from "../src/components/city-sim/city-presentation-layout.ts";
import type { CityMap } from "../src/components/city-sim/city-contract.ts";

const town: CityMap = { engine: "carla", native_name: "Carla/Maps/Town10HD_Opt", lanes: [] };

test("native camera hero follows the verified native map identity rather than authored titles", () => {
  assert.equal(isNativeTownMap(town), true);
  assert.equal(resolveCityPresentationLayout(town), "native_camera");
  assert.equal(resolveCityPresentationLayout({ ...town, generated: false }), "native_camera");
  assert.equal(resolveCityPresentationLayout({ ...town, generated: { kind: "opendrive" } }), "analysis");
  assert.equal(resolveCityPresentationLayout({ ...town, external: { source: { id: "source-a" } } }), "analysis");
  assert.equal(isNativeTownMap({ ...town, native_name: "external__1234567890abcdef", external: { source: { id: "source-a" } } }), false);
  assert.equal(resolveCityPresentationLayout({ ...town, native_name: "OpenDriveMap", name: "Town10HD_Opt" }), "analysis");
  assert.equal(resolveCityPresentationLayout({ ...town, engine: "reference" }), "analysis");
  assert.equal(resolveCityPresentationLayout({ lanes: [], name: "도시 주행" }), "analysis");
});

test("explicit presentation choice changes layout without changing map provenance", () => {
  const source = JSON.stringify(town);
  assert.equal(resolveCityPresentationLayout(town, "analysis"), "analysis");
  assert.equal(resolveCityPresentationLayout({ lanes: [] }, "native_camera"), "native_camera");
  assert.equal(JSON.stringify(town), source);
});
