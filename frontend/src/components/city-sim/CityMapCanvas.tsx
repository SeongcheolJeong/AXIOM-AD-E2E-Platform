import { useEffect, useMemo, useRef, useState } from "react";
import { actorColor } from "../../scenario-authoring";
import type { CityActor, CityMap, CityPose } from "./city-contract";
type Point = Pick<CityPose, "x" | "y">;
export type MapActor = Pick<
  CityActor,
  "id" | "kind" | "role" | "pose" | "route"
> & {
  destination?: CityPose | null;
  dimensions?: { length_m: number; width_m: number };
  bbox_center?: { x: number; y: number; z: number };
};
export default function CityMapCanvas({
  map,
  actors,
  selected,
  onSelect,
  onPick,
  pickLabel,
  actual = {},
  planned = {},
  follow = false,
  relationships = [],
  initialFit = "map",
}: {
  map: CityMap;
  actors: MapActor[];
  selected: string;
  onSelect: (id: string) => void;
  onPick?: (point: Point) => void;
  pickLabel?: string;
  actual?: Record<string, Point[]>;
  planned?: Record<string, Point[]>;
  follow?: boolean;
  relationships?: { from: Point; to: Point; label: string }[];
  initialFit?: "map" | "actor";
}) {
  const svg = useRef<SVGSVGElement>(null),
    drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const extent = useMemo(() => {
    const points = map.lanes.flatMap((lane) => lane.points);
    let minX = Infinity,
      maxX = -Infinity,
      minY = Infinity,
      maxY = -Infinity;
    for (const point of points) {
      minX = Math.min(minX, point.x);
      maxX = Math.max(maxX, point.x);
      minY = Math.min(minY, point.y);
      maxY = Math.max(maxY, point.y);
    }
    if (!points.length) {
      minX = -40;
      maxX = 40;
      minY = -40;
      maxY = 40;
    }
    minX -= 10;
    minY -= 10;
    maxX += 10;
    maxY += 10;
    return {
      x: (minX + maxX) / 2,
      y: (minY + maxY) / 2,
      size: Math.max(maxX - minX, maxY - minY, 25),
    };
  }, [map]);
  const [view, setView] = useState(() => ({ ...extent, size: initialFit === "actor" ? 65 : extent.size }));
  useEffect(() => setView({ ...extent, size: initialFit === "actor" ? 65 : extent.size }), [extent, initialFit]);
  const target = actors.find((actor) => actor.id === selected);
  const center = follow && target ? target.pose : view;
  function world(x: number, y: number): Point | null {
    const node = svg.current,
      matrix = node?.getScreenCTM();
    if (!node || !matrix) return null;
    const p = new DOMPoint(x, y).matrixTransform(matrix.inverse());
    return { x: p.x, y: p.y };
  }
  function focus() {
    if (target) setView({ x: target.pose.x, y: target.pose.y, size: 65 });
  }
  const line = (points: Point[]) =>
    points.map((p) => `${p.x},${p.y}`).join(" ");
  return (
    <div className={`city-map-canvas ${onPick ? "is-picking" : ""}`}>
      <svg
        ref={svg}
        role="img"
        aria-label="CARLA city lane map and actors"
        viewBox={`${center.x - view.size * 0.75} ${center.y - view.size / 2} ${view.size * 1.5} ${view.size}`}
        onPointerDown={(event) => {
          drag.current = { x: event.clientX, y: event.clientY, moved: false };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start || follow) return;
          const delta = Math.hypot(
            event.clientX - start.x,
            event.clientY - start.y,
          );
          if (delta < 4 && !start.moved) return;
          const a = world(start.x, start.y),
            b = world(event.clientX, event.clientY);
          if (a && b)
            setView((v) => ({ ...v, x: v.x + a.x - b.x, y: v.y + a.y - b.y }));
          drag.current = { x: event.clientX, y: event.clientY, moved: true };
        }}
        onPointerUp={(event) => {
          const start = drag.current;
          drag.current = null;
          if (start && !start.moved && onPick) {
            const p = world(event.clientX, event.clientY);
            if (p) onPick(p);
          }
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        <rect
          x={center.x - view.size}
          y={center.y - view.size}
          width={view.size * 2}
          height={view.size * 2}
          fill="#edf1e9"
        />
        {map.lanes.map((lane) => (
          <g key={lane.id}>
            <polyline
              points={line(lane.points)}
              fill="none"
              stroke="#54605a"
              strokeWidth={lane.width_m ?? 3.5}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            <polyline
              points={line(lane.points)}
              fill="none"
              stroke="#bdc9bb"
              strokeWidth={0.1}
              strokeDasharray="2 3"
            />
          </g>
        ))}
        {actors.map((actor) => (
          <g key={`route-${actor.id}`}>
            <polyline
              points={line(actor.route)}
              fill="none"
              stroke={actorColor(actor.id)}
              strokeWidth={selected === actor.id ? 0.65 : 0.3}
              strokeDasharray="2 1.5"
              opacity={selected === actor.id ? 1 : 0.5}
            />
            {actor.destination && (
              <g
                transform={`translate(${actor.destination.x} ${actor.destination.y})`}
              >
                <path
                  d="M 0 -1.4 L 1.4 0 L 0 1.4 L -1.4 0 Z"
                  fill="#fff2bd"
                  stroke="#9b5ca2"
                  strokeWidth={0.3}
                />
                <text
                  x={2}
                  y={-1.5}
                  fontSize={Math.max(1.5, view.size / 100)}
                  fill="#743b7a"
                >
                  {actor.id} 목적지
                </text>
              </g>
            )}
            {actor.route.at(-1) && (
              <circle
                cx={actor.route.at(-1)!.x}
                cy={actor.route.at(-1)!.y}
                r={0.85}
                fill="none"
                stroke={actorColor(actor.id)}
                strokeWidth={0.3}
              />
            )}
          </g>
        ))}
        {Object.entries(actual).map(([id, points]) => (
          <polyline
            key={`actual-${id}`}
            points={line(points)}
            fill="none"
            stroke={actorColor(id)}
            strokeWidth={selected === id ? 0.65 : 0.3}
          />
        ))}
        {Object.entries(planned).map(([id, points]) => (
          <polyline
            key={`plan-${id}`}
            points={line(points)}
            fill="none"
            stroke="#b276e4"
            strokeWidth={0.45}
          />
        ))}
        {relationships.map((relation, index) => (
          <g key={`relation-${index}`}>
            <line
              x1={relation.from.x}
              y1={relation.from.y}
              x2={relation.to.x}
              y2={relation.to.y}
              stroke="#d17647"
              strokeWidth={0.3}
              strokeDasharray="1 .5"
            />
            <text
              x={(relation.from.x + relation.to.x) / 2}
              y={(relation.from.y + relation.to.y) / 2 - 1}
              fontSize={Math.max(1.4, view.size / 100)}
              textAnchor="middle"
              fill="#8d422a"
              stroke="#fff"
              strokeWidth={0.08}
              paintOrder="stroke"
            >
              {relation.label}
            </text>
          </g>
        ))}
        {actors.map((actor) => (
          <g
            key={actor.id}
            transform={`translate(${actor.pose.x} ${actor.pose.y}) rotate(${(actor.pose.heading_rad * 180) / Math.PI})`}
            onPointerDown={(event) => {
              if (onPick) return;
              event.stopPropagation();
              onSelect(actor.id);
            }}
            style={{ cursor: "pointer" }}
          >
            <rect
              x={
                (actor.bbox_center?.x ?? 0) -
                (actor.dimensions?.length_m ??
                  (actor.kind === "vehicle" ? 4.5 : 0.7)) /
                  2
              }
              y={
                (actor.bbox_center?.y ?? 0) -
                (actor.dimensions?.width_m ??
                  (actor.kind === "vehicle" ? 1.8 : 0.7)) /
                  2
              }
              width={
                actor.dimensions?.length_m ??
                (actor.kind === "vehicle" ? 4.5 : 0.7)
              }
              height={
                actor.dimensions?.width_m ??
                (actor.kind === "vehicle" ? 1.8 : 0.7)
              }
              rx={0.2}
              fill={actorColor(actor.id)}
              stroke={selected === actor.id ? "#ffffff" : "#26342d"}
              strokeWidth={selected === actor.id ? 0.35 : 0.15}
            />
            <path
              d="M 0 -0.4 L 1.3 0 L 0 0.4"
              fill="none"
              stroke="#fff"
              strokeWidth={0.2}
            />
            <text
              transform={`rotate(${(-actor.pose.heading_rad * 180) / Math.PI})`}
              y={-2.3}
              textAnchor="middle"
              fill="#182c23"
              fontSize={Math.max(1.8, view.size / 115)}
              stroke="#fff"
              strokeWidth={0.1}
              paintOrder="stroke"
            >
              {actor.id}
            </text>
          </g>
        ))}
      </svg>
      <div className="city-map-caption">
        <strong>{map.name ?? map.native_name ?? "Registered CARLA map"}</strong>
        <span>
          차로 도식 · CARLA X/Y/Z · m · 실제 영상은 실행 결과에서 확인
        </span>
        {pickLabel && <b>{pickLabel} · 차로를 클릭하세요</b>}
      </div>
      <div className="city-map-controls">
        <button type="button" onClick={() => setView(extent)}>
          지도 전체
        </button>
        <button type="button" disabled={!target} onClick={focus}>
          선택 객체
        </button>
        <button
          type="button"
          aria-label="Zoom in city map"
          onClick={() =>
            setView((v) => ({ ...v, size: Math.max(12, v.size / 1.6) }))
          }
        >
          ＋
        </button>
        <button
          type="button"
          aria-label="Zoom out city map"
          onClick={() =>
            setView((v) => ({
              ...v,
              size: Math.min(extent.size * 2, v.size * 1.6),
            }))
          }
        >
          −
        </button>
      </div>
      <div className="city-map-legend">
        <span>┄ 원하는 경로</span>
        <span>━ 실제 궤적</span>
        <span className="city-planned">━ planner 출력</span>
        <span>드래그: 지도 이동</span>
      </div>
    </div>
  );
}
