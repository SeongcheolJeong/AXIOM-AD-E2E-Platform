import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Check, GitCompareArrows, LoaderCircle, Play, RefreshCw, Repeat2, Save } from "lucide-react";
import { api, useResource } from "../../api";
import { activeExecution } from "./log-sim-contract";
import type { LogCase, ReplayComparison, ReplayPreview, ReplayProfile, ReplayRegression, ReplayRequest, ReplaySample } from "./log-sim-contract";

const errorText = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const finite = (value: number | null | undefined, digits = 2) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
const comparisonActive = (items: ReplayComparison[]) => items.some(item => activeExecution(item.status));
const regressionActive = (items: ReplayRegression[]) => items.some(item => item.runs.some(run => activeExecution(run.status)));
const phaseLabel = (status: string) => ({ queued: "실행 대기", pending: "실행 대기", running: "계산 중", completed: "완료", failed: "실행 실패", cancelled: "취소됨" }[status] ?? status);

function useRetainedList<T>(path: string, shouldPoll: (items: T[]) => boolean) {
  const [items, setItems] = useState<T[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const value = await api<{ items: T[] }>(path, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setItems(value.items); setError(""); setLoading(false);
        if (shouldPoll(value.items)) timer = setTimeout(() => void read(), 1800);
      } catch (reason) { if (!controller.signal.aborted) { setError(errorText(reason)); setLoading(false); } }
    }
    void read(); return () => { controller.abort(); clearTimeout(timer); };
  }, [path, revision, shouldPoll]);
  return { items, setItems, loading, error, reload };
}

