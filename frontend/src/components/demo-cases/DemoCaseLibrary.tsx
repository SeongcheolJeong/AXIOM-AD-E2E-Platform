import { useEffect, useRef, useState } from "react";
import { ArrowRight, ArrowUpRight, CheckCircle2, Clock3, FileCheck2, Play, RefreshCw } from "lucide-react";
import { useResource } from "../../api";
import { demoCaseCounts, demoCaseReady, demoCaseStory, demoCatalogError, demoStories, filterDemoCases, scopedDemoLink } from "./demo-cases";
import type { DemoCase, DemoCaseCatalog, DemoStory } from "./demo-cases";
import "./demo-cases.css";

const moduleNames: Record<string, string> = {
  object: "Object Sim", object_sim: "Object Sim", log: "Log Sim", log_sim: "Log Sim",
  sensor: "Sensor Sim", sensor_sim: "Sensor Sim", hil: "Software-DUT HIL", hil_sim: "Software-DUT HIL",
  neural: "Neural Sim", neural_sim: "Neural Sim",
};
const shortHash = (value: string | null) => value ? `${value.slice(0, 12)}…` : "기록 없음";

function CaseCard({ item, project, index, selected }: { item: DemoCase; project: string; index: number; selected: boolean }) {
  const ready = demoCaseReady(item, project);
  const primary = ready && item.primary_link ? scopedDemoLink(item.primary_link.href, project) : null;
  const links = ready ? item.links.filter(link => scopedDemoLink(link.href, project)) : [];
  const moments = ready ? item.key_moments.filter(moment => Number.isFinite(moment.time_s) && moment.time_s >= 0 && scopedDemoLink(moment.href, project)) : [];
  return <article id={`demo-case-card-${item.id}`} tabIndex={-1} className={`demo-case-card ${ready ? "is-ready" : "is-unavailable"}${selected ? " is-returned" : ""}`} aria-labelledby={`demo-case-${item.id}`} data-demo-case={item.id}>
    <div className="demo-case-card-top"><span className="demo-case-module"><span>{String(index + 1).padStart(2, "0")}</span>{moduleNames[item.module] ?? item.module}</span><span className={`demo-case-status ${ready ? "ready" : "pending"}`}>{ready ? <CheckCircle2 size={13} /> : <Clock3 size={13} />}{ready ? "저장 결과 준비됨" : item.status === "invalid" ? "근거 확인 필요" : "결과 준비 필요"}</span></div>
    <h4 id={`demo-case-${item.id}`}>{item.question}</h4>
    <p className="demo-case-title">{item.title}</p>
    <div className="demo-case-change"><span>{item.change.label}</span><div><strong>{item.change.before}</strong><ArrowRight size={15} aria-hidden="true" /><strong>{item.change.after}</strong>{item.change.unit && <small>{item.change.unit}</small>}</div></div>
    <div className="demo-case-observation"><span>{ready ? item.observation_source === "curator_note_for_pinned_results" ? "고정 실행의 관찰 기록" : "비교에서 살펴볼 내용" : "확인할 내용"}</span><p>{item.observation}</p></div>
    <p className="demo-case-next"><ArrowUpRight size={14} /><span>{item.next_action}</span></p>
    {!ready && <p className="demo-case-unavailable" role="status">{item.reason ?? "이 작업공간에서 일치하는 저장 결과를 확인하지 못했습니다. 실행 결과를 준비한 뒤 새로고침하세요."}</p>}
    {moments.length > 0 && <div className="demo-case-moments" aria-label={`${item.title} 핵심 시점`}>{moments.map(moment => <a href={moment.href} key={`${moment.label}-${moment.time_s}`} title={`실행 시간 ${moment.time_s}초에서 열기`}><Clock3 size={12} /><b>{moment.time_s.toFixed(1)}s</b>{moment.label}</a>)}</div>}
    <div className="demo-case-card-actions">{primary ? <a className="button primary" href={primary}><Play size={14} />{item.primary_link!.label}<ArrowRight size={15} /></a> : <span className="demo-case-disabled">결과가 준비되면 비교를 열 수 있습니다</span>}{links.map(link => <a className="demo-case-secondary-link" key={link.href} href={link.href}>{link.label}<ArrowUpRight size={13} /></a>)}</div>
    <details className="demo-case-evidence"><summary><FileCheck2 size={14} />원본·실행 근거와 해석 범위</summary><div>
      {item.source_family && <p><b>소스 묶음</b> {item.source_family.label}</p>}
      {item.limits.length > 0 && <ul>{item.limits.map(limit => <li key={limit}>{limit}</li>)}</ul>}
      {item.binding && <p className="demo-case-binding">{item.binding.kind} · {item.binding.id} · 개정 {item.binding.revision}<br /><span title={item.binding.sha256}>binding SHA-256 {shortHash(item.binding.sha256)}</span></p>}
      {item.executions.map(result => <dl key={`${result.role}-${result.id}`}><dt>{result.role}</dt><dd>{result.id}</dd><dt>생성 방식</dt><dd>{result.fidelity} · {result.adapter_id} {result.adapter_version}</dd><dt>프로젝트</dt><dd>{result.project_id}</dd><dt>결과 SHA-256</dt><dd title={result.manifest_hash}>{shortHash(result.manifest_hash)}</dd><dt>입력 SHA-256</dt><dd title={result.input_hash}>{shortHash(result.input_hash)}</dd>{result.configuration_hash && <><dt>설정 SHA-256</dt><dd title={result.configuration_hash}>{shortHash(result.configuration_hash)}</dd></>}</dl>)}
    </div></details>
  </article>;
}

