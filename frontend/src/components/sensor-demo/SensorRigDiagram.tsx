import { useId } from "react";

export default function SensorRigDiagram({ presetId, value, baseline }: { presetId: string; value: number; baseline: number }) {
  const id = useId().replaceAll(":", "");
  const camera = ["camera_fov", "driving_camera_fov"].includes(presetId), lidar = presetId === "lidar_density";
  const fov = camera ? value : 90;
  const edge = Math.tan(fov * Math.PI / 360) * 122;
  const baselineEdge = Math.tan((camera ? baseline : 90) * Math.PI / 360) * 122;
  const radarRadius = presetId === "radar_range" ? 35 + value * .72 : 94;
  return <figure className="sensor-demo-rig">
    <svg viewBox="0 0 390 218" role="img" aria-label="센서 설정 배치 도식 · 실제 관측 전">
      <defs><pattern id={`rig-grid-${id}`} width="22" height="22" patternUnits="userSpaceOnUse"><path d="M22 0H0V22" fill="none" stroke="#274543" strokeWidth=".6" /></pattern><linearGradient id={`rig-fov-${id}`} x1="0" y1="1" x2="0" y2="0"><stop stopColor="#71ebc0" stopOpacity=".05"/><stop offset="1" stopColor="#71ebc0" stopOpacity=".28"/></linearGradient></defs>
      <rect width="390" height="218" rx="12" fill="#102b2d"/><rect width="390" height="218" fill={`url(#rig-grid-${id})`}/>
      <path d={`M195 149 L${195 - baselineEdge} 27 L${195 + baselineEdge} 27 Z`} fill="none" stroke="#89a4a7" strokeDasharray="4 4" opacity=".55"/>
      <path d={`M195 149 L${195 - edge} 27 L${195 + edge} 27 Z`} fill={`url(#rig-fov-${id})`} stroke="#73efc4" strokeWidth="1.6"/>
      {[35, 58, 81].map((r, index) => <circle key={r} cx="195" cy="163" r={r} fill="none" stroke={lidar ? "#94bfff" : "#587b81"} opacity={lidar ? .65 : .25} strokeDasharray={lidar ? `${Math.max(1, 6 - value / 8000)} ${Math.max(2, 10 - value / 4000)}` : "3 5"} strokeWidth={index === 0 ? "1.5" : "1"} />)}
      <path d={`M195 148 L${195 - radarRadius * .64} ${148 - radarRadius} Q195 ${148 - radarRadius * 1.14} ${195 + radarRadius * .64} ${148 - radarRadius} Z`} fill="none" stroke="#ffc27a" strokeDasharray="3 5" opacity=".75" />
      <rect x="181" y="145" width="28" height="54" rx="9" fill="#d4e4e2" stroke="#eafef5"/><rect x="185" y="155" width="20" height="15" rx="3" fill="#476461"/><rect x="185" y="184" width="20" height="8" rx="2" fill="#476461"/>
      <circle cx="195" cy="148" r="3" fill="#79ffd3"/><circle cx="195" cy="170" r="4" fill="#9ec5ff"/><path d="M195 119v-16m-4 4 4-4 4 4" stroke="#b7d9d3" fill="none"/>
      <text x="15" y="23" fill="#94b6b0" fontSize="9" letterSpacing="1.2">RIG B · TOP VIEW</text>
      <text x="376" y="202" textAnchor="end" fill="#b5cfc7" fontSize="10">{camera ? `${value}° FoV` : lidar ? `${value.toLocaleString()} points/s` : `${value} m range`}</text>
    </svg>
    <figcaption><span><i className="camera"/>Camera</span><span><i className="lidar"/>LiDAR</span><span><i className="radar"/>Radar</span><small>설정 도식 · 관측 결과는 실행 후 표시</small></figcaption>
  </figure>;
}