export default function ReferenceReplay({ project, sourceId, sourceKind, savedCase, writable, timeS, onSeek }: {
  project: string; sourceId: string; sourceKind: "carla" | "nuscenes" | "recorded"; savedCase: LogCase | null; writable: boolean; timeS: number; onSeek: (time: number) => void;
}) {
  const profiles = useResource<{ items: ReplayProfile[] }>(`/log-sim/replay-profiles?project_id=${encodeURIComponent(project)}`);
  const query = new URLSearchParams({ project_id: project, source_id: sourceId });
  const history = useRetainedList<ReplayComparison>(`/log-sim/comparisons?${query}`, comparisonActive);
  const regressions = useRetainedList<ReplayRegression>(`/log-sim/regressions?${query}`, regressionActive);
  const [baseline, setBaseline] = useState("balanced"), [candidate, setCandidate] = useState("cautious");
  const [preview, setPreview] = useState<{ signature: string; value: ReplayPreview } | null>(null);
  const [busy, setBusy] = useState(""), [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [deceleration, setDeceleration] = useState(5), [targetSpeed, setTargetSpeed] = useState(10);
  const comparisonPending = useRef({ body: "", key: "" });
  const regressionPending = useRef({ body: "", key: "" });
  const rerunPending = useRef<Record<string, string>>({});
  useEffect(() => { setSelectedId(""); }, [savedCase?.id, savedCase?.revision]);
  const active = history.items.find(item => item.id === selectedId) ?? (savedCase ? history.items.find(item => item.case_ref.id === savedCase.id && item.case_ref.revision === savedCase.revision) : history.items[0]);
  const request: ReplayRequest | null = savedCase ? { project_id: project, source_id: sourceId, case_id: savedCase.id, case_revision: savedCase.revision, baseline_profile: baseline, candidate_profile: candidate } : null;
  const signature = JSON.stringify(request);
  const currentPreview = preview?.signature === signature ? preview.value : null;
  const running = active && activeExecution(active.status);
  const validCriteria = Number.isFinite(deceleration) && deceleration >= 0 && deceleration <= 15 && Number.isFinite(targetSpeed) && targetSpeed >= 0 && targetSpeed <= 50;

  async function prepare() {
    if (!request) return;
    setBusy("preview"); setError(""); setPreview(null);
    try { setPreview({ signature, value: await api<ReplayPreview>("/log-sim/comparisons/preview", { method: "POST", body: signature }) }); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }
  async function compare() {
    if (!request || !currentPreview) return;
    setBusy("compare"); setError("");
    const body = JSON.stringify({ ...request, preview_hash: currentPreview.preview_hash });
    if (comparisonPending.current.body !== body) comparisonPending.current = { body, key: crypto.randomUUID() };
    try {
      const value = await api<ReplayComparison>("/log-sim/comparisons", { method: "POST", body, headers: { "Idempotency-Key": comparisonPending.current.key } });
      history.setItems(items => [value, ...items.filter(item => item.id !== value.id)]); setSelectedId(value.id); history.reload(); setPreview(null);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }
  async function saveRegression() {
    if (!active || !validCriteria) return;
    setBusy("save-regression"); setError("");
    const body = JSON.stringify({ project_id: project, name: `${active.profiles.baseline.label} → ${active.profiles.candidate.label} · Case r${active.case_ref.revision}`, max_deceleration_request_mps2: deceleration, max_target_speed_mps: targetSpeed });
    const signature = `${active.id}/${body}`;
    if (regressionPending.current.body !== signature) regressionPending.current = { body: signature, key: crypto.randomUUID() };
    try {
      const value = await api<ReplayRegression>(`/log-sim/comparisons/${encodeURIComponent(active.id)}/regressions`, { method: "POST", body, headers: { "Idempotency-Key": regressionPending.current.key } });
      regressions.setItems(items => [value, ...items.filter(item => item.id !== value.id)]); regressions.reload();
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }
  async function rerun(regression: ReplayRegression) {
    setBusy(regression.id); setError("");
    try {
      const key = rerunPending.current[regression.id] ??= crypto.randomUUID();
      await api(`/log-sim/regressions/${encodeURIComponent(regression.id)}/runs`, { method: "POST", body: JSON.stringify({ project_id: project }), headers: { "Idempotency-Key": key } });
      delete rerunPending.current[regression.id];
      regressions.reload(); history.reload();
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(""); }
  }

  return <section className="log-replay-workflow" aria-label="Log Sim same-log reference comparison" data-testid="log-reference-replay">
    <header className="log-replay-heading"><div><span className="log-sim-eyebrow">SAME LOG · TWO CONTROLLERS · REPEATABLE TEST</span><h2>이 장면에서, 제어 요청은 어떻게 달라질까?</h2><p>{sourceKind === "nuscenes" ? "nuScenes 원본 객체 주석과 ego 상태" : sourceKind === "recorded" ? "사용자 로그에 기록된 객체와 ego 상태" : "CARLA에 기록된 객체 GT와 ego 상태"}를 두 기준 제어기에 동일하게 입력합니다.</p></div><span className="log-replay-mode">OPEN LOOP<span>기준 제어기 · 학습 모델 아님</span></span></header>
    <div className="log-replay-author"><div className="log-replay-profile"><span>A</span><label>기준 제어기<select aria-label="Log Sim baseline replay profile" value={baseline} disabled={!!busy || !!running} onChange={event => setBaseline(event.target.value)}>{profiles.data?.items.map(profile => <option key={profile.id} value={profile.id}>{profile.label}</option>)}</select><small>{profileSummary(profiles.data?.items.find(profile => profile.id === baseline))}</small></label></div><ArrowRight className="log-replay-between" size={19} /><div className="log-replay-profile candidate"><span>B</span><label>비교 제어기<select aria-label="Log Sim candidate replay profile" value={candidate} disabled={!!busy || !!running} onChange={event => setCandidate(event.target.value)}>{profiles.data?.items.map(profile => <option key={profile.id} value={profile.id}>{profile.label}</option>)}</select><small>{profileSummary(profiles.data?.items.find(profile => profile.id === candidate))}</small></label></div><div className="log-replay-start"><button className="button secondary" disabled={!savedCase || !writable || !!busy || !!running || !profiles.data || baseline === candidate} onClick={() => void prepare()}>{busy === "preview" ? <LoaderCircle className="spin" size={15} /> : <GitCompareArrows size={15} />}A/B 입력 확인</button><small>{savedCase ? `Case r${savedCase.revision} · 동일 원본 고정` : "위에서 조사 구간을 Case로 저장하세요."}</small></div></div>
    {profiles.error && <p className="log-sim-error" role="alert">{profiles.error}</p>}
    {currentPreview && <div className="log-replay-preview" data-testid="log-reference-preview"><div><Check size={16} /><span>동일 원본 {currentPreview.frame_count}개 표본 · Case 구간 {currentPreview.case_frame_count}개<small>전체 기록 순서대로 실행하고 선택 구간을 따로 집계합니다.</small></span></div><button className="button" disabled={!!busy || !writable} onClick={() => void compare()}>{busy === "compare" ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}동일 로그 A/B 실행</button></div>}
    <p className="log-replay-boundary">비교하는 값은 가속도·조향·목표 속도 요청입니다. 요청을 차량에 적용하지 않으며, 원본 영상과 다음 관측은 그대로 유지됩니다.</p>
    {history.error && <p className="log-sim-error" role="alert">{history.error}<button type="button" onClick={history.reload}>다시 조회</button></p>}
    {history.loading && <p className="log-sim-muted">저장된 비교 불러오는 중…</p>}
    {active && <>
      <div className="log-replay-result-heading"><div><span className="log-sim-eyebrow">{running ? "EXECUTING THE PINNED LOG" : "SAVED COMPARISON"}</span><h3>{running && <LoaderCircle size={16} className="spin" />}{active.profiles.baseline.label}<ArrowRight size={16} />{active.profiles.candidate.label}<span data-testid="log-reference-status">{phaseLabel(active.status)}</span></h3><small>Case r{active.case_ref.revision} · {active.frame_count}개 원본 표본</small></div><div>{history.items.length > 1 && <select aria-label="Log Sim saved reference comparison" value={active.id} onChange={event => setSelectedId(event.target.value)}>{history.items.map(item => <option key={item.id} value={item.id}>{item.profiles.baseline.label} → {item.profiles.candidate.label} · {phaseLabel(item.status)} · {item.id.slice(-8)}</option>)}</select>}<button className="log-sim-icon-button" aria-label="Log Sim refresh reference comparison" onClick={history.reload}><RefreshCw size={15} /></button></div></div>
      {running && <p className="log-replay-progress" role="status">두 기준 제어기가 고정된 원본을 계산하고 있습니다. 완료되면 차이와 시간축이 표시됩니다.</p>}
      {active.result?.status === "comparable" ? <>
        <div className="log-replay-summary"><article><small>요청이 달라진 표본</small><strong>{active.result.changed_request_count}<span> / {active.result.frame_count}</span></strong></article><article><small>선택한 Case 안의 차이</small><strong>{active.result.case_changed_request_count}<span> / {active.case_frame_count}</span></strong></article><article><small>첫 요청 차이</small><strong>{finite(active.result.first_changed_time_s)}<span> s</span></strong>{typeof active.result.first_changed_time_s === "number" && <button onClick={() => { onSeek(active.result!.first_changed_time_s!); document.querySelector(".log-sim-investigation")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>원본에서 보기 <ArrowRight size={12} /></button>}</article></div>
        <RequestChart samples={active.result.samples} timeS={timeS} onSeek={onSeek} />
        <div className="log-replay-metric-table"><table><thead><tr><th>제어 요청 지표</th><th>A · {active.profiles.baseline.label}</th><th>B · {active.profiles.candidate.label}</th><th>차이 B − A</th></tr></thead><tbody>{active.result.metrics.map(metric => <tr key={metric.name}><th>{metric.label}</th><td>{finite(metric.baseline)} {metric.unit}</td><td>{finite(metric.candidate)} {metric.unit}</td><td>{metric.delta > 0 ? "+" : ""}{finite(metric.delta)} {metric.unit}</td></tr>)}</tbody></table></div>
        <details className="log-replay-evidence"><summary>입력과 실행 출처 확인</summary><dl><dt>A 고정 제어기</dt><dd>{profileSummary(active.profiles.baseline)}</dd><dt>B 고정 제어기</dt><dd>{profileSummary(active.profiles.candidate)}</dd><dt>원본 hash</dt><dd>{active.source_hash}</dd><dt>동일 입력 sequence</dt><dd>{active.sequence_sha256}</dd><dt>Case</dt><dd>{active.case_ref.id} · r{active.case_ref.revision}</dd><dt>실행 A / B</dt><dd>{active.execution_ids.baseline}<br />{active.execution_ids.candidate}</dd></dl><p>차이는 우열이나 안전성 점수가 아닙니다. 원본 객체 주석을 사용한 기준 제어기의 요청 차이입니다.</p></details>
        <section className="log-regression-create" aria-label="Log Sim save regression"><div><span className="log-sim-eyebrow">KEEP THIS CASE IN THE LOOP</span><h3><Repeat2 size={17} />이 비교를 반복 시험으로 저장</h3><p>원본·Case·제어기를 고정하고, 재실행의 출력 재현 여부와 요청 한도를 확인합니다.</p></div><div className="log-regression-fields"><label>감속 요청 한도 (m/s²)<input aria-label="Log Sim regression deceleration limit" type="number" min="0" max="15" step="0.1" value={deceleration} onChange={event => setDeceleration(Number(event.target.value))} /></label><label>목표 속도 한도 (m/s)<input aria-label="Log Sim regression target speed limit" type="number" min="0" max="50" step="0.5" value={targetSpeed} onChange={event => setTargetSpeed(Number(event.target.value))} /></label><button className="button secondary" disabled={!writable || !!busy || !validCriteria} onClick={() => void saveRegression()}>{busy === "save-regression" ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}반복 시험 저장</button></div><small>사용자가 정한 진단 기준입니다. 차량의 제동 성능이나 안전 합격을 뜻하지 않습니다.</small></section>
      </> : active.result ? <p className="log-sim-notice">비교 미평가 · {active.result.reason ?? active.result.status}</p> : !running && <p className="log-sim-notice">실행 결과를 비교할 수 없습니다. 실행 상태와 원본 입력을 확인하세요.</p>}
    </>}
    {regressions.items.length > 0 && <section className="log-regression-list" aria-label="Log Sim saved regressions"><h3>저장된 반복 시험 <span>{regressions.items.length}</span></h3>{regressions.items.map(regression => { const run = regression.runs[0], pending = !!run && activeExecution(run.status); return <article key={regression.id} data-testid="log-regression"><div className="log-regression-row"><div><strong>{regression.name}</strong><small>감속 요청 ≤ {regression.criteria.max_deceleration_request_mps2} m/s² · 목표 속도 ≤ {regression.criteria.max_target_speed_mps} m/s</small></div><span className={`log-regression-verdict ${pending ? "pending" : run?.verdict ?? "none"}`} data-testid="log-regression-verdict">{pending ? phaseLabel(run.status) : !run ? "아직 재실행 안 함" : run.verdict === "pass" ? "요청 기준 충족" : run.verdict === "fail" ? "요청 기준 초과" : "미평가"}</span><button className="button secondary" aria-label={`Log Sim rerun regression ${regression.id}`} disabled={!writable || !!busy || pending} onClick={() => void rerun(regression)}>{pending || busy === regression.id ? <LoaderCircle className="spin" size={14} /> : <Repeat2 size={14} />}다시 실행</button></div>{run && !pending && <div className="log-regression-run"><span>저장된 출력 재현: <strong>{run.matches_saved_outputs === true ? "동일" : run.matches_saved_outputs === false ? "달라짐" : "미평가"}</strong></span><span>누적 {regression.runs.length}회</span>{run.checks.map(check => <span key={check.name}>{check.name === "max_deceleration_request_mps2" ? "감속 요청" : check.name === "max_target_speed_mps" ? "목표 속도" : check.name}: {finite(check.value)} / {finite(check.limit)} · {check.passed === null ? "미평가" : check.passed ? "충족" : "초과"}</span>)}<button type="button" onClick={() => { setSelectedId(run.comparison_id); history.reload(); }}>이 재실행 비교 보기 <ArrowRight size={12} /></button></div>}</article>; })}</section>}
    {regressions.error && <p className="log-sim-error" role="alert">반복 시험 조회: {regressions.error}<button onClick={regressions.reload}>다시 조회</button></p>}
    {error && <p className="log-sim-error" role="alert">{error}</p>}
  </section>;
}

function RequestChart({ samples, timeS, onSeek }: { samples: ReplaySample[]; timeS: number; onSeek: (time: number) => void }) {
  if (!samples.length) return null;
  const width = 1000, height = 175, left = 42, top = 18, plotWidth = width - left - 18, plotHeight = height - top - 27;
  const first = samples[0].time_s, last = samples.at(-1)!.time_s, duration = Math.max(.001, last - first);
  const values = samples.flatMap(sample => [sample.baseline.acceleration_mps2, sample.candidate.acceleration_mps2]);
  const min = Math.min(-1, ...values) - .4, max = Math.max(1, ...values) + .4;
  const x = (time: number) => left + (time - first) / duration * plotWidth;
  const y = (value: number) => top + (max - value) / (max - min) * plotHeight;
  const path = (side: "baseline" | "candidate") => samples.map((sample, index) => `${index ? "L" : "M"}${x(sample.time_s).toFixed(2)},${y(sample[side].acceleration_mps2).toFixed(2)}`).join(" ");
  const selected = samples.filter(sample => sample.time_s <= timeS + .000001).at(-1) ?? samples[0];
  const caseFrames = samples.filter(sample => sample.in_case);
  return <div className="log-request-chart" data-testid="log-reference-chart"><div className="log-request-chart-heading"><strong>가속도 요청 <small>m/s² · 차트에서 원본 시점으로 이동</small></strong><div><span>A 기준</span><span>B 비교</span><span>Case 구간</span></div></div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="동일 기록에서 두 기준 제어기의 가속도 요청 비교" onClick={event => { const rect = event.currentTarget.getBoundingClientRect(); const px = (event.clientX - rect.left) / rect.width * width; onSeek(Math.max(first, Math.min(last, first + (px - left) / plotWidth * duration))); }}>
    {caseFrames.length > 0 && <rect x={x(caseFrames[0].time_s)} y={top} width={Math.max(3, x(caseFrames.at(-1)!.time_s) - x(caseFrames[0].time_s))} height={plotHeight} fill="#eef2dd" />}
    {[min, 0, max].map(value => <g key={value}><line x1={left} x2={width - 18} y1={y(value)} y2={y(value)} stroke="#dce5df" strokeDasharray="3 5" /><text x={left - 8} y={y(value) + 3} textAnchor="end" fill="#7b8e81" fontSize="10">{value.toFixed(1)}</text></g>)}
    <path d={path("baseline")} fill="none" stroke="#5b8fa7" strokeWidth="2.2" /><path d={path("candidate")} fill="none" stroke="#b88042" strokeWidth="2.2" />
    <line x1={x(selected.time_s)} x2={x(selected.time_s)} y1={top} y2={top + plotHeight} stroke="#2e5b43" strokeDasharray="3 3" /><circle cx={x(selected.time_s)} cy={y(selected.baseline.acceleration_mps2)} r="3.5" fill="#5b8fa7" /><circle cx={x(selected.time_s)} cy={y(selected.candidate.acceleration_mps2)} r="3.5" fill="#b88042" />
    {[0, .25, .5, .75, 1].map(fraction => <text key={fraction} x={x(first + duration * fraction)} y={height - 6} textAnchor="middle" fontSize="10" fill="#7b8e81">{(first + duration * fraction).toFixed(2)}s</text>)}
  </svg><div className="log-request-at"><span>{selected.time_s.toFixed(2)}s · 기록 속도 {finite(selected.logged_speed_mps * 3.6, 1)} km/h</span><span>A <strong>{finite(selected.baseline.acceleration_mps2)} m/s²</strong> · {decisionLabel(selected.baseline.decision)}</span><span>B <strong>{finite(selected.candidate.acceleration_mps2)} m/s²</strong> · {decisionLabel(selected.candidate.decision)}</span></div><div className="log-request-samples" aria-label="Log Sim replay sample navigation">{samples.length <= 24 && samples.map(sample => <button key={sample.index} type="button" aria-label={`Log Sim reference sample ${sample.index}`} aria-pressed={sample.index === selected.index} onClick={() => onSeek(sample.time_s)}>{sample.time_s.toFixed(1)}s</button>)}</div></div>;
}

function decisionLabel(value: string) { return ({ reference_cruise: "기준 속도 유지", reference_headway: "앞 객체 간격 반영", unavailable_objects_stop: "객체 입력 없어 정지 요청" }[value] ?? value); }

function profileSummary(profile?: ReplayProfile) { return profile ? `속도 ${profile.controller.cruise_speed_mps} m/s · 차간 시간 ${profile.controller.time_headway_s}s · 정지 여유 ${profile.controller.stop_buffer_m}m` : "설정 불러오는 중…"; }