export default function DemoCaseLibrary({ project }: { project: string }) {
  const catalog = useResource<DemoCaseCatalog>(`/demo-cases?project_id=${encodeURIComponent(project)}`);
  const [story, setStory] = useState<DemoStory>("all");
  const [readyOnly, setReadyOnly] = useState(true);
  const [returningCase] = useState(() => new URLSearchParams(location.hash.split("?")[1] ?? "").get("demo_case"));
  const restoredFocus = useRef(false);
  const identityError = catalog.data ? demoCatalogError(catalog.data, project) : null;
  const failure = catalog.error || identityError;
  const data = failure ? null : catalog.data;
  const counts = demoCaseCounts(data?.items ?? [], project);
  const visible = filterDemoCases(data?.items ?? [], project, story, readyOnly);
  useEffect(() => {
    if (!data || !returningCase || restoredFocus.current) return;
    const item = data.items.find(candidate => candidate.id === returningCase);
    if (!item) return;
    if (readyOnly && !demoCaseReady(item, project)) { setReadyOnly(false); return; }
    const frame = requestAnimationFrame(() => {
      const card = document.getElementById(`demo-case-card-${returningCase}`);
      if (!card) return;
      card.scrollIntoView({ block: "center", behavior: "instant" });
      card.focus({ preventScroll: true });
      restoredFocus.current = true;
    });
    return () => cancelAnimationFrame(frame);
  }, [data, project, returningCase, readyOnly]);
  return <section className="demo-case-library" aria-labelledby="demo-case-library-title">
    <div className="demo-case-hero"><div><span className="eyebrow">QUESTIONS → COMPARISONS → NEXT STEPS</span><h2 id="demo-case-library-title">어떤 문제를 살펴볼까요?</h2><p>질문 하나에서 시작해 조건을 바꾸고, 실제 실행 결과를 비교합니다.<br />세 가지 이야기와 새로운 시점 탐색을 준비했습니다.</p><div className="demo-case-scope"><span>CARLA 시뮬레이션</span><span>발표자 화면 공유용</span><span>현재 작업공간의 저장 근거</span></div></div><div className="demo-case-summary" aria-live="polite"><strong>{data ? counts.ready : "—"}<small> / {data ? data.items.length : "—"}</small></strong><span>바로 열 수 있는 비교 사례</span><div><span><b>{data ? counts.sourceFamilies : "—"}</b> 소스 묶음</span><span><b>{data ? counts.executions : "—"}</b> 고유 실행</span></div><small>조건 변형 수와 독립 도로·장면 수는 다릅니다.</small></div></div>
    <div className="demo-case-toolbar"><div className="demo-case-filters" role="group" aria-label="사례 이야기 선택">{([{ id: "all", title: "전체 사례" }, ...demoStories.map(item => ({ id: item.id, title: item.id === "viewpoint" ? "시점 탐색" : item.id === "investigate" ? "조사·재실행" : item.id === "observe" ? "센서 관측" : "입력 장애·반응" }))] as { id: DemoStory; title: string }[]).map(item => <button key={item.id} type="button" aria-pressed={story === item.id} onClick={() => setStory(item.id)}>{item.title}</button>)}</div><div className="demo-case-toolbar-actions"><label><input type="checkbox" checked={readyOnly} onChange={event => setReadyOnly(event.target.checked)} />준비된 사례만</label><button type="button" className="demo-case-refresh" onClick={catalog.reload} disabled={catalog.loading} aria-label="사례 결과 새로고침"><RefreshCw size={15} className={catalog.loading ? "spin" : undefined} />새로고침</button></div></div>
    {catalog.loading && <div className="demo-case-state" role="status"><RefreshCw size={20} className="spin" /><p>이 작업공간의 저장 결과와 사례 근거를 확인하고 있습니다…</p></div>}
    {failure && <div className="demo-case-state error-state" role="alert"><h3>사례 목록을 불러오지 못했습니다</h3><p>{failure}</p><button className="button secondary" onClick={catalog.reload}>다시 확인</button></div>}
    {!catalog.loading && !failure && !visible.length && <div className="demo-case-state"><h3>{data?.items.length ? "선택한 조건에 맞는 사례가 없습니다" : "이 작업공간의 사례가 아직 없습니다"}</h3><p>{readyOnly ? "‘준비된 사례만’을 해제하면 아직 필요한 결과도 확인할 수 있습니다." : "저장 결과를 연결한 뒤 사례 목록을 새로고침하세요."}</p></div>}
    {data && demoStories.map(group => {
      const items = visible.filter(item => demoCaseStory(item) === group.id);
      if (!items.length) return null;
      return <section className={`demo-case-story story-${group.id}`} key={group.id} aria-labelledby={`demo-story-${group.id}`}><div className="demo-case-story-heading"><span>{group.number}</span><div><h3 id={`demo-story-${group.id}`}>{group.title}</h3><p>{group.description}</p></div><small>{items.filter(item => demoCaseReady(item, project)).length}/{items.length} 준비됨</small></div><div className="demo-case-grid">{items.map(item => <CaseCard key={item.id} item={item} project={project} index={data.items.indexOf(item)} selected={item.id === returningCase} />)}</div></section>;
    })}
    {data && visible.some(item => !demoCaseStory(item)) && <div className="demo-case-grid">{visible.filter(item => !demoCaseStory(item)).map(item => <CaseCard key={item.id} item={item} project={project} index={data.items.indexOf(item)} selected={item.id === returningCase} />)}</div>}
    {data && <div className="demo-case-footnote"><p>저장된 실행을 비교하는 사례입니다. 새 실행은 각 화면에서 조건을 확인해 제출할 수 있습니다.</p>{data.limits.map(limit => <p key={limit}>{limit}</p>)}</div>}
  </section>;
}
