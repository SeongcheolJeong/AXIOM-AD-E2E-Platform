import { useEffect, useMemo, useRef, useState } from "react";
import { Download, FileUp, MapPinned, RefreshCw } from "lucide-react";
import { api, formatBytes, useResource } from "../../api";
import type { CatalogRef } from "../city-sim/city-contract";
import { convertMapSource, downloadMapOriginal, mapSourceWrite, publishMapSource, qualifyMapSource, uploadMapSource } from "./mapSourcesApi";
import { executionStatusNames, hasMatchingNativeQualification, isPendingMapExecution, mapSourceRoute, mapUploadError, publishedMapRoute, readSourceResume, sameSourceRef, sourceKindNames, sourceRef } from "./map-source-contract";
import type { MapConversion, MapDiagnostic, MapExecution, SourceCapabilities, SourceDetail, SourceKind, SourceRecord, SourceRights } from "./map-source-contract";
import "./external-maps.css";

const blankRights: SourceRights = { license: "", source_url: "", redistribution: "unknown", notes: "" };
function errorText(value: unknown) { return value instanceof Error ? value.message : String(value); }
function json(value: unknown) { return JSON.stringify(value, null, 2); }
function RawDetails({ title, value }: { title: string; value: unknown }) {
  return <details className="external-map-raw"><summary>{title}</summary><pre>{json(value ?? null)}</pre></details>;
}
function SourceDeclaredContext({ metadata }: { metadata: Record<string, unknown> }) {
  const upstream = metadata.upstream_conversion && typeof metadata.upstream_conversion === "object" && !Array.isArray(metadata.upstream_conversion) ? metadata.upstream_conversion as Record<string, unknown> : null;
  const flags = [
    ...(Array.isArray(metadata.inference_flags) ? metadata.inference_flags.map(flag => ({ flag, origin: "" })) : []),
    ...(Array.isArray(upstream?.inference_flags) ? upstream.inference_flags.map(flag => ({ flag, origin: "원본 제작 변환: " })) : []),
  ];
  const fidelity = typeof metadata.fidelity === "string" ? metadata.fidelity : "";
  if (!flags.length && !fidelity && !metadata.provider && !metadata.coordinate_transform && !upstream) return null;
  return <div className="external-map-inference" aria-label="원본에 기록된 가공·가정">
    <b>원본 등록 시 기록한 가공·가정</b>
    {typeof metadata.provider === "string" && <p>원본 제공: {metadata.provider}</p>}
    {fidelity && <p><strong>{fidelity === "osm_derived_procedural_roads_not_surveyed_hd_map" ? "OSM 도로에서 생성한 형상 · 측량 정밀도로지도가 아닙니다." : fidelity}</strong></p>}
    {!!flags.length && <ul className="external-map-source-flags">{flags.map(({ flag, origin }, index) => <li key={index}>{origin}{typeof flag === "string" ? flag : flag && typeof flag === "object" ? String(flag.message ?? flag.description ?? flag.kind ?? json(flag)) : json(flag)}</li>)}</ul>}
    <p>원본 제공자·업로더가 등록 당시 기록한 정보입니다. 이 등록 정보만으로 도로 정확도나 주행 가능성이 검증되지는 않습니다. native_validation 등 메타데이터는 등록 당시 선언이며, 현재 CARLA 확인 상태는 아래 실행 기록을 기준으로 합니다.</p>
    <RawDetails title="원본 가공·가정과 좌표 변환 보기" value={{ inference_flags: metadata.inference_flags, coordinate_transform: metadata.coordinate_transform, upstream_conversion: upstream, fidelity }}/>
  </div>;
}
function Diagnostics({ items }: { items: MapDiagnostic[] }) {
  if (!items.length) return <p className="external-map-muted">변환기에서 추가 진단을 반환하지 않았습니다.</p>;
  return <ul className="external-map-diagnostics">{items.map((item, index) => <li key={`${item.code}:${index}`} data-severity={item.severity}><b>{item.severity === "error" ? "변환 차단" : item.severity === "warning" ? "확인 필요" : "정보"}</b><div><span>{item.message}</span><small>{item.code}{item.source_id ? ` · 원본 ${String(item.source_id)}` : ""}</small></div></li>)}</ul>;
}

