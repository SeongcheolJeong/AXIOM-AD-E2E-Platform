import { useEffect, useMemo, useRef, useState } from 'react';
import { ReactFlow, Background, Controls, MarkerType, Position } from '@xyflow/react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, authHeaders, useResource } from '../../api';
import '@xyflow/react/dist/style.css';
import './fpga-lab.css';

type Config = Record<string, number>;
type Ref = { id: string; revision: number; sha256: string };
type Design = Ref & { name: string; seed: number; config: Config };
type Event = { id: number; time_ns: number; kind: string; frame: number; line: number | null; cause_event_id: number | null; ddr_bytes: number; fifo_bytes: number };
type Frame = { frame: number; expected_ns: number; trigger_ns: number; supplied_ns: number | null; tx_start_ns: number | null; tx_end_ns: number | null; arrival_ns: number | null; status: string; loss_reason: string | null; underflow: boolean; cause_event_id: number | null };
type Trace = { events: Event[]; frames: Frame[]; summary: Record<string, number>; config: Config; design_ref: Ref; seed: number };
type Run = { id: string; status: string; error?: string; project_id: string; experiment_kind: string; artifacts: { name: string; download_url: string; sha256: string }[] };
type History = { id: string; status: string; name: string; design_ref: Ref };
const base = '/hil-sim/fpga-lab';
const groups: { id: string; label: string; keys: [string, string][] }[] = [
  { id: 'raw', label: '합성 RAW', keys: [['width', '가로 px'], ['height', '세로 px'], ['bits', 'RAW bits'], ['fps', '공급 FPS'], ['frames', '프레임 수'], ['supply_delay_us', '공급 지연 µs']] },
  { id: 'ddr', label: 'DDR', keys: [['ddr_bytes', 'DDR bytes'], ['write_MBps', '쓰기 MB/s'], ['read_MBps', '읽기 MB/s'], ['preload_frames', '초기 프레임']] },
  { id: 'scheduler', label: '스케줄러', keys: [['start_delay_us', '시작 지연 µs'], ['jitter_us', '지터 ±µs'], ['drop_every', '매 N 프레임 누락 (0=없음)']] },
  { id: 'fifo', label: 'FIFO', keys: [['fifo_bytes', 'FIFO bytes']] },
  { id: 'packet', label: '패킷', keys: [['overhead_bytes', '라인 오버헤드 bytes']] },
  { id: 'serializer', label: 'Serializer', keys: [] },
  { id: 'link', label: '링크', keys: [['link_Mbps', '유효 Mbps'], ['propagation_us', '전파 지연 µs']] },
  { id: 'deserializer', label: 'Deserializer', keys: [] },
  { id: 'ecu', label: 'ECU', keys: [['expected_fps', '기대 FPS'], ['deadline_us', '허용 지연 µs'], ['timeout_us', 'Timeout µs']] },
];
function pendingAttempt(project: string) {
  try { return JSON.parse(sessionStorage.getItem(`fpga.pending.${project}`) ?? 'null'); } catch { return null; }
}
const terminal = (status: string) => ['completed', 'failed', 'cancelled', 'interrupted'].includes(status);
function bookmark(values: Record<string, string | null>) {
  const q = new URLSearchParams(location.hash.split('?')[1] ?? '');
  for (const [key, value] of Object.entries(values)) { if (value === null) q.delete(key); else q.set(key, value); }
  history.replaceState(null, '', `#hil-sim?${q}`);
}
function ms(ns: number | null | undefined) { return ns == null ? '—' : `${(ns / 1e6).toFixed(3)} ms`; }
const post = (body: unknown, key?: string): RequestInit => ({ method: 'POST', body: JSON.stringify(body), headers: key ? { 'Idempotency-Key': key } : {} });

