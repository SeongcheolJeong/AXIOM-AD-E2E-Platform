import { useEffect, useRef, useState } from "react";
import { Camera, Maximize2, Pause, Play, Repeat2, RotateCcw, X } from "lucide-react";
import { neuralPlaybackStep, neuralSourceLabel, neuralOriginalLabel } from "./demo-contract";
import type { NeuralExecution } from "./demo-contract";
import { loadNeuralResult } from "./load-neural-result";
import type { LoadedNeuralResult } from "./load-neural-result";

import NeuralDetectionOverlay from "./NeuralDetectionOverlay";
import { neuralSides, predictionForFrame } from "./neural-perception";
import type { NeuralPerceptionResult } from "./neural-perception";

type Images = { index: number; original: string; baseline: string; novel: string };
const signed = (n: number) => `${n > 0 ? "+" : ""}${n.toFixed(2)}`;
export default function NeuralPresenter({ execution, onFrameChange, onLoaded, comparisonResult = null, inspection, onDisplayedFrame }: { execution: NeuralExecution; onFrameChange?: (index: number) => void; onLoaded?: (value: LoadedNeuralResult) => void; comparisonResult?: NeuralPerceptionResult | null; inspection?: { frame_index: number; nonce: number } | null; onDisplayedFrame?: (index: number) => void }) {
  const [data, setData] = useState<LoadedNeuralResult | null>(null), [error, setError] = useState(""), [frameError, setFrameError] = useState("");
  const [index, setIndex] = useState(0), [images, setImages] = useState<Images | null>(null), [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(1);
  const isSweepRun = execution.summary?.mode === "camera_sweep";
  const [looping, setLooping] = useState(isSweepRun), [showOriginal, setShowOriginal] = useState(false), [showInset, setShowInset] = useState(true);
  const [showDetections, setShowDetections] = useState(true);
  const direction = useRef<1 | -1>(1);
  const [mode, setMode] = useState<"reconstruction" | "novel">("novel"), [layout, setLayout] = useState<"showcase" | "split" | "wipe" | "predictions">("showcase"), [wipe, setWipe] = useState(50), [expanded, setExpanded] = useState(false);
  const root = useRef<HTMLElement>(null), cache = useRef(new Map<number, Images>()), nextDue = useRef<number | null>(null);
  const presentationRequested = useRef(false);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    void loadNeuralResult(execution, controller.signal).then(value => { if (active) { setIndex(value.result.mode === "camera_sweep" ? 30 : 0); setData(value); onLoaded?.(value); if (value.result.mode === "camera_sweep") onFrameChange?.(value.result.frame_index); } }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { active = false; controller.abort(); for (const item of cache.current.values()) for (const side of ["original", "baseline", "novel"] as const) URL.revokeObjectURL(item[side]); cache.current.clear(); };
  }, [execution]);
  useEffect(() => {
    if (!data) return;
    let active = true; const controller = new AbortController();
    setFrameError("");
    const frame = data.result.frames[index];
    async function read() {
      let entry = cache.current.get(index);
      if (!entry) {
        const blobs = await Promise.all((["original", "baseline", "novel"] as const).map(async side => {
          const bytes = await data!.read(frame[side], controller.signal), blob = new Blob([new Uint8Array(bytes)], { type: "image/png" });
          const bitmap = await createImageBitmap(blob);
          try { if (bitmap.width !== data!.result.width || bitmap.height !== data!.result.height) throw new Error("렌더 프레임의 영상 크기가 manifest와 다릅니다."); } finally { bitmap.close(); }
          return blob;
        }));
        if (!active) return;
        entry = { index, original: URL.createObjectURL(blobs[0]), baseline: URL.createObjectURL(blobs[1]), novel: URL.createObjectURL(blobs[2]) };
        cache.current.set(index, entry);
      }
      if (active) {
        setImages(entry); onDisplayedFrame?.(frame.frame_index);
        if (data!.result.mode !== "camera_sweep") onFrameChange?.(frame.frame_index);
        while (cache.current.size > 12) {
          const oldest = cache.current.keys().next().value!;
          if (oldest === index) break;
          const old = cache.current.get(oldest)!; cache.current.delete(oldest);
          // The previous images remain valid until React commits the next frame.
          window.setTimeout(() => { for (const side of ["original", "baseline", "novel"] as const) URL.revokeObjectURL(old[side]); }, 500);
        }
      }
    }
    void read().catch(reason => { if (active) { setFrameError(reason instanceof Error ? reason.message : String(reason)); setPlaying(false); } });
    return () => { active = false; controller.abort(); };
  }, [data, index, onFrameChange, onDisplayedFrame]);
  useEffect(() => {
    if (!inspection || !data) return;
    const target = data.result.frames.findIndex(frame => frame.frame_index === inspection.frame_index);
    if (target >= 0) { setPlaying(false); nextDue.current = null; setIndex(target); setLayout("predictions"); setShowDetections(true); }
  }, [inspection, data]);
  const ready = images?.index === index && !frameError;
  useEffect(() => {
    if (!playing) { nextDue.current = null; return; }
    if (!data || !ready) return;
    const next = neuralPlaybackStep(index, data.result.frames.length, direction.current, looping);
    if (!next) { setPlaying(false); return; }
    const interval = Math.abs(data.result.frames[next.index].time_s - data.result.frames[index].time_s) * 1000 / speed;
    if (nextDue.current === null) nextDue.current = performance.now() + interval;
    const timer = setTimeout(() => { nextDue.current = performance.now() + interval; direction.current = next.direction; setIndex(next.index); }, Math.max(0, nextDue.current - performance.now()));
    return () => clearTimeout(timer);
  }, [playing, ready, index, data, speed, looping]);
  useEffect(() => {
    const hide = () => { if (document.hidden) setPlaying(false); };
    // Native fullscreen can exit automatically in embedded hosts. Presentation
    // remains open inside the host window until the user presses Escape or Close.
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      presentationRequested.current = false; setExpanded(false);
      if (document.fullscreenElement === root.current) void document.exitFullscreen().catch(() => {});
    };
    document.addEventListener("visibilitychange", hide); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("visibilitychange", hide); document.removeEventListener("keydown", escape); };
  }, []);
  async function fullscreen() {
    if (expanded) {
      presentationRequested.current = false; setExpanded(false);
      if (document.fullscreenElement === root.current) await document.exitFullscreen().catch(() => {});
      return;
    }
    presentationRequested.current = true; setExpanded(true);
    try { await root.current?.requestFullscreen(); } catch { /* Keep CSS presentation when native fullscreen is unavailable. */ }
    finally {
      // A rejected or host-restored request must not undo the user's open action.
      if (presentationRequested.current) setExpanded(true);
    }
  }
  function seek(next: number) { setPlaying(false); nextDue.current = null; direction.current = next === 60 ? -1 : 1; setIndex(next); }
  const result = data?.result, shown = result && images ? result.frames[images.index] : null, sweep = result?.mode === "camera_sweep";
  function play() { if (!looping && result && index === result.frames.length - 1) { direction.current = 1; setIndex(0); } setPlaying(value => !value); }
  const appliedLateral = shown?.applied_lateral_m ?? result?.lateral_m ?? 0, appliedHeight = shown?.applied_height_m ?? result?.height_m ?? 0;
  const sourceTime = sweep ? shown?.source_time_s : shown?.time_s;
  function comparison(next: "reconstruction" | "novel") { setMode(next); if (layout === "showcase" || layout === "predictions") setLayout("split"); }
  const left = mode === "reconstruction" ? "original" : "baseline", right = mode === "reconstruction" ? "baseline" : "novel";
  const shownPrediction = predictionForFrame(comparisonResult, shown), sourceLabel = neuralSourceLabel(result?.source_kind);
  const labels = { original: neuralOriginalLabel(result?.source_kind), baseline: "Neural 재구성 · 기준 시점", novel: "Neural 렌더 · 새로운 시점" };
  if (error) return <div className="neural-error" role="alert"><strong>저장 결과를 열 수 없습니다.</strong><p>{error}</p></div>;
  return <section className={`neural-presenter ${expanded ? "neural-expanded" : ""}`} ref={root} aria-label="Neural Sim 발표 화면">
    <header className="neural-presenter-heading"><div><span className="neural-eyebrow">{sweep ? "FROZEN MOMENT · MOVE THE CAMERA" : "RECONSTRUCTED SCENE · GENERATED VIEW"}</span><h2>{sweep ? "시간은 멈추고, 카메라만 움직입니다." : mode === "reconstruction" ? "로그에서 장면으로" : "기록에 없던 시점으로"}</h2></div><button onClick={() => void fullscreen()} aria-label="Neural 발표 전체 화면">{expanded ? <X size={17}/> : <Maximize2 size={17}/>} {expanded ? "발표 닫기" : "발표 모드"}</button></header>
    <div className="neural-display-settings"><strong>현재 표시 결과</strong>{result ? <span>{sweep ? `카메라 이동 ±${result.lateral_m.toFixed(2)} m · ${result.frames.length}개 실제 렌더 시점` : `좌우 ${signed(result.lateral_m)} m · 높이 ${signed(result.height_m)} m · ${result.mode === "frame" ? "단일 프레임" : `${result.frames.length} 프레임`}`}</span> : <span>저장 결과를 준비하는 중…</span>}</div>
    <div className="neural-view-options"><div className="neural-segment"><button aria-pressed={layout === "showcase"} onClick={() => { setLayout("showcase"); setMode("novel"); }}>새 시점 크게</button><button aria-pressed={layout !== "showcase" && layout !== "predictions" && mode === "reconstruction"} onClick={() => comparison("reconstruction")}>원본 ↔ 재구성</button><button aria-pressed={layout !== "showcase" && layout !== "predictions" && mode === "novel"} onClick={() => comparison("novel")}>기준 ↔ 새 시점</button>{comparisonResult && <button aria-pressed={layout === "predictions"} onClick={() => setLayout("predictions")}>모델 출력 3-way</button>}</div><div className="neural-segment">{layout === "predictions" ? <button aria-pressed={showDetections} onClick={() => setShowDetections(value => !value)}>검출 박스 · score</button> : layout === "showcase" ? <><button aria-pressed={showInset} onClick={() => setShowInset(value => !value)}>원본 작은 화면</button><button aria-pressed={showOriginal} onClick={() => setShowOriginal(value => !value)}>{showOriginal ? "새 시점으로 돌아가기" : "원본 크게 보기"}</button></> : <><button aria-pressed={layout === "split"} onClick={() => setLayout("split")}>나란히</button><button aria-pressed={layout === "wipe"} onClick={() => setLayout("wipe")}>슬라이더</button></>}</div></div>
    <div className={`neural-image-stage ${layout}`} style={layout !== "split" && layout !== "predictions" ? { aspectRatio: `${result?.width ?? 640} / ${result?.height ?? 360}` } : undefined}>
      {layout === "predictions" ? neuralSides.map(side => <figure key={side} className="neural-prediction-view"><img src={images?.[side]} alt={labels[side]}/>{showDetections && <NeuralDetectionOverlay prediction={shownPrediction?.[side]} side={side}/>}<figcaption>{side === "original" ? "원본" : side === "baseline" ? "같은 시점 재구성" : "변경 시점"}<b>{shownPrediction ? `${shownPrediction[side].detections.length}개` : "미실행"}</b></figcaption></figure>) : layout === "showcase" ? <>
        <figure className="neural-main-view">{images ? <img src={images[showOriginal ? "original" : "novel"]} alt={labels[showOriginal ? "original" : "novel"]} width={result?.width} height={result?.height}/> : <div className="neural-image-placeholder">영상 묶음을 준비하는 중…</div>}{showDetections && <NeuralDetectionOverlay prediction={shownPrediction?.[showOriginal ? "original" : "novel"]} side={showOriginal ? "original" : "novel"}/>}<figcaption>{showOriginal ? "원본 카메라 · 기록된 영상" : "새 시점 · Neural 렌더"}</figcaption></figure>
        {sweep && shown && <div className="neural-frozen-badge"><Pause size={12}/>원본 시각 고정 <b>{sourceTime?.toFixed(2)} s</b><span>frame {shown.source_frame_index}</span></div>}
        {showInset && images && !showOriginal && <figure className="neural-original-inset"><img src={images.original} alt={labels.original}/>{showDetections && <NeuralDetectionOverlay prediction={shownPrediction?.original} side="original"/>}<figcaption>원본 · {sourceTime?.toFixed(2)} s</figcaption></figure>}
        {shown && <div className="neural-pose-badge"><Camera size={16}/><span>{showOriginal ? "원본 카메라" : `좌우 ${signed(appliedLateral)} m`}<small>{showOriginal ? "기준 위치" : `높이 ${signed(appliedHeight)} m`}</small></span></div>}
        {sweep && !playing && ready && !showOriginal && <button className="neural-sweep-start" onClick={play}><Play size={18}/>카메라 이동 재생</button>}
      </> : layout === "split" ? ([left, right] as const).map((side, order) => <figure key={side}><figcaption><b>{order === 0 ? "A" : "B"}</b>{labels[side]}</figcaption>{images ? <img src={images[side]} alt={labels[side]} width={result?.width} height={result?.height}/> : <div className="neural-image-placeholder">영상 묶음을 준비하는 중…</div>}{showDetections && <NeuralDetectionOverlay prediction={shownPrediction?.[side]} side={side}/>}</figure>) : <><figure className="neural-wipe-back">{images && <img src={images[right]} alt={labels[right]}/>}{showDetections && <NeuralDetectionOverlay prediction={shownPrediction?.[right]} side={right}/>}<figcaption className="right"><b>B</b>{labels[right]}</figcaption></figure><figure className="neural-wipe-front" style={{ clipPath: `inset(0 ${100 - wipe}% 0 0)` }}>{images && <img src={images[left]} alt={labels[left]}/>}{showDetections && <NeuralDetectionOverlay prediction={shownPrediction?.[left]} side={left}/>}<figcaption><b>A</b>{labels[left]}</figcaption></figure><div className="neural-wipe-line" style={{ left: `${wipe}%` }}><span>↔</span></div><input aria-label="Neural 영상 비교 슬라이더" type="range" min={0} max={100} value={wipe} onChange={event => setWipe(Number(event.target.value))}/></>}
    </div>
    {sweep && result && <div className="neural-camera-ruler" aria-label="현재 렌더 카메라 위치"><div className="neural-ruler-heading"><strong><Camera size={14}/>카메라만 이동</strong><span data-testid="neural-actual-camera">{signed(appliedLateral)} m</span><small>원본 {shown?.source_time_s?.toFixed(2)} s 고정</small></div><div className="neural-ruler-track"><i className="neural-ruler-origin"/><span className="neural-ruler-camera" style={{ left: `${50 + appliedLateral / result.lateral_m * 50}%` }}><Camera size={18}/></span><input aria-label="Neural 카메라 위치 탐색" type="range" min={0} max={result.frames.length - 1} step={1} value={index} onChange={event => seek(Number(event.target.value))}/></div><div className="neural-ruler-labels"><span>왼쪽 −{result.lateral_m.toFixed(1)} m</span><span>원본 카메라 0 m</span><span>오른쪽 +{result.lateral_m.toFixed(1)} m</span></div></div>}
    <div className="neural-transport"><button className="neural-play" aria-label={playing ? "Neural 재생 일시정지" : "Neural 영상 재생"} disabled={!result || result.frames.length < 2 || !!frameError} onClick={play}>{playing ? <Pause size={19}/> : <Play size={19}/>}</button><button aria-label={sweep ? "Neural 기준 카메라로" : "Neural 처음으로"} disabled={!result} onClick={() => seek(sweep ? 30 : 0)}><RotateCcw size={16}/></button>{sweep ? <span className="neural-sweep-progress">시점 {(images?.index ?? 0) + 1} / {result?.frames.length}</span> : <><time>{shown?.time_s.toFixed(2) ?? "—"}<small> / {result?.frames.at(-1)?.time_s.toFixed(2) ?? "—"} s</small></time><input aria-label="Neural 공통 시간축" type="range" min={0} max={Math.max(1, (result?.frames.length ?? 1) - 1)} step={1} value={index} disabled={!result || result.frames.length < 2} onChange={event => seek(Number(event.target.value))}/></>}<button aria-label="Neural 왕복 반복" aria-pressed={looping} onClick={() => setLooping(value => !value)}><Repeat2 size={15}/>{sweep ? "왕복 반복" : "반복"}</button><select aria-label="Neural 재생 속도" value={speed} onChange={event => { setSpeed(Number(event.target.value)); nextDue.current = null; }}><option value={.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option></select><span className="neural-simulation-label">{sourceLabel}</span></div>
    <p className="neural-playback-note" role="status">{frameError ? `영상 읽기 실패 · ${frameError}` : !data ? "영상 묶음을 한 번 읽어 재생을 준비합니다…" : !ready ? "다음 프레임을 준비하고 있습니다. 화면에는 마지막으로 읽은 시점이 유지됩니다." : sweep ? `원본 frame ${shown?.source_frame_index} · ${shown?.source_time_s?.toFixed(2)} s 고정 · 실제 생성 시점 ${shown?.frame_index}` : `동일한 원본 시각 · frame ${shown?.frame_index} · ${sourceLabel}`}</p>
    {comparisonResult && <p className="neural-overlay-model">{comparisonResult.model_snapshot.name} · r{comparisonResult.model_snapshot.revision} · {shownPrediction ? "모델 출력 · 정답 판정 없음" : "현재 시점 모델 미실행"}</p>}
    <details className="neural-provenance"><summary>이 결과의 원본 · 학습 장면 · 생성 설정</summary><dl><dt>실행</dt><dd>{execution.id}</dd><dt>원본 실행</dt><dd>{result?.source_execution_id}</dd><dt>학습 장면</dt><dd>{result?.reconstruction_execution_id}</dd><dt>Checkpoint SHA-256</dt><dd>{result?.checkpoint_sha256}</dd><dt>Adapter</dt><dd>{execution.adapter_id} {execution.adapter_version}</dd><dt>Manifest SHA-256</dt><dd>{execution.manifest_hash}</dd></dl><p>시점 편집 범위는 데모 조작 범위입니다. 미관측 영역의 정확도와 물리 센서 특성은 평가하지 않습니다.</p><a href={`#viewers?project=${encodeURIComponent(execution.project_id)}&execution=${encodeURIComponent(execution.id)}`}>실행 기록 · 원본 다운로드 →</a></details>
  </section>;
}
