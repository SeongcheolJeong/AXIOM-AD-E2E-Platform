import { useMemo, useState } from "react";
import { ArrowRight, Check, Filter, Search } from "lucide-react";
import { batchKindLabel } from "./log-batch-contract";
import type { LogBatch, LogBatchCandidate, LogLibraryScene } from "./log-batch-contract";

export default function LogBatchBoard({ batch, scenes, selected, onToggle, onSelectMany, onInspect }: {
  batch: LogBatch; scenes: LogLibraryScene[]; selected: Set<string>;
  onToggle: (id: string) => void; onSelectMany: (ids: string[], checked: boolean) => void;
  onInspect: (candidate: LogBatchCandidate, ids: string[]) => void;
}) {
  const [query, setQuery] = useState(""), [kind, setKind] = useState(""), [decision, setDecision] = useState(""), [sign, setSign] = useState(""), [sourceId, setSourceId] = useState("");
  const [groupOverlaps, setGroupOverlaps] = useState(false);
  const [limit, setLimit] = useState(30);
  const sourceScene = useMemo(() => new Map(scenes.flatMap(scene => scene.segments.map(segment => [segment.source_id, scene] as const))), [scenes]);
  const sourceTitle = (id: string) => sourceScene.get(id)?.name ?? batch.sources.find(source => source.source_id === id)?.title ?? id;
  const matching = batch.candidates.filter(candidate => {
    const m = candidate.metrics;
    if (kind && candidate.kind !== kind || sourceId && candidate.source_id !== sourceId) return false;
    if (decision === "changed" && !m.decision_difference_count || decision === "same" && m.decision_difference_count > 0) return false;
    if (sign === "opposite" && !m.opposite_acceleration_count || sign === "same" && m.opposite_acceleration_count > 0) return false;
    const haystack = `${sourceTitle(candidate.source_id)} ${sourceScene.get(candidate.source_id)?.description ?? ""} ${candidate.label} ${candidate.kind} ${batchKindLabel(candidate.kind)}`.toLocaleLowerCase();
    return !query.trim() || haystack.includes(query.trim().toLocaleLowerCase());
  });
  // Connected time intervals in one source are a review convenience, not independent events.
  const groups: LogBatchCandidate[][] = [];
  for (const candidate of [...matching].sort((a,b) => a.source_id.localeCompare(b.source_id) || a.time_s-b.time_s)) {
    const group = groups.at(-1);
    if (group && group[0].source_id === candidate.source_id && candidate.time_s <= Math.max(...group.map(c => c.end_time_s))) group.push(candidate);
    else groups.push([candidate]);
  }
  const filtered = groupOverlaps ? groups.map(group => [...group].sort((a,b) => a.rank-b.rank)[0]).sort((a,b) => a.rank-b.rank) : matching;
  const allChecked = filtered.length > 0 && filtered.every(candidate => selected.has(candidate.id));
  const kinds = [...new Set(batch.candidates.map(candidate => candidate.kind))];
  const sourceIds = [...new Set(batch.candidates.map(candidate => candidate.source_id))];
  return <section className="log-batch-board" aria-label="Log Sim ranked candidates" data-testid="log-batch-board">
    <header><div><span className="log-sim-eyebrow">TRIAGE / REVIEW WHAT CHANGED</span><h2>먼저 검토할 장면</h2><p>판단 차이 → 가감속 방향 반대 → 요청 차이 순서의 검토 목록입니다. 순위는 안전 점수가 아닙니다.</p></div><span className="log-batch-candidate-total"><strong>{batch.candidates.length}</strong>조사 후보</span></header>
    <div className="log-batch-board-filters"><label className="log-batch-query"><Search size={15} /><input aria-label="Log Sim candidate search" placeholder="장면 설명, 사건 또는 scene 검색" value={query} onChange={event => { setQuery(event.target.value); setLimit(30); }} /></label><label><Filter size={13} /><select aria-label="Log Sim candidate rule filter" value={kind} onChange={event => { setKind(event.target.value); setLimit(30); }}><option value="">모든 사건</option>{kinds.map(value => <option key={value} value={value}>{batchKindLabel(value)}</option>)}</select></label><select aria-label="Log Sim candidate decision filter" value={decision} onChange={event => { setDecision(event.target.value); setLimit(30); }}><option value="">판단 차이 전체</option><option value="changed">판단이 다른 구간</option><option value="same">판단이 같은 구간</option></select><select aria-label="Log Sim candidate sign filter" value={sign} onChange={event => { setSign(event.target.value); setLimit(30); }}><option value="">가감속 방향 전체</option><option value="opposite">가감속 방향 반대</option><option value="same">방향 반대 없음</option></select><select aria-label="Log Sim candidate source filter" value={sourceId} onChange={event => { setSourceId(event.target.value); setLimit(30); }}><option value="">모든 원본 구간</option>{sourceIds.map(id => <option key={id} value={id}>{sourceTitle(id)} · {batch.sources.find(source => source.source_id === id)?.title ?? id.slice(-7)}</option>)}</select></div>
    <label><input aria-label="Group overlapping Log Sim candidates" type="checkbox" checked={groupOverlaps} onChange={event => setGroupOverlaps(event.target.checked)} />겹치는 시간 구간은 최상위 후보로 묶어 보기 · {groups.length}개 구간 / {matching.length}개 규칙 후보 (독립 사건 수 아님)</label>
    <div className="log-batch-board-count"><label><input aria-label="Select filtered Log Sim candidates" type="checkbox" checked={allChecked} disabled={!filtered.length} onChange={event => onSelectMany(filtered.map(candidate => candidate.id), event.target.checked)} />검색 결과 선택</label><span>현재 {filtered.length} / 전체 {batch.candidates.length}개 후보 · 선택 {selected.size}개</span><small>한 묶음 최대 100개</small></div>
    <div className="log-batch-table-scroll"><table className="log-batch-candidates"><thead><tr><th /><th>검토 순서</th><th>원본 장면 · 사건 구간</th><th>판단 차이</th><th>가감속 반대</th><th>최대 요청 차이</th><th /></tr></thead><tbody>{filtered.slice(0, limit).map(candidate => <tr key={candidate.id} className={selected.has(candidate.id) ? "selected" : ""} data-testid="log-batch-candidate"><td><input type="checkbox" aria-label={`Select Log Sim candidate ${candidate.id}`} checked={selected.has(candidate.id)} onChange={() => onToggle(candidate.id)} /></td><td><span className="log-batch-rank">{String(candidate.rank).padStart(2, "0")}</span></td><td><button className="log-batch-candidate-open" aria-label={`Inspect Log Sim candidate ${candidate.id}`} onClick={() => onInspect(candidate, filtered.map(value => value.id))}><span><strong>{sourceTitle(candidate.source_id)}</strong><small className={candidate.kind === "opposite_acceleration" ? "opposite" : ""}>{batchKindLabel(candidate.kind)}</small></span><small>{candidate.time_s.toFixed(2)}–{candidate.end_time_s.toFixed(2)}s · {candidate.metrics.sample_count}개 표본</small></button></td><td><MetricCount count={candidate.metrics.decision_difference_count} total={candidate.metrics.sample_count} /></td><td><MetricCount count={candidate.metrics.opposite_acceleration_count} total={candidate.metrics.sample_count} opposite /></td><td><span className="log-batch-delta">{candidate.metrics.max_abs_acceleration_delta_mps2.toFixed(2)}<small>m/s²</small></span></td><td><button className="log-batch-inspect-arrow" aria-label={`Open Log Sim candidate ${candidate.id}`} onClick={() => onInspect(candidate, filtered.map(value => value.id))}><ArrowRight size={16} /></button></td></tr>)}</tbody></table></div>
    {!filtered.length && <div className="log-batch-empty"><Search size={24} /><p>{batch.candidates.length ? "이 검색 조건에 맞는 조사 후보가 없습니다." : batch.status === "completed" ? "분석 완료 · 후보 규칙에 해당하는 구간이 없습니다." : "분석을 완료한 원본에서 조사 후보를 모으고 있습니다."}</p></div>}
    {filtered.length > limit && <button className="button secondary log-batch-more" onClick={() => setLimit(value => value + 30)}>후보 더 보기 · {Math.min(limit, filtered.length)} / {filtered.length}</button>}
    {selected.size > 0 && <div className="log-batch-selection-hint"><Check size={14} /><span>{selected.size}개 후보 선택됨 · 아래에서 Case 묶음으로 저장하세요.</span></div>}
  </section>;
}

function MetricCount({ count, total, opposite = false }: { count: number; total: number; opposite?: boolean }) {
  return <span className={`log-batch-metric-count ${count > 0 ? opposite ? "opposite" : "different" : ""}`}><strong>{count}</strong><small> / {total}</small><i style={{ width: `${total ? count / total * 100 : 0}%` }} /></span>;
}
