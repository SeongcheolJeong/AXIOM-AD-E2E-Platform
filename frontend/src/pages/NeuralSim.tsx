import { useResource } from "../api";
import NeuralWorkspace from "../components/neural-demo/NeuralWorkspace";
import "../components/neural-demo/neural-demo.css";

export default function NeuralSim() {
  const project = new URLSearchParams(location.hash.split("?")[1] ?? "").get("project") ?? "default";
  const projects = useResource<{ items: { id: string; name: string }[] }>("/projects");
  return <div className="neural-workbench"><div className="neural-project"><label>프로젝트<select aria-label="Neural 프로젝트" value={project} onChange={event => { location.hash = `#neural-sim?project=${encodeURIComponent(event.target.value)}`; }}>{!projects.data?.items.some(item => item.id === project) && <option value={project}>{project}</option>}{projects.data?.items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><span>원본 로그 · Neural 3D 재구성 · 새로운 카메라 시점</span></div><NeuralWorkspace key={project} project={project}/></div>;
}
