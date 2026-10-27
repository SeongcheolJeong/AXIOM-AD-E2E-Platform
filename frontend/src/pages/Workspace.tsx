import { useEffect, useState } from "react";
import { ArrowRight, Box, Cpu, Film, ScanEye, ShieldCheck, Workflow } from "lucide-react";
import { useResource } from "../api";
import { Badge, ErrorState, PageHeading } from "../components/ui";
import { retainedExecutionSource } from "../retainedExecutionSources";
import DemoCaseLibrary from "../components/demo-cases/DemoCaseLibrary";
import BetaWorkspace from "../components/beta-workspace/BetaWorkspace";
import "./workspace.css";

type Kind = "object_sim" | "planning_replay" | "sensor_capture" | "isp_compare" | "driving_open_loop" | "hil_demo";
type Execution = { id: string; name: string; experiment_kind: Kind; status: string; fidelity: string | null; created_at: string; manifest_hash: string | null };
type Handler = { id: string; status: string; kinds: Kind[] };
const kindNames: Record<Kind, string> = { object_sim: "Object Sim", planning_replay: "Log Sim", sensor_capture: "Sensor Sim", isp_compare: "RAW / ISP", driving_open_loop: "Driving policy", hil_demo: "HIL Workbench" };
function resultRoute(item: Execution, project: string) {
  const page = item.experiment_kind === "hil_demo" ? "hil-sim" : item.experiment_kind === "object_sim" ? "object-sim" : ["sensor_capture", "isp_compare"].includes(item.experiment_kind) ? "sensor-sim" : "viewers";
  return `#${page}?project=${encodeURIComponent(project)}&execution=${encodeURIComponent(item.id)}`;
}