export default function ExternalMapLibrary({ project, onUseMap }: { project: string; onUseMap: (map: CatalogRef) => void }) {
  const initial = useRef(readSourceResume(location.hash));
  const [selectedId, setSelectedId] = useState(initial.current.id);
  const [selectedRevision, setSelectedRevision] = useState<number | null>(initial.current.revision);
  const [expectedHash, setExpectedHash] = useState<string | null>(initial.current.hash);
  const [conversionId, setConversionId] = useState(initial.current.conversionId);
  const [expectedConversion, setExpectedConversion] = useState<CatalogRef | null>(initial.current.conversionId && initial.current.conversionHash ? { id: initial.current.conversionId, revision: initial.current.conversionRevision, sha256: initial.current.conversionHash } : null);
  const [executionId, setExecutionId] = useState(initial.current.executionId);
  const [liveExecution, setLiveExecution] = useState<MapExecution | null>(null);
  const [executionError, setExecutionError] = useState("");
  const [executionRefresh, setExecutionRefresh] = useState(0);
  const [error, setError] = useState(initial.current.error), [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [file, setFile] = useState<File | null>(null), [name, setName] = useState("");
  const [kind, setKind] = useState<SourceKind>("opendrive"), [rights, setRights] = useState<SourceRights>({ ...blankRights });
  const [metadataText, setMetadataText] = useState("{}"), [newRevision, setNewRevision] = useState(false);
  const [selectedLinks, setSelectedLinks] = useState(""), [sourceCrs, setSourceCrs] = useState(""), [targetCrs, setTargetCrs] = useState("");
  const [allow2d, setAllow2d] = useState(false), [advancedOptions, setAdvancedOptions] = useState("{}");
  const fileInput = useRef<HTMLInputElement>(null);
  const query = `project_id=${encodeURIComponent(project)}`;
  const catalogue = useResource<{ items: SourceRecord[] }>(`/map-sources?${query}`);
  const capabilities = useResource<SourceCapabilities>(`/map-sources/capabilities?${query}`);
  const identity = useResource<{ role: string; mode: string }>("/team/whoami");
  const detail = useResource<SourceDetail>(selectedId && selectedRevision ? `/map-sources/${encodeURIComponent(selectedId)}?${query}&revision=${selectedRevision}` : null);
  const revisions = useResource<{ items: SourceRecord[] }>(selectedId ? `/map-sources/${encodeURIComponent(selectedId)}/revisions?${query}` : null);
  const writable = !!identity.data && (identity.data.mode !== "project" || ["Engineer", "Admin"].includes(identity.data.role));
  const sourceMismatch = detail.data && (detail.data.project_id !== project || detail.data.id !== selectedId || detail.data.revision !== selectedRevision || expectedHash && detail.data.sha256 !== expectedHash);
  const source = !sourceMismatch ? detail.data : null;
  const conversions = source?.conversions?.filter(item => sameSourceRef(item.source_ref, source)) ?? [];
  const conversion = conversions.find(item => item.id === conversionId && (!expectedConversion || sameSourceRef(item, expectedConversion))) ?? (!conversionId ? conversions.at(-1) : null);
  const execution = liveExecution?.id === executionId ? liveExecution : source?.executions?.find(item => item.id === executionId);
  const activeExecution = execution && isPendingMapExecution(execution);
  const sameConversionMissing = !!source && !!conversionId && !conversion;
  const latestRevision = Math.max(0, ...(revisions.data?.items ?? []).map(item => item.revision), ...(catalogue.data?.items ?? []).filter(item => item.id === selectedId).map(item => item.revision));
  const uploadProblem = mapUploadError(file, kind, capabilities.data?.max_upload_bytes ?? 0);
  const canConvert = !!source && !!capabilities.data?.conversion[source.source_kind]?.available && writable && !busy && !detail.error;
  const canQualify = !!source && !!conversion?.ready && !!capabilities.data?.qualification.configured && writable && !busy && (!executionId || !!execution) && !activeExecution && !executionError && !detail.error;
  // A completed queue job is never presented as native readiness. The server is
  // the authority for matching receipt evidence and the final publish decision.
  const nativeVerified = hasMatchingNativeQualification(execution, source, conversion);
  const canRequestPublish = nativeVerified && !!conversion?.ready && writable && !busy && !executionError && !detail.error;
  const inferred = useMemo(() => Array.isArray(conversion?.inference_flags) ? conversion.inference_flags : [], [conversion]);

  function replaceRoute(value?: SourceRecord, converted?: MapConversion | null, execution?: string) {
    history.replaceState(null, "", `${location.pathname}${location.search}${mapSourceRoute(project, value ? sourceRef(value) : undefined, converted ? sourceRef(converted) : undefined, execution)}`);
  }
  function openSource(value: SourceRecord) {
    setSelectedId(value.id); setSelectedRevision(value.revision); setExpectedHash(value.sha256);
    setConversionId(""); setExpectedConversion(null); setExecutionId(""); setLiveExecution(null); setExecutionError("");
    setError(""); setNotice(""); setNewRevision(false); replaceRoute(value);
  }
  function chooseConversion(value: MapConversion) {
    if (!source) return;
    setConversionId(value.id); setExpectedConversion(sourceRef(value)); setExecutionId(""); setLiveExecution(null); setExecutionError(""); replaceRoute(source, value);
  }
  function chooseExecution(id: string) {
    setExecutionId(id); setLiveExecution(null); setExecutionError("");
    if (source) replaceRoute(source, conversion, id);
  }
  async function perform(label: string, work: () => Promise<void>) {
    if (busy) return;
    setBusy(label); setError(""); setNotice("");
    try { await work(); } catch (reason) { setError(errorText(reason)); } finally { setBusy(""); }
  }
  function refresh() { catalogue.reload(); capabilities.reload(); detail.reload(); revisions.reload(); identity.reload(); setExecutionRefresh(value => value + 1); }
  useEffect(() => {
    if (!executionId || !source) return;
    let active = true, timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const value = await api<MapExecution>(`/experiments/executions/${encodeURIComponent(executionId)}`);
        if (!active) return;
        if (value.id !== executionId || value.project_id && value.project_id !== project) throw new Error("실행의 프로젝트·식별자가 선택한 지도와 다릅니다.");
        setLiveExecution(value); setExecutionError("");
        if (isPendingMapExecution(value)) timer = setTimeout(() => void poll(), 2500);
      } catch (reason) { if (active) { setExecutionError(errorText(reason)); setLiveExecution(null); } }
    };
    void poll();
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [executionId, project, source?.id, source?.revision, executionRefresh]);
  const previousStatus = useRef<string | null>(null);
  useEffect(() => {
    if (liveExecution && previousStatus.current !== liveExecution.status && !isPendingMapExecution(liveExecution)) detail.reload();
    previousStatus.current = liveExecution?.status ?? null;
  }, [liveExecution?.status]);
  async function upload(event: React.FormEvent) {
    event.preventDefault();
    await perform("upload", async () => {
      if (!file || !capabilities.data || uploadProblem) throw new Error(uploadProblem || "지원 형식을 먼저 확인하세요.");
      const metadata: unknown = JSON.parse(metadataText);
      if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) throw new Error("원본 메타데이터는 JSON 객체여야 합니다.");
      if (newRevision && (!source || !latestRevision)) throw new Error("개정을 추가할 원본을 선택하세요.");
      const value = await uploadMapSource(file, { project_id: project, ...(newRevision && source ? { id: source.id } : {}), expected_revision: newRevision ? latestRevision : 0, name: name.trim(), source_kind: kind, rights, metadata: metadata as Record<string, unknown> });
      openSource(value); catalogue.reload(); revisions.reload(); detail.reload(); setFile(null); if (fileInput.current) fileInput.current.value = "";
      setNotice(`원본 r${value.revision} 등록 완료 · ${formatBytes(value.size_bytes)} · 파일 SHA-256 확인`);
    });
  }
  async function convert() {
    await perform("convert", async () => {
      if (!source) return;
      const options: unknown = JSON.parse(advancedOptions);
      if (!options || typeof options !== "object" || Array.isArray(options)) throw new Error("추가 변환 옵션은 JSON 객체여야 합니다.");
      const ngii = source.source_kind === "ngii_shp" ? {
        ...(selectedLinks.trim() ? { selected_link_ids: selectedLinks.split(/[\s,]+/).filter(Boolean) } : {}),
        ...(sourceCrs.trim() ? { source_crs: sourceCrs.trim() } : {}),
        ...(targetCrs.trim() ? { target_crs: targetCrs.trim() } : {}),
        allow_2d: allow2d,
      } : {};
      const value = await convertMapSource(project, sourceRef(source), { ...options, ...ngii });
      if (!sameSourceRef(value.source_ref, source)) throw new Error("변환 결과의 원본 개정·해시가 요청과 다릅니다.");
      chooseConversion(value); detail.reload(); setNotice(value.ready ? "변환 진단 완료 · 실제 CARLA 확인은 아직 필요합니다." : "변환이 보류되었습니다. 아래 원본별 진단을 확인하세요.");
    });
  }
  async function qualify() {
    await perform("qualify", async () => {
      if (!source || !conversion?.ready) return;
      const value = await qualifyMapSource(project, sourceRef(source), sourceRef(conversion));
      setExecutionId(value.id); setLiveExecution(value); setExecutionError(""); replaceRoute(source, conversion, value.id); detail.reload();
      setNotice("CARLA 지도 확인 요청을 접수했습니다. 완료 여부는 실행 근거로 확인합니다.");
    });
  }
  async function publish() {
    await perform("publish", async () => {
      if (!source || !conversion || !execution) return;
      const value = await publishMapSource(project, sourceRef(source), sourceRef(conversion), execution.id);
      if (!value.map_ref?.id || !value.map_ref.sha256 || !Number.isSafeInteger(value.map_ref.revision)) throw new Error("게시된 지도 개정 정보를 확인할 수 없습니다.");
      detail.reload(); catalogue.reload(); setNotice(`실행 지도 r${value.map_ref.revision} 게시 완료. 아래에서 시나리오를 작성하세요.`);
    });
  }
  async function cancelQualification() {
    await perform("cancel", async () => {
      if (!execution || !isPendingMapExecution(execution) || !execution.cancellation_supported) return;
      await mapSourceWrite(`/experiments/executions/${encodeURIComponent(execution.id)}/cancel`, {});
      setExecutionRefresh(value => value + 1); setNotice("취소를 요청했습니다. 실행 종료·정리 상태를 확인하세요.");
    });
  }
  const failures = [error, catalogue.error, capabilities.error, identity.error, detail.error, revisions.error, executionError, sourceMismatch ? "요청한 원본의 프로젝트·개정·해시가 응답과 다릅니다. 결과를 표시하지 않습니다." : "", sameConversionMissing ? "요청한 변환 개정·해시를 이 원본에서 찾지 못했습니다. 다른 변환 결과로 대신 표시하지 않습니다." : ""].filter(Boolean);

  return <section className="external-map-library" aria-label="한국·외부 지도 라이브러리">
    <header className="external-map-heading"><div><span className="external-map-eyebrow">SOURCE → CONVERT → NATIVE CHECK → SCENARIO</span><h2>한국·외부 지도 라이브러리</h2><p>가지고 있는 원본 지도를 등록하고, 확인된 개정으로 시나리오를 만드세요.</p></div><button className="button secondary" onClick={refresh} disabled={!!busy}><RefreshCw size={16}/>상태 다시 확인</button></header>
    <div className="external-map-boundary"><MapPinned size={18}/><p><b>변환 성공과 실제 실행 가능은 다릅니다.</b> CARLA 확인은 지도 로드·차로 읽기 범위입니다. 주행 성공·센서 정확도·실제 도로의 최신성은 보장하지 않습니다.</p></div>
    {failures.length > 0 && <div className="external-map-errors" role="alert">{Array.from(new Set(failures)).map(message => <p key={message}>{message}</p>)}<button className="button secondary" onClick={refresh} disabled={!!busy}>다시 확인</button></div>}
    {notice && <p className="external-map-notice" role="status">{notice}</p>}
    {!writable && identity.data && <p className="external-map-muted">읽기 권한입니다. 원본 등록·변환·실행·게시에는 Engineer 권한이 필요합니다.</p>}
    <div className="external-map-grid"><aside>
      <form className="external-map-panel" aria-label="외부 지도 원본 등록" onSubmit={event => void upload(event)}>
        <h3><FileUp size={19}/>원본 등록</h3><p className="external-map-muted">.xodr 또는 SHP 묶음 ZIP의 원본 bytes와 출처를 보존합니다. 지도 구조를 브라우저에서 바꾸지 않습니다.</p>
        <label>원본 종류<select value={kind} aria-label="지도 원본 종류" disabled={!!busy || !writable} onChange={event => setKind(event.target.value as SourceKind)}>{Object.entries(sourceKindNames).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label>지도 이름<input aria-label="외부 지도 이름" value={name} required maxLength={180} disabled={!!busy || !writable} onChange={event => setName(event.target.value)}/></label>
        <label>원본 파일<input ref={fileInput} aria-label="지도 원본 파일" type="file" accept={kind === "opendrive" ? ".xodr" : ".zip"} disabled={!!busy || !writable} onChange={event => { const selected = event.target.files?.[0] ?? null; setFile(selected); if (selected && !name) setName(selected.name.replace(/\.[^.]+$/, "")); }}/></label>
        <p className="external-map-muted">{capabilities.data ? `최대 ${formatBytes(capabilities.data.max_upload_bytes)}` : "업로드 한도 확인 중"}{file ? ` · 선택 ${formatBytes(file.size)}` : ""}{file && uploadProblem ? ` · ${uploadProblem}` : ""}</p>
        <label>라이선스·사용 권한<input aria-label="지도 라이선스" required maxLength={500} value={rights.license} placeholder="예: 원본에 표시된 라이선스" disabled={!!busy || !writable} onChange={event => setRights({ ...rights, license: event.target.value })}/></label>
        <label>출처 URL<input aria-label="지도 출처 URL" type="url" maxLength={2000} value={rights.source_url} placeholder="원본 제공 페이지" disabled={!!busy || !writable} onChange={event => setRights({ ...rights, source_url: event.target.value })}/></label>
        <label>재배포 조건<select aria-label="지도 재배포 조건" value={rights.redistribution} disabled={!!busy || !writable} onChange={event => setRights({ ...rights, redistribution: event.target.value as SourceRights["redistribution"] })}><option value="unknown">미확인</option><option value="permitted">허용 조건 확인</option><option value="restricted">별도 허가·재배포 제한</option></select></label>
        <label>권리·원본 메모<textarea aria-label="지도 권리 메모" maxLength={4000} value={rights.notes} placeholder="표시 의무, 가공 내역, 원본 취득 시점 등" disabled={!!busy || !writable} onChange={event => setRights({ ...rights, notes: event.target.value })}/></label>
        <details><summary>원본 메타데이터 · JSON</summary><label>메타데이터<textarea className="external-map-json-input" aria-label="지도 원본 메타데이터 JSON" value={metadataText} disabled={!!busy || !writable} onChange={event => setMetadataText(event.target.value)}/></label></details>
        {source && <label className="external-map-checkbox"><input type="checkbox" checked={newRevision} disabled={!!busy || !writable} onChange={event => { setNewRevision(event.target.checked); if (event.target.checked) { setName(source.name); setKind(source.source_kind); setRights({ ...source.rights }); setMetadataText(json(source.metadata)); } }}/>선택 원본의 새 개정으로 등록 (현재 최신 r{latestRevision})</label>}
        <p className="external-map-muted">권리는 사용자가 기록한 선언입니다. 파일 등록이나 지도 게시가 외부 재배포 허가를 뜻하지 않습니다.</p>
        <button className="button" type="submit" disabled={!!busy || !writable || !capabilities.data || !!uploadProblem || !name.trim() || !rights.license.trim()}>{busy === "upload" ? "원본 저장·해시 확인 중…" : "원본 등록"}</button>
      </form>
      <section className="external-map-panel" aria-label="등록 지도 목록"><h3>등록 지도</h3>{catalogue.loading ? <p>목록 확인 중…</p> : !catalogue.error && !catalogue.data?.items.length ? <p className="external-map-muted">이 프로젝트에 등록된 원본이 없습니다.</p> : null}<div className="external-map-list">{catalogue.data?.items.map(item => <button key={`${item.id}:${item.revision}`} className={item.id === selectedId ? "selected" : ""} disabled={!!busy} onClick={() => openSource(item)}><b>{item.name}</b><span>{sourceKindNames[item.source_kind]} · r{item.revision}</span><small>{item.native_ready ? "보존된 CARLA 확인 근거 있음" : "원본 등록 · 실행 상태는 상세에서 확인"}</small></button>)}</div></section>
    </aside><div className="external-map-detail">
      {!selectedId && <div className="external-map-empty"><MapPinned size={36}/><h3>원본 지도에서 시작하세요.</h3><p>등록한 파일을 선택하면 변환 진단, 실제 엔진 확인, 게시된 지도 개정을 이어서 볼 수 있습니다.</p></div>}
      {detail.loading && selectedId && <p className="external-map-muted">선택 원본을 확인하고 있습니다…</p>}
      {source && <>
        <section className="external-map-panel"><div className="external-map-section-title"><div><span className="external-map-eyebrow">01 · ORIGINAL</span><h3>{source.name}</h3></div><button className="button secondary" disabled={!!busy} onClick={() => void perform("download", () => downloadMapOriginal(project, source))}><Download size={16}/>원본 내려받기</button></div>
          <label className="external-map-revision">저장 개정<select aria-label="외부 지도 저장 개정" value={source.revision} disabled={!!busy} onChange={event => { const value = revisions.data?.items.find(item => item.revision === Number(event.target.value)); if (value) openSource(value); }}><option value={source.revision}>r{source.revision} · {source.filename}</option>{revisions.data?.items.filter(item => item.revision !== source.revision).map(item => <option key={item.revision} value={item.revision}>r{item.revision} · {item.filename}</option>)}</select></label>
          <dl className="external-map-facts"><div><dt>원본</dt><dd>{sourceKindNames[source.source_kind]} · {formatBytes(source.size_bytes)}</dd></div><div><dt>파일 SHA-256</dt><dd className="external-map-hash">{source.source_sha256}</dd></div><div><dt>권리</dt><dd>{source.rights.license} · 재배포 {source.rights.redistribution}</dd></div><div><dt>출처</dt><dd>{/^https?:\/\//i.test(source.rights.source_url) ? <a href={source.rights.source_url} target="_blank" rel="noreferrer">{source.rights.source_url}</a> : source.rights.source_url || "등록된 URL 없음"}</dd></div></dl>
          {source.rights.notes && <p className="external-map-muted">{source.rights.notes}</p>}<SourceDeclaredContext metadata={source.metadata}/><RawDetails title="원본 개정·메타데이터" value={{ source_ref: sourceRef(source), metadata: source.metadata, rights: source.rights }}/>
          {!!source.attachments?.length && <details className="external-map-raw"><summary>함께 보존한 원본·변환 자료 ({source.attachments.length})</summary><div className="external-map-published">{source.attachments.map((attachment, index) => <div key={`${index}:${attachment.sha256}`}><b>{attachment.filename}</b><span>{attachment.role} · {formatBytes(attachment.size_bytes)}</span><span className="external-map-hash">{attachment.sha256}</span><button className="button secondary" disabled={!!busy} onClick={() => void perform("download", () => downloadMapOriginal(project, source, index))}>보존 자료 내려받기</button></div>)}</div></details>}
        </section>
        <section className="external-map-panel"><span className="external-map-eyebrow">02 · CONVERSION</span><h3>변환과 원본 진단</h3><p className="external-map-muted">원본에 있는 정보와 사용자가 보완한 값을 분리합니다. 지원하지 않는 구조는 진단으로 남기고 중단합니다.</p>
          {source.source_kind === "ngii_shp" && <div className="external-map-convert-fields"><label>선택 차로 ID<textarea aria-label="변환 선택 차로 ID" value={selectedLinks} placeholder="쉼표 또는 줄바꿈으로 구분 · 비우면 지원 한도 내 전체" disabled={!!busy || !writable} onChange={event => setSelectedLinks(event.target.value)}/></label><div className="external-map-two"><label>원본 좌표계<input aria-label="원본 좌표계" value={sourceCrs} placeholder="PRJ가 없을 때만 직접 지정" disabled={!!busy || !writable} onChange={event => setSourceCrs(event.target.value)}/></label><label>변환 좌표계<input aria-label="변환 좌표계" value={targetCrs} placeholder="필요한 경우 미터 투영 좌표계" disabled={!!busy || !writable} onChange={event => setTargetCrs(event.target.value)}/></label></div><label className="external-map-checkbox"><input type="checkbox" checked={allow2d} disabled={!!busy || !writable} onChange={event => setAllow2d(event.target.checked)}/>고도가 없는 2D 원본을 z=0 평면으로 가정하는 것을 허용</label><p className="external-map-muted">좌표계는 추정하지 않습니다. 평면 허용은 원본 고도 복원이 아니며 보완값으로 기록됩니다.</p><details><summary>고급 변환 옵션 · 명시적 보완</summary><label>추가 옵션 JSON<textarea className="external-map-json-input" aria-label="지도 추가 변환 옵션 JSON" value={advancedOptions} disabled={!!busy || !writable} onChange={event => setAdvancedOptions(event.target.value)}/></label><p className="external-map-muted">local_origin, encoding, lane_width_overrides_m, junction_links 등 지원 옵션만 입력하세요. 차로 폭·교차로 연결의 보완값은 원본 측정값으로 취급하지 않습니다.</p></details></div>}
          <button className="button secondary" onClick={() => void convert()} disabled={!canConvert}>{busy === "convert" ? "변환·진단 중…" : "변환·진단 실행"}</button>{capabilities.data && !capabilities.data.conversion[source.source_kind]?.available && <p className="external-map-muted">이 서비스에서 해당 변환기를 사용할 수 없습니다. {capabilities.data.conversion[source.source_kind]?.reason}</p>}
          {!!conversions.length && <label className="external-map-revision">변환 기록<select aria-label="지도 변환 기록" value={conversion?.id ?? ""} disabled={!!busy} onChange={event => { const item = conversions.find(value => value.id === event.target.value); if (item) chooseConversion(item); }}>{sameConversionMissing && <option value="">요청한 변환을 찾지 못함</option>}{conversions.map(item => <option key={item.id} value={item.id}>{item.id} · {item.ready ? "변환 후보 생성" : "진단·보류"}</option>)}</select></label>}
          {conversion && <div className="external-map-conversion-result"><h4>{conversion.ready ? nativeVerified ? "선택 변환의 CARLA 지도 확인 완료" : "변환 후보 생성 · 실행 확인은 아래에서" : "변환 보류"}</h4><Diagnostics items={conversion.diagnostics ?? []}/><div className="external-map-inference"><b>이번 변환 단계의 추가 보완·가정</b><p>{inferred.length ? `이번 변환에서 보완하거나 가정한 항목 ${inferred.length}개. 원본 등록 시 선언된 가공 정보와 별도로 보존합니다.` : "이번 변환기의 추가 보완·가정 항목 없음. 위 원본 카드의 가공·가정은 그대로 적용되며, 원본 정확성을 검증한 의미는 아닙니다."}</p>{inferred.length > 0 && <RawDetails title="보완·가정 항목 보기" value={conversion.inference_flags}/>}</div><RawDetails title="좌표 변환·변환기·후보 해시" value={{ source_ref: conversion.source_ref, conversion_ref: sourceRef(conversion), coordinate_transform: conversion.coordinate_transform, converter: conversion.converter, candidate_sha256: conversion.candidate_sha256 }}/></div>}
        </section>
        <section className="external-map-panel"><span className="external-map-eyebrow">03 · CARLA QUALIFICATION</span><h3>실제 엔진에서 지도 확인</h3><p className="external-map-muted">큐에 작업을 제출해 실제 CARLA 지도 로드와 차로 읽기를 확인합니다. 어댑터 등록은 worker·엔진 준비 완료를 뜻하지 않습니다.</p><p className="external-map-readiness">{capabilities.data?.qualification.configured ? "확인 어댑터 등록됨 · 현재 worker·엔진은 미확인" : "현재 서비스에 확인 어댑터가 설정되지 않았습니다."}</p>
          <button className="button secondary" disabled={!canQualify} onClick={() => void qualify()}>{busy === "qualify" ? "작업 접수 중…" : "CARLA 지도 확인 요청"}</button>
          {!!source.executions?.length && <label className="external-map-revision">확인 실행<select aria-label="지도 확인 실행" value={executionId} disabled={!!busy} onChange={event => chooseExecution(event.target.value)}><option value="">실행을 선택하세요</option>{source.executions.map(item => <option key={item.id} value={item.id}>{item.id} · {executionStatusNames[item.status] ?? item.status}</option>)}</select></label>}
          {execution && <div className="external-map-native-result"><h4>{executionStatusNames[execution.status] ?? execution.status}</h4><p className="external-map-hash">{execution.id}</p>{nativeVerified && <p className="external-map-notice">선택 원본·변환의 CARLA 지도 로드·차로 읽기를 확인했습니다. 주행 시험은 별도입니다.</p>}{execution.status === "completed" && !nativeVerified && <p className="external-map-execution-error">완료 상태만으로 지도 준비를 인정하지 않습니다. 선택 원본·변환과 일치하는 실제 엔진 확인 근거가 필요합니다.</p>}{execution.error && <p className="external-map-execution-error">{execution.error}</p>}{execution.outcome_uncertain && <p className="external-map-execution-error">실행 결과가 불확실합니다. 게시 전에 실행 상태와 정리를 확인하세요.</p>}<dl className="external-map-facts"><div><dt>실행기</dt><dd>{execution.adapter_id ?? "미제공"} · {execution.adapter_version ?? "버전 미제공"}</dd></div><div><dt>실행 수준</dt><dd>{execution.fidelity ?? "근거 확인 필요"}</dd></div></dl>{activeExecution && execution.cancellation_supported && <button className="button secondary" disabled={!!busy || !writable} onClick={() => void cancelQualification()}>지도 확인 실행 취소</button>}<RawDetails title="실행 응답·원본 근거" value={{ summary: execution.summary, input_hash: execution.input_hash, artifacts: execution.artifacts }}/><a className="external-map-link" href={`#experiments?${new URLSearchParams({ project, execution: execution.id })}`}>실행 기록·산출물 열기 →</a></div>}
        </section>
        <section className="external-map-panel"><span className="external-map-eyebrow">04 · PUBLISH & USE</span><h3>시나리오에서 사용할 지도</h3><p className="external-map-muted">원본·변환·실행이 일치하는 성공 근거를 서버가 확인한 뒤 게시합니다. 게시된 지도는 원본 편집기로 수정하지 않습니다.</p><button className="button" disabled={!canRequestPublish} onClick={() => void publish()}>{busy === "publish" ? "근거 확인·게시 중…" : "확인된 실행 지도로 게시"}</button>{!canRequestPublish && !source.published_maps?.length && <p className="external-map-muted">변환 후보와 성공한 실제 엔진 확인 기록이 필요합니다.</p>}<div className="external-map-published">{source.published_maps?.map(map => <div key={`${map.id}:${map.revision}`}><b>{map.id} · r{map.revision}</b><span className="external-map-hash">{map.sha256}</span><a className="button secondary" href={publishedMapRoute(project, map)} onClick={event => { event.preventDefault(); onUseMap(map); }}>이 지도로 시나리오 작성 →</a></div>)}</div><p className="external-map-muted">시나리오에서 배우·경로·행동을 저장한 뒤 일반 실행과 원본 재생으로 이어갑니다. 지도 확인 작업 자체는 주행 시험이 아닙니다.</p></section>
      </>}
    </div></div>
  </section>;
}
