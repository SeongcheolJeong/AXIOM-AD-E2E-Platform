import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Clock3, Search, ScanSearch } from "lucide-react";
import { useResource } from "../../api";
import { ErrorState, Loading } from "../ui";
import type { DetectedLogEvent, LogEventSearch } from "./log-sim-contract";

const kinds = [
  ["", "전체"], ["low_speed", "저속·정지"], ["deceleration", "감속"], ["nearby_ahead", "전방 근접"],
] as const;

export default function EventExplorer({ project, sourceId, selectedId, onSelect }: {
  project: string; sourceId: string; selectedId: string; onSelect: (event: DetectedLogEvent) => void;
}) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(() => { const timer = setTimeout(() => { setSearch(query.trim()); setOffset(0); }, 250); return () => clearTimeout(timer); }, [query]);
  const params = new URLSearchParams({ project_id: project, offset: String(offset), limit: "4" });
  if (search) params.set("q", search);
  if (kind) params.set("kind", kind);
  const result = useResource<LogEventSearch>(`/log-sim/sources/${encodeURIComponent(sourceId)}/events?${params}`);
  return <section className="log-event-explorer" aria-label="Log Sim automatic event search">
    <div className="log-event-heading"><div><span className="log-sim-eyebrow">FIND THE MOMENT</span><h3><ScanSearch size={17} />자동으로 찾은 조사 후보</h3></div><span className="log-event-count">{result.data ? `${result.data.total}개` : "분석 중"}</span></div>
    <div className="log-event-filters"><label><Search size={14} /><input aria-label="Log Sim event search" placeholder="사건 이름이나 객체 ID 검색" value={query} onChange={event => setQuery(event.target.value)} /></label><div role="group" aria-label="Log Sim event kind">{kinds.map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => { setKind(value); setOffset(0); }}>{label}</button>)}</div></div>
    {result.loading && <Loading label="기록된 속도와 객체 위치에서 조사 후보를 찾는 중…" />}
    {result.error && <ErrorState message={result.error} retry={result.reload} />}
    {result.data && <>
      <div className="log-event-results">{result.data.items.map(event => <button key={event.id} type="button" aria-label={`Log Sim detected event ${event.id}`} aria-pressed={selectedId === event.id} className={selectedId === event.id ? "selected" : ""} onClick={() => onSelect(event)}>
        <span className={`log-event-dot ${event.kind}`} /><span className="log-event-description"><strong>{event.label}</strong><small>{evidenceLabel(event)}</small></span><span className="log-event-time"><Clock3 size={12} />{event.time_s.toFixed(2)}–{event.end_time_s.toFixed(2)}s</span><ArrowRight size={15} />
      </button>)}</div>
      {!result.data.items.length && <p className="log-event-empty">이 조건에 맞는 조사 후보가 없습니다. 검색어 또는 사건 종류를 바꿔보세요.</p>}
      <div className="log-event-footer"><small>속도·위치 규칙으로 찾은 후보 · 안전 실패 판정 아님</small><div><button type="button" aria-label="Log Sim previous events" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 4))}><ArrowLeft size={13} /></button><span>{result.data.total ? `${offset + 1}–${offset + result.data.items.length} / ${result.data.total}` : "0 / 0"}</span><button type="button" aria-label="Log Sim next events" disabled={offset + result.data.items.length >= result.data.total} onClick={() => setOffset(offset + 4)}><ArrowRight size={13} /></button></div></div>
    </>}
  </section>;
}

function evidenceLabel(event: DetectedLogEvent) {
  const evidence = event.evidence;
  if (typeof evidence.minimum_speed_mps === "number") return `최소 ${(evidence.minimum_speed_mps * 3.6).toFixed(1)} km/h · ${evidence.sample_count}개 표본`;
  if (typeof evidence.minimum_acceleration_mps2 === "number") return `최소 가속도 ${evidence.minimum_acceleration_mps2.toFixed(2)} m/s² · ${evidence.sample_count}개 표본`;
  if (typeof evidence.minimum_longitudinal_center_distance_m === "number") return `전방 중심 거리 ${evidence.minimum_longitudinal_center_distance_m.toFixed(1)} m · ${evidence.object_id ?? "객체"}`;
  return event.description;
}