export default function Workspace() {
  const identity = useResource<{ project_id?: string; permissions: { execute: boolean } }>("/team/whoami");
  const projects = useResource<{ items: { id: string; name: string }[] }>("/projects");
  const [selectedProject, setSelectedProject] = useState(new URLSearchParams(location.hash.split("?")[1] ?? "").get("project") ?? "default");
  const availableProjects = projects.data?.items ?? [];
  const project = identity.data?.project_id ?? (availableProjects.some(item => item.id === selectedProject) ? selectedProject : availableProjects[0]?.id);
  const failure = identity.error || projects.error;
  useEffect(() => {
    if (!project || !location.hash.startsWith("#workspace")) return;
    const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
    const previous = params.get("project");
    if (previous === project) return;
    params.set("project", project);
    if (previous) { params.delete("beta_task"); params.delete("demo_case"); }
    history.replaceState(history.state, "", `#workspace?${params}`);
  }, [project]);
  const selectProject = (value: string) => {
    setSelectedProject(value);
    const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
    params.set("project", value);
    params.delete("demo_case");
    history.replaceState(history.state, "", `#workspace?${params}`);
  };
  return <div className="workspace-home">
    <PageHeading eyebrow="WORKSPACE" title="문제에서 시작하는 시뮬레이션" description="실제 저장 결과로 질문을 살펴보고, 조건 변경과 다음 조사로 이어갑니다." />
    <div className="workspace-scope"><label>작업공간<select aria-label="Start workspace project" value={project ?? ""} disabled={!!identity.data?.project_id || !availableProjects.length} onChange={event => selectProject(event.target.value)}>{!availableProjects.length && <option value={project ?? ""}>{project ?? "작업공간 확인 중…"}</option>}{availableProjects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><span>선택한 작업공간의 결과만 표시합니다. 사례 목록의 조건 변형과 고유 실행 수를 구분합니다.</span></div>
    {failure && <ErrorState message={failure} retry={() => { identity.reload(); projects.reload(); }} />}
    {project && <WorkspaceProjectContent key={project} project={project} canExecute={identity.data?.permissions.execute === true} />}
  </div>;
}

function WorkspaceProjectContent({ project, canExecute }: { project: string; canExecute: boolean }) {
  const executions = useResource<{ items: Execution[]; next_before_seq: number | null }>(`/experiments/executions?project_id=${encodeURIComponent(project)}&limit=100`);
  const capabilities = useResource<{ handlers: Handler[] }>("/experiments/capabilities");
  const items = executions.data?.items ?? [];
  const hasHistory = executions.data !== null;
  const scoped = `project=${encodeURIComponent(project)}`;
  const available = (kinds: Kind[]) => capabilities.data?.handlers.some(handler => handler.status === "available" && handler.kinds.some(kind => kinds.includes(kind)));
  const count = (kinds: Kind[]) => items.filter(item => kinds.includes(item.experiment_kind) && item.status === "completed").length;
  const countLabel = (kinds: Kind[]) => !hasHistory ? "결과 조회 중" : `${count(kinds)}개 완료 결과${executions.data?.next_before_seq ? " · 최근 100건 기준" : ""}`;
  const latestSensor = items.find(item => ["sensor_capture", "isp_compare"].includes(item.experiment_kind) && item.status === "completed");
  const latestFeedback = items.find(item => item.experiment_kind === "sensor_capture" && item.status === "completed" && item.fidelity?.includes("feedback"));
  const carlaResults = items.filter(item => item.status === "completed" && retainedExecutionSource(item.id, item.manifest_hash));
  const carlaRoad = carlaResults.find(item => item.id === "job_59913285dc8d4e38afca789b72bcd0a3") ?? carlaResults[0];
  const latestObject = items.find(item => item.experiment_kind === "object_sim" && item.status === "completed");
  const failure = executions.error || capabilities.error;

  return <>
    <BetaWorkspace key={`beta:${project}`} project={project} />
    <DemoCaseLibrary key={project} project={project} />
    <div className="workspace-section-heading workspace-tools-heading"><div><span className="eyebrow">BUILD YOUR NEXT EXPERIMENT</span><h2>직접 조건을 바꾸고 실행하기</h2></div><button className="button secondary" onClick={() => { executions.reload(); capabilities.reload(); }}>실행 결과 새로고침</button></div>
    {failure && <ErrorState message={failure} retry={() => { executions.reload(); capabilities.reload(); }} />}
    <section className="workspace-task-grid" aria-label="Available workflows">
      <article className="workspace-task object"><Box size={26} /><h2>Object Sim</h2><p>도로 위 차량을 배치하고 행동·트리거를 작성합니다. 개정을 저장하고 설정된 엔진에서 실행한 뒤 결과 궤적을 재생합니다.</p><div className="workspace-task-state">{countLabel(["object_sim"])}{capabilities.data && <small>{available(["object_sim"]) ? "실행 어댑터 설정됨 · 준비 상태는 위에서 확인" : "Object 실행 엔진 미설정"}</small>}</div><div className="workspace-task-actions"><a className="button primary" href={`#object-sim?${scoped}&mode=author`}>{canExecute ? "시나리오 작성·실행" : "시나리오 살펴보기"}<ArrowRight size={15} /></a><a className="button secondary" href={latestObject ? resultRoute(latestObject, project) : `#object-sim?${scoped}`}>궤적 재생 Viewer</a></div><small>3D 화면은 객체 배치 도식입니다. 센서 영상이나 주행 안전 검증이 아닙니다.</small></article>
      <article className="workspace-task sensor"><ScanEye size={26} /><h2>Sensor Sim</h2><p>CARLA feedback 주행의 계획·실제 자차·새 카메라 영상을 함께 재생하고, 렌즈·센서별 RAW·ISP 영상과 원본 근거를 비교합니다.</p><div className="workspace-task-state">{countLabel(["sensor_capture", "isp_compare"])}{capabilities.data && <small>{available(["sensor_capture", "isp_compare"]) ? "설정된 센서 / ISP 어댑터 있음" : "센서 실행 엔진 미설정"}</small>}</div><div className="workspace-task-actions"><a className="button primary" href={latestSensor ? resultRoute(latestSensor, project) : `#sensor-sim?${scoped}`}>센서 영상 Viewer<ArrowRight size={15} /></a>{latestFeedback && <a className="button secondary" href={resultRoute(latestFeedback, project)}>Feedback 주행 영상 재생</a>}{carlaRoad && <a className="button secondary" href={resultRoute(carlaRoad, project)}>CARLA 도로영상 재생</a>}{canExecute && <><a className="button secondary" href={`#sensor-sim?${scoped}&mode=author&kind=sensor_capture&adapter=carla-feedback-sensor`}>Feedback 주행 · 카메라 실행</a><a className="button secondary" href={`#sensor-sim?${scoped}&mode=author`}>센서 실험 구성</a></>}</div><small>결과마다 native 계산·CARLA RGB·reference 등 생성 방식과 한계를 표시합니다.</small>{carlaResults.length > 0 && <small>{carlaResults.length}건의 기존 CARLA 실행을 명시적으로 연결했습니다. 기존 3건은 정지 자차이며 feedback 주행 결과와 구분합니다.</small>}</article>
      <article className="workspace-task"><Cpu size={26} /><h2>HIL Workbench</h2><p>객체 입력에 지연·단절을 주입하고 Software DUT의 판단과 CARLA 차량 반응을 원본 영상·신호로 비교합니다.</p><div className="workspace-task-state">{countLabel(["hil_demo"])}</div><div className="workspace-task-actions"><a className="button primary" href={`#hil-sim?${scoped}`}>고장 주입 A/B 데모<ArrowRight size={15} /></a></div><small>Software DUT · CARLA Simulation. 실제 ECU·물리 센서 검증은 후속 범위입니다.</small></article>
      <article className="workspace-task"><Film size={26} /><h2>Log Sim · 로그에서 비교까지</h2><p>영상과 객체를 함께 보며 사건을 찾고 Case로 저장합니다. 앞차의 정지 시간을 바꿔 재실행하고 원본과 나란히 비교합니다.</p><div className="workspace-task-state">CARLA 조건 변경 · 실도로 원본 조사</div><div className="workspace-task-actions"><a className="button primary" href={`#log-sim?${scoped}`}>로그 조사·변경·비교<ArrowRight size={15} /></a><a className="button secondary" href={`#replay-specs?${scoped}`}>Replay Spec 상세 설정</a></div><small>CARLA는 원본 시나리오부터 다시 실행합니다. 실도로 데이터는 원본 영상·주석 조사용입니다.</small></article>
      <article className="workspace-task"><ShieldCheck size={26} /><h2>검증 계획 · 근거</h2><p>검증 조건과 기준을 정의하고 실행 결과를 연결합니다. 데이터 부족·미평가·실패와 다음 작업을 구분합니다.</p><div className="workspace-task-actions"><a className="button secondary" href="#validation">검증 계획 열기</a><a className="button secondary" href={`#viewers?${scoped}`}>모든 결과 Viewer</a></div><small>실행이 완료돼도 기준 통과나 실측 검증이 자동으로 성립하지 않습니다.</small></article>
    </section>
    <section className="workspace-results" aria-label="Recent workspace results"><div className="workspace-section-heading"><div><span className="eyebrow">SAVED EVIDENCE</span><h2>최근 실행 · 클릭해서 Viewer 열기</h2></div><a className="button secondary" href={`#viewers?${scoped}`}><Workflow size={16} />전체 결과</a></div>
      {executions.loading && <p>저장 결과를 확인하고 있습니다…</p>}
      {!executions.loading && !executions.error && !items.length && <p>아직 이 작업공간의 실행 결과가 없습니다. 시나리오를 저장하고 실행을 제출하거나 기존 실험 결과가 있는 작업공간을 선택하세요.</p>}
      {items.slice(0, 8).map(item => <a className="workspace-result-row" href={resultRoute(item, project)} key={item.id}><span className="workspace-result-kind">{kindNames[item.experiment_kind]}</span><div><strong>{item.name}</strong><small>{item.fidelity ?? "생성 방식은 실행 상세에서 확인"} · {new Date(item.created_at).toLocaleString()}<br />{item.id}{retainedExecutionSource(item.id, item.manifest_hash) && <><br />{retainedExecutionSource(item.id, item.manifest_hash)}</>}</small></div><Badge status={item.status} /><ArrowRight size={17} /></a>)}
      {executions.data?.next_before_seq && <p className="workspace-limit">최근 100건을 조회했습니다. 전체 결과 Viewer에서 이전 기록을 더 불러올 수 있습니다.</p>}
    </section>
  </>;
}
