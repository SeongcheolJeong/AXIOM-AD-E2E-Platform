import Experiments from "./Experiments";
import SensorDemoWorkspace from "../components/sensor-demo/SensorDemoWorkspace";
import SensorLayoutWorkspace from "../components/sensor-layouts/SensorLayoutWorkspace";
import { useResource } from "../api";
import "./sensor-sim.css";

export default function SensorSim() {
  const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const layout = query.get("mode") === "layout";
  const demo = !layout && query.get("mode") !== "author" && query.get("mode") !== "results" && !query.get("execution");
  const projects = useResource<{ items: { id: string; name: string }[] }>("/projects");
  const authoring = query.get("mode") === "author";
  const feedback = authoring && query.get("adapter") === "carla-feedback-sensor";
  const project = query.get("project") ?? "default";
  return <div className="sensor-sim-workbench">
    <nav className="sensor-workbench-tabs" aria-label="Sensor Sim workspace">
      <a className={layout ? "selected" : ""} href={`#sensor-sim?project=${encodeURIComponent(project)}&mode=layout`}>센서 배치 · Beta</a>
      <a className={demo ? "selected" : ""} href={`#sensor-sim?project=${encodeURIComponent(project)}&mode=demo`}>빠른 시작 · A/B 데모</a>
      <a className={!authoring && !demo && !layout ? "selected" : ""} href={`#sensor-sim?project=${encodeURIComponent(project)}&mode=results`}>저장 결과 보기 · Viewer</a>
      <a className={authoring && !feedback ? "selected" : ""} href={`#sensor-sim?project=${encodeURIComponent(project)}&mode=author&kind=sensor_capture&adapter=camera-e2e-lens-sensor-scene`}>센서 설정 작성 · 실행</a>
      <a className={feedback ? "selected" : ""} href={`#sensor-sim?project=${encodeURIComponent(project)}&mode=author&kind=sensor_capture&adapter=carla-feedback-sensor`}>Feedback 주행 · 카메라 실행</a>
      <a href="#viewers">모든 Viewer</a>
    </nav>
    {(demo || layout) && <div className="sensor-demo-project"><label>프로젝트<select aria-label="Sensor Sim 프로젝트" value={project} onChange={e => { location.hash = `#sensor-sim?project=${encodeURIComponent(e.target.value)}&mode=${layout ? "layout" : "demo"}`; }}>{!projects.data?.items.some(p => p.id === project) && <option value={project}>{project}</option>}{projects.data?.items.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><span>{layout ? "고정된 기준 A · 장착 배치 B · 저장 개정별 비교" : "고정된 장면 · 한 가지 센서 변경 · 실제 엔진 A/B"}</span></div>}
    {layout && <SensorLayoutWorkspace key={project} project={project}/>}
    {demo && <SensorDemoWorkspace key={project} project={project}/>}
    {authoring && <section className="sensor-workbench-intro"><h2>{feedback ? "제어에 반응한 자차 · 새 센서 영상 시험" : "센서 시험 시작하기"}</h2><ol><li>{feedback ? "CARLA reference 제어기·목표 속도·카메라와 2–6초 실행 범위를 고정합니다." : "지원 어댑터와 렌즈·센서 설정을 선택합니다."}</li><li>입력과 계산 범위를 확인하고 Preview workload를 누릅니다.</li><li>고정 실험 저장 → 실행 제출 → 저장 결과 Viewer에서 확인합니다.</li></ol><p>{feedback ? "f의 계획·명령과 f+1의 실제 자차·카메라를 함께 재생합니다. 카메라 입력 학습형 모델과 물리 센서 검증은 포함하지 않습니다." : "어댑터별로 지원 범위가 다릅니다. CameraE2E 중앙 경사 에지 모델과 CARLA 출력은 실행 기록의 fidelity로 구분합니다."}</p></section>}
    {!demo && !layout && <Experiments displayMode={authoring ? "author" : "results"} kindFilter={["sensor_capture", "isp_compare"]}
      title={authoring ? "Sensor Sim · 설정 작성과 실행" : "Sensor Sim · 저장 결과 Viewer"}
      description={authoring ? "지원하는 주행·카메라·렌즈·센서·ISP 입력을 고정하고 작업량을 확인한 뒤 명시적으로 실행합니다." : "CARLA feedback 주행의 연속 RGB·계획·실제 궤적·제어값과 렌즈/센서 ISP·RAW 원본을 확인합니다. 완료 결과와 실패 기록을 함께 남깁니다."} />}
  </div>;
}
