import type { CityLane, CityPose } from "./city-contract.ts";
export type Point3 = { x: number; y: number; z: number };
/** CARLA is left handed with Z up; Three is right handed with Y up. */
export function cityToThree(point: Point3, lift = 0): [number, number, number] {
  return [point.x, point.z + lift, point.y];
}
export function cityBoxCenter(pose: CityPose, offset: Point3): Point3 {
  const c = Math.cos(pose.heading_rad),
    s = Math.sin(pose.heading_rad);
  return {
    x: pose.x + c * offset.x - s * offset.y,
    y: pose.y + s * offset.x + c * offset.y,
    z: pose.z + offset.z,
  };
}
export function laneRibbonData(lanes: CityLane[]) {
  const positions: number[] = [],
    indices: number[] = [],
    isolated: { point: CityPose; width: number }[] = [];
  for (const lane of lanes) {
    if (lane.points.length === 1) {
      isolated.push({ point: lane.points[0], width: lane.width_m ?? 3.5 });
      continue;
    }
    const base = positions.length / 3,
      half = (lane.width_m ?? 3.5) / 2;
    for (const point of lane.points) {
      const dx = -Math.sin(point.heading_rad) * half,
        dy = Math.cos(point.heading_rad) * half;
      positions.push(
        ...cityToThree({ x: point.x - dx, y: point.y - dy, z: point.z }),
        ...cityToThree({ x: point.x + dx, y: point.y + dy, z: point.z }),
      );
    }
    for (let i = 0; i < lane.points.length - 1; i++) {
      const a = base + i * 2;
      indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
  }
  return { positions, indices, isolated };
}
export function cityBounds(points: Point3[]) {
  let xmin = Infinity,
    xmax = -Infinity,
    ymin = Infinity,
    ymax = -Infinity,
    zmin = Infinity,
    zmax = -Infinity;
  for (const p of points) {
    xmin = Math.min(xmin, p.x);
    xmax = Math.max(xmax, p.x);
    ymin = Math.min(ymin, p.y);
    ymax = Math.max(ymax, p.y);
    zmin = Math.min(zmin, p.z);
    zmax = Math.max(zmax, p.z);
  }
  if (!points.length)
    return { xmin: -20, xmax: 20, ymin: -20, ymax: 20, zmin: 0, zmax: 3 };
  return {
    xmin: xmin - 7,
    xmax: xmax + 7,
    ymin: ymin - 7,
    ymax: ymax + 7,
    zmin,
    zmax: zmax + 3,
  };
}
