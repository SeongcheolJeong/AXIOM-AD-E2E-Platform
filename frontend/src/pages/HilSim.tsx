import VirtualEcuLab from "../components/vecu/VirtualEcuLab";
import EngineeringLab from "../components/engineering-lab/EngineeringLab";
import ApolloLab from "../components/apollo-lab/ApolloLab";
import FpgaWorkbench from "../components/workbench/FpgaWorkbench";
import RtlLab from "../components/rtl-lab/RtlLab";
import EcuLab from "../components/ecu-lab/EcuLab";
import FpgaLab from "../components/fpga-lab/FpgaLab";
import { useResource } from "../api";
import HilWorkspace from "../components/hil-demo/HilWorkspace";
import HilBetaWorkspace from "../components/hil-demo/HilBetaWorkspace";
import "../components/hil-demo/hil-demo.css";

export default function HilSim() {
  const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const project = query.get("project") ?? "default";
  const vecu = query.get("mode") === "vecu";
  const fpga = query.get("mode") === "fpga";
  const apollo = fpga && query.get("target") === "apollo-sm8750";
  const engineering = fpga && query.get("target") === "engineering-lab";
  const workbench = fpga && query.get("target") === "fpga-workbench";
  const rtl = fpga && query.get("target") === "rtl-fifo";
  const ecu = fpga && query.get("target") === "sa8650";
  const beta = !vecu && !fpga && (query.get("mode") === "beta" || query.has("test"));
  const projects = useResource<{ items: { id: string; name: string }[] }>(
    "/projects",
  );
  return (
    <div className="hil-workbench">
      <div className="hil-project">
        <label>
          프로젝트
          <select
            aria-label="HIL 프로젝트"
            value={project}
            onChange={(event) => {
              location.hash = `#hil-sim?project=${encodeURIComponent(event.target.value)}${vecu ? "&mode=vecu" : fpga ? `&mode=fpga${engineering ? "&target=engineering-lab" : workbench ? "&target=fpga-workbench" : rtl ? "&target=rtl-fifo" : apollo ? "&target=apollo-sm8750" : ecu ? "&target=sa8650" : ""}` : beta ? "&mode=beta" : ""}`;
            }}
          >
            {!projects.data?.items.some((item) => item.id === project) && (
              <option value={project}>{project}</option>
            )}
            {projects.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <span>{vecu ? "호스트 ECU 소프트웨어 · 고정 입력 재생 / CARLA 폐루프" : engineering ? "사용자 RTL · SPICE 회로 · 전용 런타임 연결" : workbench ? "블록 배선 · 가상 계측기 · 실제 RTL 실행" : rtl ? "FIFO RTL · SimPy 참조 · 클록별 A/B 검증" : fpga ? "카메라 RAW · FPGA 버퍼 · 동작/타이밍 모델" : "별도 프로세스 Software DUT · 객체 입력 · CARLA 폐루프"}</span>
        <nav className="hil-mode-tabs" aria-label="HIL 작업 모드">
          <a className={vecu ? "selected" : ""} href={`#hil-sim?project=${encodeURIComponent(project)}&mode=vecu`}>Virtual ECU Lab</a>
          <a
            className={beta ? "selected" : ""}
            href={`#hil-sim?project=${encodeURIComponent(project)}&mode=beta`}
          >
            시험 관리
          </a>
          <a
            className={!vecu && !beta && !fpga ? "selected" : ""}
            href={`#hil-sim?project=${encodeURIComponent(project)}`}
          >
            빠른 데모
          </a>
          <a className={fpga ? "selected" : ""} href={`#hil-sim?project=${encodeURIComponent(project)}&mode=fpga`}>FPGA Lab</a>
        </nav>
      </div>
      {vecu ? <VirtualEcuLab key={project} project={project} /> : fpga ? <><nav className="ecu-mode-tabs" aria-label="FPGA Lab 모델"><a className={!ecu && !apollo && !rtl && !workbench && !engineering ? "selected" : ""} href={`#hil-sim?project=${encodeURIComponent(project)}&mode=fpga`}>FPGA 송신 경로</a><a className={ecu ? "selected" : ""} href={`#hil-sim?project=${encodeURIComponent(project)}&mode=fpga&target=sa8650`}>SA8650 Target ECU</a><a className={apollo ? "selected" : ""} href={`#hil-sim?project=${encodeURIComponent(project)}&mode=fpga&target=apollo-sm8750`}>Apollo SM8750</a><a className={rtl ? "selected" : ""} href={`#hil-sim?project=${encodeURIComponent(project)}&mode=fpga&target=rtl-fifo`}>RTL A/B</a><a className={workbench ? "selected" : ""} href={`#hil-sim?project=${encodeURIComponent(project)}&mode=fpga&target=fpga-workbench`}>FPGA Workbench</a><a className={engineering ? "selected" : ""} href={`#hil-sim?project=${encodeURIComponent(project)}&mode=fpga&target=engineering-lab`}>Engineering Lab</a></nav>{engineering ? <EngineeringLab key={project} project={project} /> : workbench ? <FpgaWorkbench key={project} project={project} /> : rtl ? <RtlLab key={project} project={project} /> : apollo ? <ApolloLab key={project} project={project} /> : ecu ? <EcuLab key={project} project={project} /> : <FpgaLab key={project} project={project} />}</> : beta ? (
        <HilBetaWorkspace key={project} project={project} />
      ) : (
        <HilWorkspace key={project} project={project} />
      )}
    </div>
  );
}
