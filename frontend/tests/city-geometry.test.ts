import test from "node:test";
import assert from "node:assert/strict";
import {
  cityBounds,
  cityBoxCenter,
  cityToThree,
  laneRibbonData,
} from "../src/components/city-sim/city-geometry.ts";
test("native CARLA Y-right/Z-up conversion reflects handedness without losing height", () => {
  assert.deepEqual(cityToThree({ x: 3, y: 8, z: 12 }), [3, 12, 8]);
  const center = cityBoxCenter(
    { x: 10, y: 20, z: 4, heading_rad: Math.PI / 2 },
    { x: 1, y: 2, z: 3 },
  );
  assert.ok(Math.abs(center.x - 8) < 1e-12);
  assert.ok(Math.abs(center.y - 21) < 1e-12);
  assert.equal(center.z, 7);
  assert.deepEqual(cityToThree(center), [8, 7, 21]);
});
test("elevated lane ribbons retain native heights/width and do not bridge disconnected lanes", () => {
  const geometry = laneRibbonData([
    {
      id: "east",
      width_m: 4,
      points: [
        { x: 0, y: 0, z: 3, heading_rad: 0 },
        { x: 10, y: 0, z: 5, heading_rad: 0 },
      ],
    },
    {
      id: "north",
      width_m: 2,
      points: [
        { x: 20, y: 20, z: 10, heading_rad: Math.PI / 2 },
        { x: 20, y: 30, z: 11, heading_rad: Math.PI / 2 },
      ],
    },
  ]);
  assert.deepEqual(
    geometry.positions.slice(0, 12),
    [0, 3, -2, 0, 3, 2, 10, 5, -2, 10, 5, 2],
  );
  assert.deepEqual(geometry.indices, [0, 1, 2, 1, 3, 2, 4, 5, 6, 5, 7, 6]);
  assert.equal(geometry.positions[13], 10);
  assert.equal(geometry.positions[19], 11);
});
test("a lone native lane observation stays an isolated point and cannot create a made-up road segment", () => {
  const point = { x: 10, y: 20, z: 3, heading_rad: 1 },
    geometry = laneRibbonData([
      { id: "isolated", points: [point], width_m: 3.5 },
    ]);
  assert.deepEqual(geometry.positions, []);
  assert.deepEqual(geometry.indices, []);
  assert.deepEqual(geometry.isolated, [{ point, width: 3.5 }]);
});
test("native bounds fit all coordinates, negative elevations and tall geometry", () => {
  const result = cityBounds([
    { x: -40, y: 20, z: -5 },
    { x: 50, y: -10, z: 13 },
  ]);
  assert.ok(result.xmin < -40 && result.xmax > 50);
  assert.ok(result.ymin < -10 && result.ymax > 20);
  assert.equal(result.zmin, -5);
  assert.equal(result.zmax, 16);
});
