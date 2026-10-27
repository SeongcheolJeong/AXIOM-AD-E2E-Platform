import { useEffect, useRef, useState } from "react";
import { authHeaders, useResource } from "../../api";
import type { ObjectExecution } from "../object-scene/object-scene";
import type { CityFrame } from "./city-scene";
import { bytesSha256, mapSourceWrite } from "../external-maps/mapSourcesApi";

type CapturedCase = { id: string; revision: number; title: string; note: string; manifest_hash: string; segment_hash: string; segment: { recording_id: string; recording_sha256: string; sample_count: number }; object_sim_evidence: { execution_id: string; frame_index: number; time_s: number; scene_sha256: string; map_ref: unknown; scenario_ref: unknown; external_map_provenance: unknown } };

export default function CityCaseCapture({ execution, frame, onOpenFrame }: { execution: ObjectExecution; frame: CityFrame; onOpenFrame: (index: number) => void }) {
  const initial = useRef(new URLSearchParams(location.hash.split("?")[1] ?? ""));
  const [caseId, setCaseId] = useState(initial.current.get("execution") === execution.id ? initial.current.get("scene_case") ?? "" : "");
  const [revision, setRevision] = useState(Number(initial.current.get("scene_case_revision") ?? 1));
  const [title, setTitle] = useState(""), [note, setNote] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const identity = useResource<{ role: string; mode: string }>("/team/whoami");
  const writable = !!identity.data && (identity.data.mode !== "project" || ["Engineer", "Admin"].includes(identity.data.role));
  const root = `/object-sim/executions/${encodeURIComponent(execution.id)}/cases`;
  const query = new URLSearchParams({ project_id: execution.project_id, revision: String(revision) });
  const saved = useResource<CapturedCase>(caseId && Number.isSafeInteger(revision) && revision > 0 ? `${root}/${encodeURIComponent(caseId)}?${query}` : null);
  const scene = execution.artifacts.filter(item => item.name === "city-scene.json");
  const restored = useRef("");
  useEffect(() => {
    if (!saved.data || saved.data.object_sim_evidence.execution_id !== execution.id) return;
    const pin = `${execution.id}:${saved.data.id}:${saved.data.revision}`;
    if (restored.current === pin) return;
    restored.current = pin;
    onOpenFrame(saved.data.object_sim_evidence.frame_index);
  }, [saved.data, execution.id, onOpenFrame]);
  useEffect(() => {
    const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
    setCaseId(params.get("execution") === execution.id ? params.get("scene_case") ?? "" : "");
    setRevision(Number(params.get("scene_case_revision") ?? 1));
    setError(""); setTitle(""); setNote("");
  }, [execution.id]);
  async function capture() {
    if (busy || !writable || execution.status !== "completed" || scene.length !== 1 || !execution.manifest_hash) return;
    setBusy(true); setError("");
    try {
      const value = await mapSourceWrite<CapturedCase>(root, { project_id: execution.project_id,
        execution_manifest_hash: execution.manifest_hash, scene_sha256: scene[0].sha256,
        frame_index: frame.index, time_s: frame.time_s, title: title.trim(), note: note.trim() });
      setCaseId(value.id); setRevision(value.revision); saved.reload();
      const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
      params.set("project", execution.project_id); params.set("execution", execution.id);
      params.set("scene_case", value.id); params.set("scene_case_revision", String(value.revision));
      history.replaceState(null, "", `${location.pathname}${location.search}#object-sim?${params}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  async function download() {
    if (!saved.data || busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/v1${root}/${encodeURIComponent(saved.data.id)}/export?${query}`, { headers: authHeaders() });
      if (!response.ok) throw new Error(`Case 내보내기 실패 (HTTP ${response.status})`);
      const bytes = await response.arrayBuffer();
      if (await bytesSha256(bytes) !== saved.data.manifest_hash) throw new Error("Case 내려받기 SHA-256이 저장 개정과 다릅니다.");
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/json" }));
      const link = document.createElement("a"); link.href = url; link.download = `${saved.data.id}-r${saved.data.revision}.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  return <section className="city-panel" aria-label="Object Sim Case 저장">
    <h3>선택한 원본 시점을 Case로 저장</h3><p>현재 프레임 {frame.index} · 시뮬레이션 {frame.time_s.toFixed(3)}s. 봉인된 장면의 객체 상태를 좌표 변환 없이 기록한 조사 Case입니다. 영상 재생·센서 측정·안전 판정은 원래 실행 범위를 따릅니다.</p>
    <div className="city-toolbar"><label>Case 제목<input aria-label="Object Case 제목" value={title} maxLength={180} disabled={busy || !writable} onChange={event => setTitle(event.target.value)}/></label><label>조사 메모<textarea aria-label="Object Case 조사 메모" value={note} maxLength={4000} disabled={busy || !writable} onChange={event => setNote(event.target.value)}/></label><button className="button secondary" disabled={busy || !writable || !title.trim() || !note.trim() || scene.length !== 1 || execution.status !== "completed"} onClick={() => void capture()}>{busy ? "Case 처리 중…" : "선택 프레임 Case 저장"}</button></div>
    {(error || saved.error || identity.error) && <p className="expansion-error" role="alert">{error || saved.error || identity.error}</p>}
    {saved.data && <div><h4>{saved.data.title} · r{saved.data.revision}</h4><p>{saved.data.note}</p><p>저장 프레임 {saved.data.object_sim_evidence.frame_index} · {saved.data.object_sim_evidence.time_s.toFixed(3)}s · 원본 JSON 메시지 {saved.data.segment.sample_count}개</p><div className="city-toolbar"><button className="button secondary" disabled={busy} onClick={() => void download()}>봉인 Case 내려받기</button><a className="button secondary" href={`#object-sim?${new URLSearchParams({ project: execution.project_id, execution: execution.id, scene_case: saved.data.id, scene_case_revision: String(saved.data.revision) })}`}>이 Case와 실행 다시 열기</a></div><details><summary>Case · 시나리오 · 원본 지도 계보</summary><pre className="expansion-json">{JSON.stringify({ case_id: saved.data.id, revision: saved.data.revision, manifest_hash: saved.data.manifest_hash, segment_hash: saved.data.segment_hash, evidence: saved.data.object_sim_evidence }, null, 2)}</pre></details></div>}
  </section>;
}
