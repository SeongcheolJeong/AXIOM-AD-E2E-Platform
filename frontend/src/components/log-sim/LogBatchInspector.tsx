import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Pause, Play, X } from "lucide-react";
import { useResource } from "../../api";
import { ErrorState, Loading } from "../ui";
import RoadLogViewer from "./RoadLogViewer";
import type { LogSource, ReplaySample } from "./log-sim-contract";
import { batchKindLabel } from "./log-batch-contract";
import type { LogBatchCandidate, LogBatchComparison } from "./log-batch-contract";

export default function LogBatchInspector({ project, candidate, sceneName, selected, onToggle, onClose, onNavigate, canPrevious, canNext }: {
  project: string; candidate: LogBatchCandidate; sceneName: string; selected: boolean;
  onToggle: () => void; onClose: () => void; onNavigate: (delta: number) => void; canPrevious: boolean; canNext: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const source = useResource<LogSource>(`/log-sim/sources/${encodeURIComponent(candidate.source_id)}?project_id=${encodeURIComponent(project)}`);
  const comparison = useResource<LogBatchComparison>(`/log-sim/comparisons/${encodeURIComponent(candidate.comparison_id)}?project_id=${encodeURIComponent(project)}`);
  const [timeS, setTimeS] = useState(candidate.time_s), [playing, setPlaying] = useState(false), [ready, setReady] = useState(false);
  const readyChanged = useCallback((value: boolean) => setReady(value), []);
  const first = source.data?.road_frames?.[0]?.time_s ?? 0;
  const last = source.data?.road_frames?.at(-1)?.time_s ?? candidate.end_time_s;
  const samples = comparison.data?.result?.samples ?? [];
  const sample = samples.filter(value => value.time_s <= timeS + .000001).at(-1) ?? samples[0];
  const initialized = useRef("");
  useEffect(() => { dialog.current?.showModal(); return () => { dialog.current?.close(); }; }, []);
  useEffect(() => { setTimeS(candidate.time_s); setPlaying(false); }, [candidate.id]);
  useEffect(() => {
    if (!samples.length || initialized.current === candidate.id) return;
    const inWindow = samples.filter(value => value.index >= candidate.start_index && value.index <= candidate.end_index);
    const focus = inWindow.find(value => opposite(value)) ?? inWindow.find(value => value.baseline.decision !== value.candidate.decision) ?? inWindow[0];
    if (focus) setTimeS(focus.time_s);
    initialized.current = candidate.id;
  }, [samples, candidate]);
  useEffect(() => {
    if (!playing || !ready) return;
    if (timeS >= last) { setPlaying(false); return; }
    const timer = setTimeout(() => setTimeS(value => Math.min(last, value + .1)), 100);
    return () => clearTimeout(timer);
  }, [playing, ready, timeS, last]);
  const seek = (value: number) => { setPlaying(false); setTimeS(Math.min(last, Math.max(first, value))); };
  return <dialog ref={dialog} className="log-batch-inspector" aria-label="Log Sim candidate investigation" onCancel={event => { event.preventDefault(); onClose(); }}>
    <header><div><span className="log-sim-eyebrow">CANDIDATE {candidate.rank} / ORIGINAL + REQUESTS</span><h2>{sceneName}<span>{batchKindLabel(candidate.kind)}</span></h2><p>{candidate.time_s.toFixed(2)}–{candidate.end_time_s.toFixed(2)}s · 원본 {candidate.metrics.sample_count}개 표본</p></div><div><button className={`button ${selected ? "selected" : "secondary"}`} aria-label="Log Sim toggle inspected candidate" aria-pressed={selected} onClick={onToggle}>{selected ? <Check size={16} /> : null}{selected ? "묶음에 선택됨" : "Case 묶음에 선택"}</button><button className="log-batch-close" aria-label="Close Log Sim candidate" onClick={onClose}><X size={20} /></button></div></header>
    {(source.loading || comparison.loading) && <Loading label="원본 영상과 고정된 A/B 실행을 연결하는 중…" />}
    {source.error && <ErrorState message={source.error} retry={source.reload} />}
    {comparison.error && <ErrorState message={comparison.error} retry={comparison.reload} />}
    {source.data && comparison.data?.result?.status === "comparable" && <>
      <div className="log-batch-inspector-main"><div className="log-batch-original"><div className="log-batch-panel-label"><span>ORIGINAL RECORDING</span><strong>원본 영상 · 객체 주석</strong></div><RoadLogViewer frames={source.data.road_frames ?? []} timeS={timeS} onReady={readyChanged} /></div><div className="log-batch-requests"><div className="log-batch-panel-label"><span>SAME INPUT / OPEN LOOP</span><strong>같은 시점의 제어 요청</strong></div>{sample && <>
        <div className="log-batch-control-pair">{(["baseline", "candidate"] as const).map((side, index) => <article key={side} className={side}><div><span>{index ? "B" : "A"}</span><strong>{comparison.data!.profiles[side].label}</strong></div><small>{decisionLabel(sample[side].decision)}</small><strong className="log-batch-acceleration">{sample[side].acceleration_mps2 > 0 ? "+" : ""}{sample[side].acceleration_mps2.toFixed(2)}<span>m/s²</span></strong><footer><span>목표 속도 <b>{sample[side].target_speed_mps.toFixed(2)} m/s</b></span><span>조향 요청 <b>{sample[side].steering_rad.toFixed(3)} rad</b></span></footer></article>)}</div>
        <div className={`log-batch-current-difference ${opposite(sample) ? "opposite" : ""}`}>{opposite(sample) ? "이 표본에서 가속과 감속 요청이 반대입니다." : sample.baseline.decision !== sample.candidate.decision ? "이 표본에서 기준 제어기의 판단이 다릅니다." : "이 표본의 판단은 같으며 요청값을 비교합니다."}</div>
      </>}<CompactRequestChart samples={samples} candidate={candidate} timeS={timeS} onSeek={seek} /><div className="log-batch-window-metrics"><span>구간 내 판단 차이<strong>{candidate.metrics.decision_difference_count} / {candidate.metrics.sample_count}</strong></span><span>구간 내 방향 반대<strong>{candidate.metrics.opposite_acceleration_count} / {candidate.metrics.sample_count}</strong></span><span>최대 요청 차이<strong>{candidate.metrics.max_abs_acceleration_delta_mps2.toFixed(2)} m/s²</strong></span></div></div></div>
      <div className="log-batch-inspector-transport"><button className="button secondary" aria-label={playing ? "Log Sim inspector pause" : "Log Sim inspector play"} disabled={!ready} onClick={() => { if (timeS >= last) setTimeS(first); setPlaying(!playing); }}>{playing ? <Pause size={16} /> : <Play size={16} />}{playing ? "일시 정지" : "동기 재생"}</button><output>{timeS.toFixed(2)} <span>/ {last.toFixed(2)}s</span></output><input aria-label="Log Sim candidate playback time" type="range" min={first} max={last || 1} step="any" value={Math.min(last, Math.max(first, timeS))} onChange={event => seek(Number(event.target.value))} /><span>기록 ego {sample ? (sample.logged_speed_mps * 3.6).toFixed(1) : "—"} km/h</span></div>
      <p className="log-batch-inspector-boundary">원본 영상·기록 객체와 기준 제어기 요청을 함께 표시합니다. 요청은 차량에 적용되지 않으며 가감속 차이는 안전성 판정이 아닙니다.</p>
    </>}
    {comparison.data?.result && comparison.data.result.status !== "comparable" && <p className="log-sim-notice">이 후보의 실행을 비교할 수 없습니다. {comparison.data.result.reason}</p>}
    <footer className="log-batch-inspector-footer"><button className="button secondary" disabled={!canPrevious} onClick={() => onNavigate(-1)}><ArrowLeft size={14} />이전 후보</button><span>동일 원본 · A/B 입력 고정 · 후보 구간 별도 집계</span><button className="button secondary" disabled={!canNext} onClick={() => onNavigate(1)}>다음 후보<ArrowRight size={14} /></button></footer>
  </dialog>;
}

function CompactRequestChart({ samples, candidate, timeS, onSeek }: { samples: ReplaySample[]; candidate: LogBatchCandidate; timeS: number; onSeek: (time: number) => void }) {
  if (!samples.length) return null;
  const width = 540, height = 170, left = 35, top = 18, plotWidth = width - left - 13, plotHeight = height - top - 25;
  const first = samples[0].time_s, last = samples.at(-1)!.time_s, duration = Math.max(.001, last - first);
  const values = samples.flatMap(sample => [sample.baseline.acceleration_mps2, sample.candidate.acceleration_mps2]);
  const low = Math.min(-1, ...values) - .25, high = Math.max(1, ...values) + .25;
  const x = (time: number) => left + (time - first) / duration * plotWidth, y = (value: number) => top + (high - value) / (high - low) * plotHeight;
  const path = (side: "baseline" | "candidate") => samples.map((sample, index) => `${index ? "L" : "M"}${x(sample.time_s).toFixed(2)},${y(sample[side].acceleration_mps2).toFixed(2)}`).join(" ");
  return <div className="log-batch-mini-chart"><div><strong>가속도 요청 <small>m/s²</small></strong><span><i />A 기준 <i />B 비교</span></div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Log Sim synchronized acceleration requests" onClick={event => { const box = event.currentTarget.getBoundingClientRect(); const px = (event.clientX - box.left) / box.width * width; onSeek(Math.max(first, Math.min(last, first + (px - left) / plotWidth * duration))); }}>
    <rect x={x(candidate.time_s)} y={top} width={Math.max(2, x(candidate.end_time_s) - x(candidate.time_s))} height={plotHeight} fill="#ecf2df" />
    {[low, 0, high].map(value => <g key={value}><line x1={left} x2={width - 13} y1={y(value)} y2={y(value)} stroke="#dce5d7" strokeDasharray="3 5" /><text x={left - 6} y={y(value) + 3} textAnchor="end" fill="#899a7e" fontSize="9">{value.toFixed(1)}</text></g>)}
    <path d={path("baseline")} fill="none" stroke="#5b8fa7" strokeWidth="2" /><path d={path("candidate")} fill="none" stroke="#b88042" strokeWidth="2" /><line x1={x(Math.min(last, Math.max(first, timeS)))} x2={x(Math.min(last, Math.max(first, timeS)))} y1={top} y2={top + plotHeight} stroke="#4c6f3a" strokeDasharray="3 3" />
    {[0, .5, 1].map(fraction => <text key={fraction} x={x(first + duration * fraction)} y={height - 5} textAnchor="middle" fill="#859777" fontSize="9">{(first + duration * fraction).toFixed(2)}s</text>)}
  </svg><small>음영은 선택 후보 구간 · 클릭하면 원본과 함께 이동</small></div>;
}
const opposite = (sample: ReplaySample) => sample.baseline.acceleration_mps2 * sample.candidate.acceleration_mps2 < -1e-8;
const decisionLabel = (value: string) => ({ reference_cruise: "기준 속도 유지", reference_headway: "앞 객체 간격 반영", unavailable_objects_stop: "객체 입력 없어 정지 요청" }[value] ?? value);
