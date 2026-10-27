import { useEffect, useId, useState } from "react";
import { authHeaders } from "../../api";
import type { RoadActor, RoadFrame } from "./log-sim-contract";

/** Original keyframes only. No interpolated images, inferred lanes, or detections. */
export default function RoadLogViewer({ frames, timeS, onReady }: { frames: RoadFrame[]; timeS: number; onReady: (ready: boolean) => void }) {
  const [images, setImages] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const gridId = useId().replaceAll(":", "");
  const frame = frames.filter(item => item.time_s <= timeS + 0.000001).at(-1) ?? frames[0];
  const urls = [...new Set(frames.map(item => item.image_url).filter((url): url is string => !!url))];
  const signature = urls.join("\n");
  useEffect(() => {
    const controller = new AbortController();
    const retained: string[] = [];
    setImages({}); setErrors({});
    void Promise.all(urls.map(async url => {
      try {
        const response = await fetch(url, { headers: authHeaders(), signal: controller.signal });
        if (!response.ok) throw new Error(`원본 이미지 조회 실패 (${response.status})`);
        const bytes = await response.arrayBuffer();
        const expectedHash = response.headers.get("X-Content-SHA256");
        if (expectedHash) {
          const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map(value => value.toString(16).padStart(2, "0")).join("");
          if (hash !== expectedHash) throw new Error("원본 이미지 SHA-256이 보존된 기록과 다릅니다.");
        }
        const blob = new Blob([bytes], { type: response.headers.get("Content-Type") ?? "image/jpeg" });
        if (controller.signal.aborted) return;
        const source = URL.createObjectURL(blob); retained.push(source);
        setImages(previous => ({ ...previous, [url]: source }));
      } catch (reason) {
        if (!controller.signal.aborted) setErrors(previous => ({ ...previous, [url]: reason instanceof Error ? reason.message : String(reason) }));
      }
    }));
    return () => { controller.abort(); retained.forEach(url => URL.revokeObjectURL(url)); };
  }, [signature]);
  const image = frame?.image_url ? images[frame.image_url] : undefined;
  useEffect(() => { onReady(!!frame && (!frame.image_url || !!image)); }, [frame, image, onReady]);
  if (!frame) return <p className="log-sim-notice">이 소스에 준비된 원본 프레임이 없습니다.</p>;
  const ego = frame.ego;
  const point = (actor: RoadActor) => {
    const dx = actor.x - ego.x, dy = actor.y - ego.y;
    return { x: 160 - (-dx * Math.sin(ego.yaw_rad) + dy * Math.cos(ego.yaw_rad)) * 5, y: 235 - (dx * Math.cos(ego.yaw_rad) + dy * Math.sin(ego.yaw_rad)) * 5 };
  };
  return <div className={`log-road-viewer ${urls.length ? "" : "log-road-no-camera"}`}>
    {urls.length > 0 && <figure className="log-road-camera">
      <div className="log-visual-label"><strong>기록 원본 · 전방 카메라</strong><span>원본 보존 영상</span></div>
      <div className="log-road-image">
        {image ? <img src={image} alt={`기록 원본 전방 영상 ${frame.image_timestamp_ns ?? "시각 미보존"} ns`} /> : <p role={frame.image_url && errors[frame.image_url] ? "alert" : "status"}>{frame.image_url ? errors[frame.image_url] ?? "원본 이미지를 불러오는 중…" : "해당 시각의 전방 이미지가 준비되지 않았습니다."}</p>}
      </div>
      <figcaption>자차 표본 {frame.time_s.toFixed(2)}s · 카메라 원본 {frame.image_timestamp_ns ?? "미보존"} ns<br />{frame.image_age_ns !== undefined ? `자차 표본 대비 카메라 시차 ${(Number(frame.image_age_ns) / 1e6).toFixed(2)} ms · ` : ""}저장된 키프레임 간에는 마지막 원본 유지</figcaption>
    </figure>}
    {!urls.length && <p className="log-sim-notice">영상 없는 로그 · 기록된 객체와 자차 신호로 조사합니다.</p>}
    <div className="log-road-bev">
      <div className="log-visual-label"><strong>객체 BEV</strong><span>기록된 객체 입력</span></div>
      <svg viewBox="0 0 320 310" role="img" aria-label="자차 기준 기록된 객체 지도">
        <defs><pattern id={gridId} width="50" height="50" patternUnits="userSpaceOnUse"><path d="M50 0H0V50" fill="none" stroke="#26443d" strokeWidth="1" /></pattern></defs>
        <rect width="320" height="310" fill="#102b25" /><rect width="320" height="310" fill={`url(#${gridId})`} />
        <path d="M160 8V295" stroke="#5b8575" strokeDasharray="4 6" />
        {frame.objects.map((actor, index) => { const p = point(actor); return <g key={actor.id ?? index} transform={`translate(${p.x} ${p.y}) rotate(${-(actor.yaw_rad - ego.yaw_rad) * 180 / Math.PI})`}><rect x={-actor.width_m * 2.5} y={-actor.length_m * 2.5} width={Math.max(2, actor.width_m * 5)} height={Math.max(2, actor.length_m * 5)} rx="1" fill="#e4b877" fillOpacity=".65" stroke="#ffdb9f" /><title>{actor.category ?? "객체"} · {actor.id}</title></g>; })}
        <rect x={160 - ego.width_m * 2.5} y={235 - ego.length_m * 2.5} width={ego.width_m * 5} height={ego.length_m * 5} rx="2" fill="#8de0bf" /><path d="M156 233L160 227L164 233" fill="none" stroke="#153d2c" strokeWidth="1.5" />
        <text x="12" y="291" fill="#aec9bf" fontSize="10">격자 10 m · 지도 차선 미표시</text>
      </svg>
      <div className="log-road-stats"><span>자차 속도<strong>{typeof ego.speed_mps === "number" ? `${(ego.speed_mps * 3.6).toFixed(1)} km/h` : "미보존"}</strong></span><span>기록 객체<strong>{frame.objects.length}개</strong></span></div>
    </div>
  </div>;
}