function Pattern({ config, frame, seed }: { config: Config; frame: number; seed: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const ctx = canvas.current?.getContext('2d'); if (!ctx) return;
    const pixels = ctx.createImageData(256, 128), maximum = 2 ** config.bits - 1;
    for (let y = 0; y < 128; y++) for (let x = 0; x < 256; x++) {
      const value = Math.floor(((Math.floor(x * config.width / 256) + Math.floor(y * config.height / 128) + frame + seed) % (maximum + 1)) * 255 / maximum);
      const offset = (y * 256 + x) * 4;
      pixels.data[offset] = pixels.data[offset + 1] = pixels.data[offset + 2] = value; pixels.data[offset + 3] = 255;
    }
    ctx.putImageData(pixels, 0, 0);
  }, [config, frame, seed]);
  return <figure className="fpga-pattern"><canvas ref={canvas} width={256} height={128} aria-label="합성 RAW 패턴 미리보기" /><figcaption>합성 ramp/1 · 프레임 {frame}<br />RAW{config.bits} → 8-bit 축소 표시</figcaption></figure>;
}
function TimingWave({ frame, other, timeout }: { frame: Frame; other?: Frame; timeout: number }) {
  const end = Math.max(frame.arrival_ns ?? 0, other?.arrival_ns ?? 0, frame.expected_ns + timeout * 1000, frame.trigger_ns, frame.tx_end_ns ?? 0);
  const start = Math.min(frame.expected_ns, frame.supplied_ns ?? frame.expected_ns, other?.expected_ns ?? frame.expected_ns);
  const x = (time: number) => 110 + (time - start) / Math.max(1, end - start) * 750;
  const tracks: [string, number | null, number | null][] = [['Trigger',frame.trigger_ns,null],['DDR 준비',frame.supplied_ns,null],['A 송신',frame.tx_start_ns,frame.tx_end_ns],['A 수신',frame.arrival_ns,null],['Timeout',frame.expected_ns + timeout * 1000,null]];
  if (other) tracks.push(['B 송신',other.tx_start_ns,other.tx_end_ns],['B 수신',other.arrival_ns,null]);
  return <svg className="fpga-wave" viewBox={`0 0 900 ${tracks.length * 30 + 30}`} role="img" aria-label="선택 프레임 타이밍 파형">{tracks.map(([label, begin, finish], i) => <g key={label}><text x={0} y={i*30+22} fill="#b7cbe0" fontSize={12}>{label}</text><line x1={110} x2={860} y1={i*30+18} y2={i*30+18} stroke="#33495e" />{begin != null && (finish != null ? <rect x={x(begin)} y={i*30+10} width={Math.max(2,x(finish)-x(begin))} height={16} fill={label.startsWith('B') ? '#fab764' : '#46cbb7'} /> : <line x1={x(begin)} x2={x(begin)} y1={i*30+7} y2={i*30+29} stroke={label === 'Timeout' ? '#eaa076' : '#46cbb7'} strokeWidth={2} />)}</g>)}<text x={110} y={tracks.length*30+22} fill="#b7cbe0" fontSize={11}>{ms(start)}</text><text x={760} y={tracks.length*30+22} fill="#b7cbe0" fontSize={11}>{ms(end)}</text></svg>;
}

