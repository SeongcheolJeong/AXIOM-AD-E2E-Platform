import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { Line, OrbitControls } from "@react-three/drei";
import { BufferGeometry, DoubleSide, Float32BufferAttribute } from "three";
import { cityBounds, cityToThree, laneRibbonData } from "./city-geometry";
import { previewObjectMap, roadPoint, sampleRoad } from "./object-map-contract";
import type { ObjectMapSource } from "./object-map-contract";
function RoadMesh({ source }: { source: ObjectMapSource }) {
  const map = useMemo(() => previewObjectMap(source), [source]);
  const data = useMemo(() => laneRibbonData(map.lanes), [map]);
  const geometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute("position", new Float32BufferAttribute(data.positions, 3));
    g.setIndex(data.indices);
    g.computeVertexNormals();
    return g;
  }, [data]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <>
      <mesh geometry={geometry}>
        <meshStandardMaterial color="#536365" side={DoubleSide} />
      </mesh>
      {map.lanes
        .filter((l) => l.points.length > 1)
        .map((l) => (
          <Line
            key={l.id}
            points={l.points.map((p) => cityToThree(p, 0.035))}
            color="#d6cf9e"
            lineWidth={1}
            dashed
            dashSize={2}
            gapSize={2}
          />
        ))}
      {source.objects.map((o) => {
        const road = source.roads.find((r) => r.id === o.road_id);
        if (!road) return null;
        const p = roadPoint(road, o.s_m, o.t_m);
        return (
          <mesh
            key={o.id}
            position={cityToThree(p, o.height_m / 2)}
            rotation={[0, -p.heading_rad, 0]}
          >
            <boxGeometry args={[o.length_m, o.height_m, o.width_m]} />
            <meshStandardMaterial color="#d99562" />
          </mesh>
        );
      })}
      {map.signals?.map((s) => (
        <group key={s.id} position={cityToThree(s.pose)}>
          <mesh position={[0, 1.5, 0]}>
            <cylinderGeometry args={[0.08, 0.08, 3, 8]} />
            <meshStandardMaterial color="#344b50" />
          </mesh>
          <mesh position={[0, 3, 0]}>
            <boxGeometry args={[0.6, 0.9, 0.25]} />
            <meshStandardMaterial
              color={s.kind === "traffic_light" ? "#c6a843" : "#bd5a52"}
            />
          </mesh>
        </group>
      ))}
    </>
  );
}
export default function ObjectMapStage({
  source,
  selected,
  onSelect,
  onPlace,
  mode,
}: {
  source: ObjectMapSource;
  selected: string;
  onSelect: (id: string) => void;
  onPlace?: (point: { x: number; y: number }) => void;
  mode: "2d" | "3d";
}) {
  const map = useMemo(() => previewObjectMap(source), [source]),
    points = map.lanes.flatMap((l) => l.points),
    bounds = cityBounds(points),
    size = Math.max(50, bounds.xmax - bounds.xmin, bounds.ymax - bounds.ymin),
    center = {
      x: (bounds.xmin + bounds.xmax) / 2,
      y: (bounds.ymin + bounds.ymax) / 2,
    };
  const svg = useRef<SVGSVGElement>(null),
    [zoom, setZoom] = useState(1),
    [pan, setPan] = useState({ x: 0, y: 0 }),
    drag = useRef<{ x: number; y: number; didMove: boolean } | null>(null);
  const extent = size / zoom;
  const world = (x: number, y: number) => {
    const m = svg.current?.getScreenCTM();
    if (!m) return null;
    const p = new DOMPoint(x, y).matrixTransform(m.inverse());
    return { x: p.x, y: p.y };
  };
  if (mode === "3d")
    return (
      <div className="object-map-stage">
        <Canvas
          camera={{
            position: [
              center.x + size * 0.7,
              size * 0.8,
              center.y + size * 0.8,
            ],
            fov: 45,
          }}
        >
          <ambientLight intensity={1.8} />
          <directionalLight position={[30, 80, 20]} intensity={2} />
          <RoadMesh source={source} />
          <gridHelper
            args={[size * 3, 60, "#739694", "#c8d6d1"]}
            position={[center.x, -0.08, center.y]}
          />
          <OrbitControls target={[center.x, 0, center.y]} makeDefault />
        </Canvas>
        <span className="object-map-overlay">
          기본 3D 편집 미리보기 · CARLA 실행 영상은 결과에서 확인
        </span>
      </div>
    );
  return (
    <div className={`object-map-stage ${onPlace ? "is-picking" : ""}`}>
      <svg
        ref={svg}
        aria-label="Editable road network"
        role="img"
        viewBox={`${center.x + pan.x - extent * 0.75} ${center.y + pan.y - extent / 2} ${extent * 1.5} ${extent}`}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY, didMove: false };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const start = drag.current;
          if (!start) return;
          if (
            Math.hypot(e.clientX - start.x, e.clientY - start.y) < 4 &&
            !start.didMove
          )
            return;
          const a = world(start.x, start.y),
            b = world(e.clientX, e.clientY);
          if (a && b)
            setPan((p) => ({ x: p.x + a.x - b.x, y: p.y + a.y - b.y }));
          drag.current = { x: e.clientX, y: e.clientY, didMove: true };
        }}
        onPointerUp={(e) => {
          const start = drag.current;
          drag.current = null;
          if (start && !start.didMove && onPlace) {
            const p = world(e.clientX, e.clientY);
            if (p) onPlace(p);
          }
        }}
        onPointerCancel={() => (drag.current = null)}
      >
        <defs>
          <pattern
            id="object-map-grid"
            width={5}
            height={5}
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 5 0 L 0 0 0 5"
              fill="none"
              stroke="#d7e2dd"
              strokeWidth={0.07}
            />
          </pattern>
        </defs>
        <rect
          x={center.x + pan.x - extent * 2}
          y={center.y + pan.y - extent * 2}
          width={extent * 4}
          height={extent * 4}
          fill="url(#object-map-grid)"
        />
        {source.roads.map((road) => (
          <g
            key={road.id}
            onPointerDown={(e) => {
              if (onPlace) return;
              e.stopPropagation();
              onSelect(road.id);
            }}
            style={{ cursor: "pointer" }}
          >
            {map.lanes
              .filter((l) => l.id.startsWith(`${road.id}:`))
              .map((l) => (
                <g key={l.id}>
                  <polyline
                    points={l.points.map((p) => `${p.x},${p.y}`).join(" ")}
                    fill="none"
                    stroke={selected === road.id ? "#436e70" : "#647775"}
                    strokeWidth={l.width_m}
                  />
                  <polyline
                    points={l.points.map((p) => `${p.x},${p.y}`).join(" ")}
                    fill="none"
                    stroke="#d9d3ac"
                    strokeWidth={0.12}
                    strokeDasharray="2 2"
                  />
                  {l.points[Math.floor(l.points.length * 0.55)] && (
                    <text
                      x={l.points[Math.floor(l.points.length * 0.55)].x}
                      y={l.points[Math.floor(l.points.length * 0.55)].y}
                      fontSize={1.6}
                      fill="white"
                    >
                      {Number(l.id.split(":").at(-1)) < 0 ? "→" : "←"}
                    </text>
                  )}
                </g>
              ))}
            <polyline
              points={sampleRoad(road)
                .map((p) => `${p.x},${p.y}`)
                .join(" ")}
              fill="none"
              stroke={selected === road.id ? "#f0c684" : "#f6e8a1"}
              strokeWidth={0.18}
            />
            <circle
              cx={road.origin.x}
              cy={road.origin.y}
              r={selected === road.id ? 0.9 : 0.5}
              fill="#21756b"
            />
            <text
              x={road.origin.x}
              y={road.origin.y - 3}
              fontSize={Math.max(1.5, extent / 65)}
              fill="#203e3b"
            >
              {road.name}
            </text>
          </g>
        ))}
        {source.connections.map((c, i) => {
          const a = map.lanes
              .find((l) => l.id === `${c.from_road}:${c.from_lane}`)
              ?.points.at(-1),
            b = map.lanes.find((l) => l.id === `${c.to_road}:${c.to_lane}`)
              ?.points[0];
          return a && b ? (
            <line
              key={i}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke="#dc9957"
              strokeWidth={0.6}
              strokeDasharray="1 1"
            />
          ) : null;
        })}
        {map.signals?.map((s) => (
          <g key={s.id}>
            <circle
              cx={s.pose.x}
              cy={s.pose.y}
              r={0.9}
              fill={s.kind === "traffic_light" ? "#d9b34f" : "#bc665f"}
            />
            <text x={s.pose.x + 1.5} y={s.pose.y} fontSize={1.5} fill="#62392c">
              {s.kind}
            </text>
          </g>
        ))}
        {source.objects.map((o) => {
          const road = source.roads.find((r) => r.id === o.road_id);
          if (!road) return null;
          const p = roadPoint(road, o.s_m, o.t_m);
          return (
            <rect
              key={o.id}
              x={-o.length_m / 2}
              y={-o.width_m / 2}
              width={o.length_m}
              height={o.width_m}
              transform={`translate(${p.x} ${p.y}) rotate(${(p.heading_rad * 180) / Math.PI})`}
              fill="#d99562"
            />
          );
        })}
      </svg>
      <span className="object-map-overlay">
        {onPlace
          ? "원점을 배치할 위치를 클릭 · 1m 스냅"
          : "도로를 클릭해 선택 · 드래그하여 이동"}{" "}
        · X/Y metres
      </span>
      <div className="city-map-controls">
        <button
          onClick={() => {
            setZoom(1);
            setPan({ x: 0, y: 0 });
          }}
        >
          전체 보기
        </button>
        <button
          aria-label="Zoom in editable map"
          onClick={() => setZoom((z) => Math.min(15, z * 1.4))}
        >
          ＋
        </button>
        <button
          aria-label="Zoom out editable map"
          onClick={() => setZoom((z) => Math.max(0.2, z / 1.4))}
        >
          −
        </button>
      </div>
    </div>
  );
}
