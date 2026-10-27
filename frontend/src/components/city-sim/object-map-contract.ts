import type { CatalogRef, CityMap, CityPose } from "./city-contract";
export type RoadGeometry = {
  kind: "line" | "arc" | "spiral";
  length_m: number;
  curvature_start: number;
  curvature_end: number;
};
export type RoadLane = {
  id: number;
  width_m: number;
  speed_limit_mps: number;
  lane_change: "none" | "both" | "left" | "right";
  closed: boolean;
  priority?: number;
};
export type ObjectRoad = {
  id: string;
  name: string;
  origin: CityPose;
  geometries: RoadGeometry[];
  lanes: RoadLane[];
  junction_id?: string | null;
};
export type ObjectMapSource = {
  schema_version: "object-map/1";
  name: string;
  roads: ObjectRoad[];
  connections: {
    from_road: string;
    from_lane: number;
    to_road: string;
    to_lane: number;
  }[];
  junctions: { id: string; name: string }[];
  signals: {
    id: string;
    road_id: string;
    s_m: number;
    t_m: number;
    kind: "traffic_light" | "stop" | "yield";
    orientation: "+" | "-";
    lane_ids: number[];
  }[];
  objects: {
    id: string;
    road_id: string;
    s_m: number;
    t_m: number;
    length_m: number;
    width_m: number;
    height_m: number;
  }[];
  rights: {
    license: string;
    source_url: string;
    redistribution: string;
    notes: string;
  };
  [key: string]: unknown;
};
export type ObjectMapRecord = CatalogRef & {
  project_id: string;
  definition: ObjectMapSource;
};
export type ObjectMapPreview = {
  issues: (
    | { message: string; severity?: string; road_id?: string; code?: string }
    | string
  )[];
  [key: string]: unknown;
};
export function emptyObjectMap(): ObjectMapSource {
  return {
    schema_version: "object-map/1",
    name: "새 도로 지도",
    roads: [],
    connections: [],
    junctions: [],
    signals: [],
    objects: [],
    rights: {
      license: "Internal authored",
      source_url: "",
      redistribution: "unknown",
      notes: "사용자 작성 지도 · 권리는 발행 전 확인",
    },
  };
}
export function newRoad(
  id: string,
  origin: CityPose = { x: 0, y: 0, z: 0, heading_rad: 0 },
): ObjectRoad {
  return {
    id,
    name: `도로 ${id}`,
    origin: { ...origin },
    geometries: [
      { kind: "line", length_m: 80, curvature_start: 0, curvature_end: 0 },
    ],
    lanes: [
      {
        id: -1,
        width_m: 3.5,
        speed_limit_mps: 12,
        lane_change: "both",
        closed: false,
      },
      {
        id: 1,
        width_m: 3.5,
        speed_limit_mps: 12,
        lane_change: "both",
        closed: false,
      },
    ],
  };
}
/** Local authoring preview only; the server compiler validates and publishes geometry. */
export function sampleRoad(road: ObjectRoad, step = 1): CityPose[] {
  let { x, y, z, heading_rad } = road.origin;
  const points: CityPose[] = [{ x, y, z, heading_rad }];
  for (const geometry of road.geometries) {
    if (!Number.isFinite(geometry.length_m) || geometry.length_m <= 0) continue;
    const count = Math.min(
        4000,
        Math.max(1, Math.ceil(geometry.length_m / step)),
      ),
      ds = geometry.length_m / count;
    for (let i = 0; i < count; i++) {
      const curvature =
        geometry.kind === "line"
          ? 0
          : geometry.kind === "arc"
            ? geometry.curvature_start
            : geometry.curvature_start +
              ((geometry.curvature_end - geometry.curvature_start) *
                (i + 0.5)) /
                count;
      const delta = curvature * ds,
        mid = heading_rad + delta / 2;
      const distance =
        Math.abs(delta) < 1e-8 ? ds : (2 * Math.sin(delta / 2)) / curvature;
      x += Math.cos(mid) * distance;
      y += Math.sin(mid) * distance;
      heading_rad += delta;
      points.push({ x, y, z, heading_rad });
    }
  }
  return points;
}
export function roadPoint(road: ObjectRoad, s: number, t = 0): CityPose {
  const points = sampleRoad(road, 0.5);
  let traveled = 0;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1],
      b = points[i],
      d = Math.hypot(b.x - a.x, b.y - a.y);
    if (traveled + d >= s) {
      const f = Math.max(0, Math.min(1, (s - traveled) / d)),
        h = a.heading_rad + (b.heading_rad - a.heading_rad) * f;
      return {
        x: a.x + (b.x - a.x) * f - Math.sin(h) * t,
        y: a.y + (b.y - a.y) * f + Math.cos(h) * t,
        z: a.z,
        heading_rad: h,
      };
    }
    traveled += d;
  }
  const p = points.at(-1)!;
  return {
    ...p,
    x: p.x - Math.sin(p.heading_rad) * t,
    y: p.y + Math.cos(p.heading_rad) * t,
  };
}
export function previewObjectMap(source: ObjectMapSource): CityMap {
  const lanes = source.roads.flatMap((road) => {
    const points = sampleRoad(road);
    return road.lanes.map((lane) => {
      const side = Math.sign(lane.id),
        inner = road.lanes
          .filter(
            (l) =>
              Math.sign(l.id) === side && Math.abs(l.id) < Math.abs(lane.id),
          )
          .reduce((sum, l) => sum + l.width_m, 0),
        offset = -side * (inner + lane.width_m / 2);
      const path = points.map((p) => ({
        ...p,
        x: p.x - Math.sin(p.heading_rad) * offset,
        y: p.y + Math.cos(p.heading_rad) * offset,
        heading_rad: p.heading_rad + (side > 0 ? Math.PI : 0),
      }));
      if (side > 0) path.reverse();
      return {
        id: `${road.id}:${lane.id}`,
        width_m: lane.width_m,
        points: path,
      };
    });
  });
  return {
    name: source.name,
    lanes,
    signals: source.signals.flatMap((signal) => {
      const road = source.roads.find((r) => r.id === signal.road_id);
      return road
        ? [
            {
              id: signal.id,
              kind: signal.kind,
              pose: roadPoint(road, signal.s_m, signal.t_m),
            },
          ]
        : [];
    }),
  };
}
export function deleteRoad(
  source: ObjectMapSource,
  id: string,
): ObjectMapSource {
  return {
    ...source,
    roads: source.roads.filter((r) => r.id !== id),
    connections: source.connections.filter(
      (c) => c.from_road !== id && c.to_road !== id,
    ),
    signals: source.signals.filter((s) => s.road_id !== id),
    objects: source.objects.filter((o) => o.road_id !== id),
  };
}
export function roadId(source: ObjectMapSource): string {
  let n = 1;
  while (source.roads.some((r) => r.id === String(n))) n++;
  return String(n);
}
export function moveHistory<T>(
  history: { past: T[]; present: T; future: T[] },
  direction: "undo" | "redo",
): { past: T[]; present: T; future: T[] } {
  if (direction === "undo" && history.past.length)
    return {
      past: history.past.slice(0, -1),
      present: history.past.at(-1)!,
      future: [history.present, ...history.future],
    };
  if (direction === "redo" && history.future.length)
    return {
      past: [...history.past, history.present],
      present: history.future[0],
      future: history.future.slice(1),
    };
  return history;
}
/** Authored junction template: explicit connector roads, never an engine run or measured map. */
export function appendJunction(
  source: ObjectMapSource,
  kind: "cross" | "tee",
): ObjectMapSource {
  const next = structuredClone(source),
    offset = source.roads.length
      ? Math.max(
          ...source.roads.flatMap((r) => sampleRoad(r).map((p) => p.x)),
        ) + 100
      : 0;
  let n = 1;
  while (next.junctions.some((j) => j.id === `junction-${n}`)) n++;
  const junction = `junction-${n}`;
  next.junctions.push({
    id: junction,
    name: kind === "cross" ? "십자 교차로" : "T자 교차로",
  });
  const headings =
    kind === "cross"
      ? [0, Math.PI / 2, Math.PI, -Math.PI / 2]
      : [0, Math.PI, -Math.PI / 2];
  const approaches = headings.map((heading, i) => {
    const id = roadId(next),
      road = newRoad(id, {
        x: offset - Math.cos(heading) * 60,
        y: -Math.sin(heading) * 60,
        z: 0,
        heading_rad: heading,
      });
    road.name = `${junction} 진입 ${i + 1}`;
    road.geometries[0].length_m = 48;
    next.roads.push(road);
    return road;
  });
  for (const from of approaches)
    for (const to of approaches) {
      if (from.id === to.id) continue;
      const id = roadId(next),
        start = sampleRoad(from).at(-1)!,
        diff = Math.atan2(
          Math.sin(to.origin.heading_rad + Math.PI - start.heading_rad),
          Math.cos(to.origin.heading_rad + Math.PI - start.heading_rad),
        );
      const connector = newRoad(id, start);
      connector.name = `연결 ${from.id} → ${to.id}`;
      connector.junction_id = junction;
      connector.lanes = [
        {
          id: -1,
          width_m: 3.5,
          speed_limit_mps: 6,
          lane_change: "none",
          closed: false,
        },
      ];
      connector.geometries =
        Math.abs(diff) < 0.01
          ? [
              {
                kind: "line",
                length_m: 24,
                curvature_start: 0,
                curvature_end: 0,
              },
            ]
          : [
              {
                kind: "arc",
                length_m: 6 * Math.PI,
                curvature_start: Math.sign(diff) / 12,
                curvature_end: Math.sign(diff) / 12,
              },
            ];
      next.roads.push(connector);
      next.connections.push(
        { from_road: from.id, from_lane: -1, to_road: id, to_lane: -1 },
        { from_road: id, from_lane: -1, to_road: to.id, to_lane: 1 },
      );
    }
  return next;
}
