import { mountPoint, sensorLabels } from "./layout-contract";
import type { LayoutRig, LayoutSensor, LayoutSensorKey, Triple } from "./layout-contract";

const colors = { front: "#36cfc2", rear: "#80a9ff", lidar: "#d6a9ff", radar: "#ffbe6f" };
function finiteSensor(sensor: LayoutSensor) { return [...sensor.translation_m, ...sensor.rotation_deg].every(Number.isFinite); }
export default function LayoutDiagram({ rig, selected, onSelect }: { rig: LayoutRig; selected: LayoutSensorKey; onSelect: (key: LayoutSensorKey) => void }) {
  const valid = rig.sensors.filter(finiteSensor), extent = Math.max(4.5, ...valid.flatMap(sensor => sensor.translation_m.map(Math.abs))) + 1.5;
  const scale = 105 / extent;
  function panel(view: "top" | "side") {
    const project = ([x, y, z]: Triple) => view === "top" ? [145 - y * scale, 130 - x * scale] : [145 + x * scale, 170 - z * scale];
    const polygon = (points: Triple[]) => points.map(point => project(point).join(",")).join(" ");
    const body: Triple[] = view === "top" ? [[2.3, .95, 0], [2.3, -.95, 0], [-2.3, -.95, 0], [-2.3, .95, 0]] : [[2.3, 0, 0], [1.9, 0, 1.25], [.7, 0, 1.55], [-1.3, 0, 1.55], [-2.3, 0, .8], [-2.3, 0, 0]];
    return <svg key={view} viewBox="0 0 290 255" role="img" aria-label={view === "top" ? "센서 배치 위에서 보기 +X 전방 +Y 왼쪽" : "센서 배치 옆에서 보기 +X 전방 +Z 위"}>
      <defs>{valid.map(sensor => <marker id={`layout-${view}-${sensor.key}`} key={sensor.key} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6 Z" fill={colors[sensor.key]}/></marker>)}</defs>
      <text x="14" y="21" className="layout-diagram-title">{view === "top" ? "TOP · 위에서 보기" : "SIDE · 옆에서 보기"}</text>
      <path d={view === "top" ? "M145 36V229 M35 130H255" : "M145 36V229 M35 170H255"} stroke="#293b49" strokeDasharray="3 5"/>
      <polygon points={polygon(body)} fill="#23313f" stroke="#627386" strokeWidth="1.5"/>
      {valid.map(sensor => {
        const origin = project(sensor.translation_m), end = project(mountPoint(sensor, [1.7, 0, 0]));
        const h = Number(sensor.parameters.fov ?? sensor.parameters.horizontal_fov ?? 90) / 2 * Math.PI / 180;
        const v = sensor.kind === "rgb" ? Math.atan(Math.tan(h) * 360 / 640) : Number(sensor.parameters.vertical_fov ?? 40) / 2 * Math.PI / 180;
        const corners = [[1.4, Math.tan(h) * 1.4, Math.tan(v) * 1.4], [1.4, -Math.tan(h) * 1.4, Math.tan(v) * 1.4], [1.4, -Math.tan(h) * 1.4, -Math.tan(v) * 1.4], [1.4, Math.tan(h) * 1.4, -Math.tan(v) * 1.4]] as Triple[];
        return <g key={sensor.key} opacity={selected === sensor.key ? 1 : .3} onClick={() => onSelect(sensor.key)} style={{ cursor: "pointer" }}>
          {sensor.kind === "lidar" ? <>{view === "top" ? <ellipse cx={origin[0]} cy={origin[1]} rx={1.6 * scale} ry={1.6 * scale} fill="none" stroke={colors[sensor.key]} strokeDasharray="3 4"/> : <polygon points={polygon([sensor.translation_m, mountPoint(sensor, [1.6, 0, Math.tan(Math.PI / 18) * 1.6]), mountPoint(sensor, [1.6, 0, Math.tan(-Math.PI / 6) * 1.6])])} fill={colors[sensor.key]} fillOpacity=".1" stroke={colors[sensor.key]} strokeWidth=".7"/>}</> : corners.map((corner, index) => <polygon key={index} points={polygon([sensor.translation_m, mountPoint(sensor, corner), mountPoint(sensor, corners[(index + 1) % corners.length])])} fill={colors[sensor.key]} fillOpacity=".04" stroke={colors[sensor.key]} strokeWidth=".5"/>)}
          <path d={`M${origin.join(" ")} L${end.join(" ")}`} stroke={colors[sensor.key]} strokeWidth="2" markerEnd={`url(#layout-${view}-${sensor.key})`}/><circle cx={origin[0]} cy={origin[1]} r={selected === sensor.key ? 5 : 3} fill={colors[sensor.key]}/>
          {selected === sensor.key && <text x={origin[0] + 9} y={origin[1] - 8} fill={colors[sensor.key]}>{sensorLabels[sensor.key]}</text>}
        </g>;
      })}
      <text x="15" y="240" fill="#a9b8c6">{view === "top" ? "↑ +X 전방   ← +Y 왼쪽" : "→ +X 전방   ↑ +Z 위"}</text>
    </svg>;
  }
  return <div className="layout-diagram"><div>{panel("top")}{panel("side")}</div><p>좌표: 오른손계 · 화살표: 센서 +X 방향 · 도식의 부채꼴은 방향 설명용이며 실제 가시 범위·가림·감지 성능을 뜻하지 않습니다.</p></div>;
}
