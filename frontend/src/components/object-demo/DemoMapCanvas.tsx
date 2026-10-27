import { useRef, useState } from "react";
import type { CityActor, CityMap, CityPose } from "../city-sim/city-contract";
import { actorColor } from "../../scenario-authoring";
import { snapDemoPose } from "./demo-contract";

export default function DemoMapCanvas({
  map,
  actors,
  selected,
  disabled,
  onSelect,
  onMove,
  onError,
  editMode = "start",
  onDestination,
}: {
  map: CityMap;
  actors: CityActor[];
  selected: string;
  disabled: boolean;
  onSelect: (id: string) => void;
  onMove: (id: string, pose: CityPose) => void;
  onError: (message: string) => void;
  editMode?: "start" | "destination";
  onDestination?: (id: string, pose: CityPose) => void;
}) {
  const svg = useRef<SVGSVGElement>(null),
    drag = useRef<{
      actorId: string;
      pointerId: number;
      origin: { x: number; y: number };
    } | null>(null);
  const [moving, setMoving] = useState<{ id: string; pose: CityPose } | null>(
      null,
    ),
    [zoom, setZoom] = useState(1);
  const [bounds] = useState(() => {
    const points = actors.flatMap((actor) => [
      actor.pose,
      ...(actor.route ?? []),
      ...(actor.destination ? [actor.destination] : []),
    ]);
    if (!points.length)
      points.push(...map.lanes.flatMap((lane) => lane.points));
    const xs = points.map((point) => point.x),
      ys = points.map((point) => point.y);
    const xmin = Math.min(...xs, 0),
      xmax = Math.max(...xs, 1),
      ymin = Math.min(...ys, 0),
      ymax = Math.max(...ys, 1);
    // Fit the relevant corridor, rather than the entire registered city.
    const actualXmin = points.length ? Math.min(...xs) : xmin,
      actualXmax = points.length ? Math.max(...xs) : xmax;
    const actualYmin = points.length ? Math.min(...ys) : ymin,
      actualYmax = points.length ? Math.max(...ys) : ymax;
    return {
      x: (actualXmin + actualXmax) / 2,
      y: (actualYmin + actualYmax) / 2,
      size: Math.max(
        (actualXmax - actualXmin + 30) / 1.7,
        actualYmax - actualYmin + 30,
        40,
      ),
    };
    // This component is keyed by preset/binding; keep its corridor camera fixed during edits.
  });
  const size = bounds.size / zoom;
  function world(event: { clientX: number; clientY: number }) {
    const matrix = svg.current?.getScreenCTM();
    return matrix
      ? new DOMPoint(event.clientX, event.clientY).matrixTransform(
          matrix.inverse(),
        )
      : null;
  }
  function end(event: React.PointerEvent<SVGSVGElement>) {
    const current = drag.current;
    drag.current = null;
    setMoving(null);
    if (!current || current.pointerId !== event.pointerId || disabled) return;
    if (
      Math.hypot(
        event.clientX - current.origin.x,
        event.clientY - current.origin.y,
      ) < 4
    )
      return;
    const point = world(event),
      pose = point ? snapDemoPose(map, point) : null;
    if (!pose) {
      onError("차로에서 6m 이내로 이동해 주세요. 시작 위치는 유지했습니다.");
      return;
    }
    onMove(current.actorId, pose);
  }
  const line = (points: { x: number; y: number }[]) =>
    points.map((p) => `${p.x},${p.y}`).join(" ");
  return (
    <div className="demo-map">
      <svg
        ref={svg}
        viewBox={`${bounds.x - size * 0.85} ${bounds.y - size / 2} ${size * 1.7} ${size}`}
        aria-label={
          editMode === "destination"
            ? "작성 지도 · 차로를 클릭하여 목적지 선택"
            : "데모 지도 · 객체를 드래그하여 시작 위치 편집"
        }
        role="img"
        onClick={(event) => {
          if (disabled || editMode !== "destination" || !onDestination) return;
          const point = world(event),
            pose = point ? snapDemoPose(map, point) : null;
          if (pose) onDestination(selected, pose);
          else
            onError(
              "차로에서 6m 이내의 목적지를 선택해 주세요. 목적지는 유지했습니다.",
            );
        }}
        onPointerMove={(event) => {
          if (!drag.current || disabled) return;
          const point = world(event),
            pose = point ? snapDemoPose(map, point) : null;
          if (pose) setMoving({ id: drag.current.actorId, pose });
        }}
        onPointerUp={end}
        onPointerCancel={() => {
          drag.current = null;
          setMoving(null);
        }}
      >
        <defs>
          <pattern
            id="demo-grid"
            width="10"
            height="10"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M10 0H0V10"
              fill="none"
              stroke="#fff"
              strokeOpacity=".035"
              strokeWidth=".18"
            />
          </pattern>
        </defs>
        <rect
          x={bounds.x - size * 2}
          y={bounds.y - size * 2}
          width={size * 4}
          height={size * 4}
          fill="#172b32"
        />
        <rect
          x={bounds.x - size * 2}
          y={bounds.y - size * 2}
          width={size * 4}
          height={size * 4}
          fill="url(#demo-grid)"
        />
        {map.lanes.map((lane) => (
          <g key={lane.id}>
            <polyline
              points={line(lane.points)}
              fill="none"
              stroke="#43555c"
              strokeWidth={(lane.width_m ?? 3.5) + 0.2}
              strokeLinejoin="round"
            />
            <polyline
              points={line(lane.points)}
              fill="none"
              stroke="#91a4a8"
              strokeOpacity=".45"
              strokeWidth=".12"
              strokeDasharray="2 2"
            />
          </g>
        ))}
        {actors.map((actor) => (
          <g
            key={`route-${actor.id}`}
            opacity={selected === actor.id ? 0.8 : 0.16}
          >
            <polyline
              points={line(actor.route)}
              fill="none"
              stroke={actorColor(actor.id)}
              strokeWidth=".38"
              strokeDasharray="2 1.5"
            />
            {actor.destination && (
              <circle
                cx={actor.destination.x}
                cy={actor.destination.y}
                r="1.5"
                fill="none"
                stroke={actorColor(actor.id)}
                strokeWidth=".35"
              />
            )}
          </g>
        ))}
        {actors.map((actor) => {
          const pose = moving?.id === actor.id ? moving.pose : actor.pose,
            active = actor.id === selected;
          return (
            <g
              key={actor.id}
              role="button"
              tabIndex={0}
              aria-label={`객체 ${actor.id} 선택`}
              className={`demo-map-actor ${disabled ? "" : "draggable"}`}
              transform={`translate(${pose.x} ${pose.y})`}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ")
                  onSelect(actor.id);
              }}
              onPointerDown={(event) => {
                onSelect(actor.id);
                if (disabled || editMode === "destination") return;
                event.preventDefault();
                event.stopPropagation();
                drag.current = {
                  actorId: actor.id,
                  pointerId: event.pointerId,
                  origin: { x: event.clientX, y: event.clientY },
                };
                svg.current?.setPointerCapture(event.pointerId);
              }}
            >
              {active && (
                <circle
                  r="4"
                  fill="#8ce7cf"
                  fillOpacity=".12"
                  stroke="#8ce7cf"
                  strokeOpacity=".4"
                  strokeWidth=".15"
                />
              )}
              <g transform={`rotate(${(pose.heading_rad * 180) / Math.PI})`}>
                <rect
                  x={actor.kind === "static" ? -1 : -2.25}
                  y={actor.kind === "static" ? -1 : -0.9}
                  width={actor.kind === "static" ? 2 : 4.5}
                  height={actor.kind === "static" ? 2 : 1.8}
                  rx=".35"
                  fill={actorColor(actor.id)}
                  stroke={active ? "#e3fff8" : "#15242d"}
                  strokeWidth=".23"
                />
                {actor.kind !== "static" && (
                  <path
                    d="M.4 -.5 L1.2 0 L.4 .5"
                    fill="none"
                    stroke="#fff"
                    strokeWidth=".25"
                  />
                )}
              </g>
              <text
                y="-3.5"
                textAnchor="middle"
                fill="#e9f8f3"
                fontSize={Math.max(1.5, size / 100)}
                paintOrder="stroke"
                stroke="#172b32"
                strokeWidth=".5"
              >
                {actor.id === "ego" ? "EGO" : actor.id}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="demo-map-meta">
        <span>SCENARIO DESIGN</span>
        <strong>{map.name ?? map.native_name ?? "등록 지도"}</strong>
        <small>작성 지도 · 시작 위치 / 경로</small>
      </div>
      <div className="demo-map-controls">
        <button
          type="button"
          onClick={() => setZoom(1)}
          aria-label="데모 장면 전체 보기"
        >
          전체
        </button>
        <button
          type="button"
          onClick={() => setZoom((value) => Math.min(4, value * 1.4))}
          aria-label="데모 지도 확대"
        >
          ＋
        </button>
        <button
          type="button"
          onClick={() => setZoom((value) => Math.max(0.5, value / 1.4))}
          aria-label="데모 지도 축소"
        >
          −
        </button>
      </div>
      <div className="demo-map-footer">
        <span>
          <i />{" "}
          {editMode === "destination"
            ? "차로 클릭 · 선택 객체의 목적지 설정"
            : "객체 드래그 · 차로 자동 맞춤"}
        </span>
        <span>┄ 계획 경로 &nbsp; ○ 목적지</span>
      </div>
    </div>
  );
}