export default function FpgaLab({ project }: { project: string }) {
  const initial = useRef(new URLSearchParams(location.hash.split('?')[1] ?? ''));
  const templates = useResource<{ adapter_installed: boolean; items: { id: string; name: string; config: Config }[]; config_schema: { properties: Record<string, { minimum?: number; maximum?: number }> } }>(`${base}/templates?project_id=${encodeURIComponent(project)}`);
  const designs = useResource<{ items: Design[] }>(`${base}/designs?project_id=${encodeURIComponent(project)}`);
  const runs = useResource<{ items: History[] }>(`${base}/executions?project_id=${encodeURIComponent(project)}`);
  const [config, setConfig] = useState<Config | null>(null), [name, setName] = useState('정상 전송'), [seed, setSeed] = useState(42);
  const [saved, setSaved] = useState<Design | null>(null), [block, setBlock] = useState('raw'), [expanded, setExpanded] = useState(true);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [stage, setStage] = useState('');
  const [runId, setRunId] = useState(initial.current.get('execution') ?? ''), [compareId, setCompareId] = useState(initial.current.get('compare') ?? '');
  const [run, setRun] = useState<Run | null>(null), [trace, setTrace] = useState<Trace | null>(null), [compare, setCompare] = useState<Trace | null>(null);
  const [cursor, setCursor] = useState(Number(initial.current.get('event') ?? 0) || 0), [playing, setPlaying] = useState(false);
  const attempt = useRef<{ signature: string; key: string; preview?: { experiment: unknown; preview_hash: string }; experiment?: string } | null>(pendingAttempt(project));
  const saveAttempt = useRef<{ signature: string; key: string } | null>(null);
  const initialized = useRef(false);
  const active = !!runId && (!run || !terminal(run.status));
  const dirty = !saved || JSON.stringify(saved.config) !== JSON.stringify(config) || saved.name !== name || saved.seed !== seed;
  const selectDesign = (d: Design) => { setSaved(d); setConfig(d.config); setName(d.name); setSeed(d.seed); bookmark({ design: d.id, revision: String(d.revision) }); };
  useEffect(() => {
    if (initialized.current || !templates.data || !designs.data) return;
    initialized.current = true;
    const id = initial.current.get('design'), rev = Number(initial.current.get('revision'));
    const d = designs.data.items.find(x => x.id === id && x.revision === rev);
    if (id && !d) { setError('저장 개정을 찾지 못했습니다. 프로젝트와 개정을 확인하세요.'); }
    if (d) selectDesign(d); else setConfig(templates.data.items[0].config);
  }, [templates.data, designs.data]);
  async function fetchTrace(value: Run): Promise<Trace | null> {
    if (value.project_id !== project || value.experiment_kind !== 'fpga_timing') throw new Error('선택한 프로젝트의 FPGA 실행이 아닙니다.');
    const artifact = value.artifacts.find(a => a.name === 'timing-trace.json');
    if (!artifact) return null;
    return api<Trace>(artifact.download_url.replace('/api/v1', ''));
  }
  useEffect(() => {
    setTrace(null); setRun(null); setPlaying(false);
    if (!runId) return;
    let live = true; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api<Run>(`/experiments/executions/${encodeURIComponent(runId)}`);
        if (!live) return;
        if (next.project_id !== project || next.experiment_kind !== 'fpga_timing') throw new Error('다른 프로젝트 또는 실행 종류입니다.');
        setRun(next);
        if (terminal(next.status)) {
          const data = await fetchTrace(next);
          if (live) { setTrace(data); runs.reload(); }
        } else timer = setTimeout(poll, 1000);
      } catch (e) { if (live) { setError(String(e)); timer = setTimeout(poll, 3000); } }
    }
    void poll(); return () => { live = false; clearTimeout(timer); };
  }, [runId, project]);
  useEffect(() => {
    setCompare(null); if (!compareId) return;
    let live = true;
    api<Run>(`/experiments/executions/${encodeURIComponent(compareId)}`).then(fetchTrace).then(t => { if (live) setCompare(t); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [compareId, project]);
  useEffect(() => {
    if (!playing || !trace) return;
    const timer = setInterval(() => setCursor(i => {
      const target = (trace.events[i]?.time_ns ?? 0) + 20000000;
      let next = i;
      while (next < trace.events.length - 1 && trace.events[next].time_ns < target) next++;
      if (next === trace.events.length - 1) setPlaying(false);
      return next;
    }), 100);
    return () => clearInterval(timer);
  }, [playing, trace]);
  useEffect(() => { bookmark({ event: String(cursor) }); }, [cursor]);
  const event = trace?.events[Math.min(cursor, trace.events.length - 1)];
  const frame = event ? trace?.frames[event.frame] : null;
  const chart = useMemo(() => {
    if (!trace) return [];
    const stride = Math.max(1, Math.ceil(trace.events.length / 1600));
    return trace.events.filter((e, i) => i % stride === 0 || i === trace.events.length - 1 || e.kind === 'ecu_arrival' || e.kind === 'trigger').map(e => ({ ms: e.time_ns / 1e6, DDR: e.ddr_bytes, FIFO: e.fifo_bytes }));
  }, [trace]);
  const visible = expanded ? groups : [groups[0], { id: 'fpga', label: 'FPGA · 펼치기', keys: [] }, ...groups.slice(4)];
  const currentNode = event?.kind.startsWith('ddr') || event?.kind === 'preloaded' ? 'ddr' : event?.kind.startsWith('fifo') ? 'fifo' : event?.kind.startsWith('tx') ? 'link' : event?.kind.startsWith('ecu') ? 'ecu' : 'scheduler';
  const nodes = visible.map((g, i) => ({ id: g.id, position: { x: (i < 5 ? i : 8 - i) * 170, y: i < 5 ? 25 : 155 }, sourcePosition: i === 4 ? Position.Bottom : i < 5 ? Position.Right : Position.Left, targetPosition: i === 5 ? Position.Top : i < 5 ? Position.Left : Position.Right, data: { label: `${g.label}${g.id === currentNode && event ? `\n#${event.frame} · ${event.kind}` : ''}` }, style: { width: 140, minHeight: 72, border: g.id === block ? '2px solid #49caba' : '1px solid #52647c', background: g.id === currentNode && event ? '#164e50' : '#172638', color: '#eef5ff', borderRadius: 12, fontSize: 12, whiteSpace: 'pre-line' as const } }));
  const edges = visible.slice(1).map((g, i) => ({ id: `${visible[i].id}-${g.id}`, source: visible[i].id, target: g.id, type: 'smoothstep', animated: playing, markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#49caba', strokeWidth: 2 } }));
  async function action(fn: () => Promise<void>) { setBusy(true); setError(''); try { await fn(); } catch (e) { setError(String(e)); } finally { setBusy(false); } }
  async function save() {
    const body = { project_id: project, name, seed, config, ...(saved ? { id: saved.id, expected_revision: saved.revision } : {}) };
    const signature = JSON.stringify(body);
    if (saveAttempt.current?.signature !== signature) saveAttempt.current = { signature, key: crypto.randomUUID() };
    const d = await api<Design>(`${base}/designs`, post(body, saveAttempt.current.key));
    selectDesign(d); saveAttempt.current = null; designs.reload(); setStage(`개정 r${d.revision} 저장 완료`);
  }
  async function execute() {
    if (!saved || dirty) throw new Error('현재 설정을 먼저 저장하세요.');
    const signature = saved.sha256;
    if (attempt.current?.signature !== signature) attempt.current = { signature, key: crypto.randomUUID() };
    const a = attempt.current;
    const persist = () => sessionStorage.setItem(`fpga.pending.${project}`, JSON.stringify(a));
    persist();
    setStage('계산 준비 확인');
    a.preview ??= await api(`${base}/designs/${saved.id}/preview`, post({ project_id: project, revision: saved.revision, sha256: saved.sha256 }));
    persist();
    setStage('실행 제출');
    if (!a.experiment) a.experiment = (await api<{ id: string }>('/experiments', post(a.preview.experiment, a.key))).id;
    persist();
    const job = await api<{ id: string }>(`/experiments/${a.experiment}/executions`, post({ preview_hash: a.preview.preview_hash }, a.key));
    setCursor(0); setRunId(job.id); bookmark({ execution: job.id, event: '0' }); attempt.current = null; sessionStorage.removeItem(`fpga.pending.${project}`); setStage('실행 큐 접수');
  }
  async function download(url: string, filename: string) {
    const response = await fetch(url, { headers: authHeaders() });
    if (!response.ok) throw new Error(`다운로드 실패 ${response.status}`);
    const blob = URL.createObjectURL(await response.blob()); const a = document.createElement('a'); a.href = blob; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(blob), 1000);
  }
  if (!config || !templates.data) return <div className="fpga-lab">{error || templates.error || designs.error || 'FPGA Lab 불러오는 중…'}</div>;
  return <section className="fpga-lab">
    <header className="fpga-title"><div><span className="fpga-eyebrow">CAMERA HIL / DESIGN LAB</span><h1>FPGA Lab <small>동작·타이밍</small></h1><p>RAW에서 ECU까지, 데이터가 기다리는 곳을 확인하세요.</p></div><span className="fpga-badge">Behavioral model · SimPy</span></header>
    <p className="fpga-boundary">합성 RAW · 라인 단위 추상 모델 · RTL/CSI-2 규격/전기 신호/실제 ECU 검증 아님. 재생은 완료된 계산의 사건 기록입니다.</p>
    {(error || templates.error || designs.error || runs.error) && <p role="alert">{error || templates.error || designs.error || runs.error}</p>}
    <div className="fpga-toolbar">
      <label>예제<select aria-label="FPGA 예제" defaultValue="normal" onChange={e => { const p = templates.data!.items.find(x => x.id === e.target.value)!; setConfig(p.config); setName(p.name); setSaved(null); bookmark({ design: null, revision: null }); }}>{templates.data.items.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label>설계 이름<input aria-label="설계 이름" value={name} onChange={e => setName(e.target.value)} /></label>
      <label>Seed<input aria-label="Seed" type="number" value={seed} min={0} max={2147483647} onChange={e => setSeed(Number(e.target.value))} /></label>
      <button disabled={busy} onClick={() => void action(save)}>개정 저장</button>
      <button className="fpga-primary" disabled={busy || active || dirty || !templates.data.adapter_installed} onClick={() => void action(execute)}>계산 실행</button>
      {active && <button disabled={busy} onClick={() => void action(async () => { await api(`/experiments/executions/${runId}/cancel`, post({})); })}>실행 취소</button>}
      <span>{dirty ? '미저장 편집안' : `저장 r${saved?.revision}`} · {stage}</span>
    </div>
    {!templates.data.adapter_installed && <p role="alert">SimPy adapter가 설치되지 않았습니다. 작성과 보존 결과 조회만 가능합니다.</p>}
    <div className="fpga-layout"><div className="fpga-panel">
      <div className="fpga-panel-heading"><strong>카메라 1채널 · 고정 경로</strong><button onClick={() => setExpanded(x => !x)}>{expanded ? 'FPGA 내부 접기' : 'FPGA 내부 펼치기'}</button></div>
      <div className="fpga-flow"><ReactFlow key={String(expanded)} nodes={nodes} edges={edges} fitView nodesDraggable={false} nodesConnectable={false} onNodeClick={(_, n) => { if (n.id === 'fpga') setExpanded(true); else setBlock(n.id); }} minZoom={0.35} maxZoom={1.5}><Background color="#334559" /><Controls showInteractive={false} /></ReactFlow></div>
      <div className="fpga-budget"><span>편집안 프레임 <b>{(config.width * config.height * config.bits / 8 / 1e6).toFixed(3)} MB</b></span><span>Payload <b>{(config.width * config.height * config.bits * config.fps / 1e9).toFixed(3)} Gbit/s</b></span><span>링크 <b>{config.link_Mbps} Mbps</b></span></div>
      <p className="fpga-muted">Payload에는 라인 오버헤드가 포함되지 않습니다. DDR는 독립 읽기·쓰기 포트, 링크는 순차 송신 모델입니다.</p>
    </div><aside className="fpga-panel fpga-settings"><h2>{groups.find(g => g.id === block)?.label} 설정</h2>{groups.find(g => g.id === block)?.keys.map(([key, label]) => <label key={key}>{label}{key === 'bits' ? <select aria-label={label} value={config[key]} onChange={e => setConfig({ ...config, [key]: Number(e.target.value) })}>{[8, 10, 12, 16].map(n => <option key={n}>{n}</option>)}</select> : <input aria-label={label} type="number" step={1} min={templates.data?.config_schema.properties[key]?.minimum} max={templates.data?.config_schema.properties[key]?.maximum} value={config[key]} onChange={e => setConfig({ ...config, [key]: Number(e.target.value) })} />}</label>)}{['serializer', 'deserializer'].includes(block) && <p>링크 처리율과 전파 지연에 포함된 추상 전달 블록입니다. 특정 부품/IP의 모델이 아닙니다.</p>}</aside></div>
    <div className="fpga-toolbar"><label>저장 개정<select aria-label="저장 개정" value={saved ? `${saved.id}:${saved.revision}` : ''} onChange={e => { const d = designs.data?.items.find(x => `${x.id}:${x.revision}` === e.target.value); if (d) selectDesign(d); }}><option value="">선택</option>{designs.data?.items.map(d => <option key={`${d.id}:${d.revision}`} value={`${d.id}:${d.revision}`}>{d.name} · r{d.revision} · {d.id.slice(-6)}</option>)}</select></label>
      <label>실행 A<select aria-label="실행 A" value={runId} onChange={e => { setCursor(0); setRunId(e.target.value); bookmark({ execution: e.target.value, event: '0' }); }}><option value="">선택</option>{runId && !runs.data?.items.some(r => r.id === runId) && <option value={runId}>{runId} · 진행 확인 중</option>}{runs.data?.items.map(r => <option key={r.id} value={r.id}>{r.name} · r{r.design_ref.revision} · {r.status} · {r.id.slice(-6)}</option>)}</select></label>
      <label>비교 B<select aria-label="비교 B" value={compareId} onChange={e => { setCompareId(e.target.value); bookmark({ compare: e.target.value }); }}><option value="">없음</option>{runs.data?.items.filter(r => r.status === 'completed').map(r => <option key={r.id} value={r.id}>{r.name} · r{r.design_ref.revision} · {r.id.slice(-6)}</option>)}</select></label>
      {run && <span role="status">실행 상태: {run.status}{run.error ? ` · ${run.error}` : ''}</span>}
    </div>
    {trace && <>
      <div className="fpga-panel"><h2>저장 실행 A · r{trace.design_ref.revision} <small>{runId.slice(-8)}</small></h2>{(dirty || saved?.sha256 !== trace.design_ref.sha256) && <p className="fpga-warning">위 편집안과 다른 저장 결과입니다. 현재 설정은 이 결과에 적용되지 않았습니다.</p>}
      <div className="fpga-metrics">{[['received','수신'],['missing','누락'],['deadline_exceeded','지연 초과'],['underflow_frames','공급 부족'],['overflow_frames','DDR 넘침']].map(([k,l]) => <div key={k}><span>{l}</span><b>{trace.summary[k]}<small>{compare ? ` / B ${compare.summary[k]}` : ''}</small></b></div>)}</div>
      {compare && <><h3>A/B 설정 차이 · B r{compare.design_ref.revision}</h3><p>Seed A {trace.seed} / B {compare.seed} · 각각 독립적으로 계산된 저장 결과</p><table><thead><tr><th>설정</th><th>A</th><th>B</th></tr></thead><tbody>{Object.keys(trace.config).filter(k => trace.config[k] !== compare.config[k]).map(k => <tr key={k}><td>{k}</td><td>{trace.config[k]}</td><td>{compare.config[k]}</td></tr>)}</tbody></table></>}
      <div className="fpga-toolbar"><button onClick={() => setPlaying(p => !p)}>{playing ? '일시정지' : '재생'}</button><button onClick={() => { setPlaying(false); setCursor(i => Math.max(0, i - 1)); }}>이전 사건</button><button onClick={() => { setPlaying(false); setCursor(i => Math.min(trace.events.length - 1, i + 1)); }}>다음 사건</button><button onClick={() => { setPlaying(false); const index = trace.events.findIndex((e, i) => i > cursor && e.frame > (event?.frame ?? 0) && e.kind === 'trigger'); setCursor(index < 0 ? trace.events.length - 1 : index); }}>다음 프레임</button><span>#{event?.id} · {ms(event?.time_ns)} · {event?.kind}</span></div>
      <input className="fpga-scrub" aria-label="사건 위치" type="range" min={0} max={trace.events.length - 1} value={Math.min(cursor, trace.events.length - 1)} onChange={e => { setPlaying(false); setCursor(Number(e.target.value)); }} />
      <div className="fpga-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={chart}><CartesianGrid stroke="#2a394a" /><XAxis dataKey="ms" type="number" domain={['dataMin','dataMax']} unit="ms" /><YAxis yAxisId="ddr" tickFormatter={n => `${(Number(n) / 1e6).toFixed(1)}M`} /><YAxis yAxisId="fifo" orientation="right" tickFormatter={n => `${(Number(n) / 1e3).toFixed(0)}k`} /><Tooltip />{event && <ReferenceLine yAxisId="ddr" x={event.time_ns / 1e6} stroke="#e5edf7" strokeDasharray="4 4" />}<Line yAxisId="ddr" type="stepAfter" dataKey="DDR" stroke="#46cbb7" dot={false} isAnimationActive={false} /><Line yAxisId="fifo" type="stepAfter" dataKey="FIFO" stroke="#fab764" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>
      <p className="fpga-muted">차트는 약 2,000점으로 표시합니다. 정확한 사건 값: DDR {event?.ddr_bytes} B / FIFO {event?.fifo_bytes} B · 최대 DDR {trace.summary.peak_ddr_bytes} B / FIFO {trace.summary.peak_fifo_bytes} B</p>
      {frame && <><Pattern config={trace.config} frame={frame.frame} seed={trace.seed} /><TimingWave frame={frame} other={compare?.frames[frame.frame]} timeout={trace.config.timeout_us} /><h3>프레임 {frame.frame} · {frame.status}</h3><div className="fpga-timeline">{[['Trigger',frame.trigger_ns],['DDR 준비',frame.supplied_ns],['송신 시작',frame.tx_start_ns],['송신 종료',frame.tx_end_ns],['ECU 도착',frame.arrival_ns]].map(([label, value]) => <div key={String(label)}><span>{label}</span><b>{ms(value as number | null)}</b></div>)}</div><p>원인: {frame.loss_reason ?? (frame.underflow ? '공급 부족 사건 발생' : '라인 전송·큐 대기 기록 참조')}{frame.cause_event_id != null && <button onClick={() => setCursor(frame.cause_event_id!)}>원인 사건 #{frame.cause_event_id}</button>}{event?.cause_event_id != null && <button onClick={() => setCursor(event.cause_event_id!)}>선행 사건 #{event.cause_event_id}</button>}</p>{compare?.frames[frame.frame] && <p>B 같은 프레임: {compare.frames[frame.frame].status} · 송신 {ms(compare.frames[frame.frame].tx_start_ns)} → 수신 {ms(compare.frames[frame.frame].arrival_ns)}</p>}</>}
      <div className="fpga-toolbar"><button onClick={() => void action(() => download(`/api/v1/experiments/executions/${runId}/export`, `${runId}.json`))}>실행 근거 JSON</button>{run?.artifacts.map(a => <button key={a.name} onClick={() => void action(() => download(a.download_url, a.name))}>{a.name}</button>)}</div>
      </div>
    </>}
  </section>;
}
