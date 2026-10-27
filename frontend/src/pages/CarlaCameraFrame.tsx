import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { authHeaders } from "../api";
import type { CarlaStoredZip, CarlaZipArtifact as Artifact, CarlaZipMember } from "./carla-zip";

export type CarlaCameraRecord = { index: number; frame: number; timestampNs: string; timestampSeconds: number; width: number; height: number; key: string; artifact: Artifact; encoding: "bgra8" | "png"; cacheKey: string; archive?: CarlaStoredZip; member?: CarlaZipMember };

export async function verifiedArtifactBytes(executionId: string, artifact: Artifact, limit: number, signal: AbortSignal) {
  const route = `/api/v1/experiments/executions/${encodeURIComponent(executionId)}/artifacts/${encodeURIComponent(artifact.id)}`;
  if (artifact.download_url !== route || !Number.isSafeInteger(artifact.size_bytes) || artifact.size_bytes < 1 || artifact.size_bytes > limit || !/^[a-f0-9]{64}$/.test(artifact.sha256)) throw new Error("원본 파일 경로·크기·SHA-256 계약이 유효하지 않습니다.");
  const response = await fetch(route, { headers: authHeaders(), signal });
  if (!response.ok || !response.body) throw new Error(`원본 파일 조회 실패 (${response.status}).`);
  const reader = response.body.getReader(), chunks: Uint8Array[] = [];
  let count = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      count += value.byteLength;
      if (count > artifact.size_bytes || count > limit) throw new Error("원본 파일이 고정된 크기 제한을 초과했습니다.");
      chunks.push(value);
    }
  } catch (reason) { await reader.cancel(); throw reason; }
  if (count !== artifact.size_bytes) throw new Error("원본 파일 크기가 보존된 기록과 다릅니다.");
  const bytes = new Uint8Array(count); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map((value) => value.toString(16).padStart(2, "0")).join("");
  if (hash !== artifact.sha256) throw new Error("원본 파일 SHA-256이 보존된 기록과 다릅니다.");
  return bytes;
}

type CameraFrameProps = { executionId: string; record: CarlaCameraRecord; cache: Map<string, Uint8ClampedArray>; onReady: (id: string, ready: boolean) => void; onDownload?: (artifact: Artifact) => void };
type VerifiedDisplay = { record: CarlaCameraRecord; pixels: Uint8ClampedArray };

export function CarlaCameraFrame(props: CameraFrameProps) {
  // A new stream must never briefly inherit another camera's verified image.
  // Archive chunk changes within one stream deliberately keep the same canvas.
  const { executionId, record } = props;
  const stream = JSON.stringify([executionId, record.key, record.width, record.height, record.encoding]);
  return <RetainedCameraFrame key={stream} {...props} />;
}

function RetainedCameraFrame({ executionId, record, cache, onReady, onDownload }: CameraFrameProps) {
  const canvas = useRef<HTMLCanvasElement>(null), downloadController = useRef<AbortController | null>(null);
  const [displayed, setDisplayed] = useState<VerifiedDisplay | null>(null);
  const [error, setError] = useState(""), [downloadError, setDownloadError] = useState("");
  const [downloading, setDownloading] = useState(false), [slowLoad, setSlowLoad] = useState(false);
  useEffect(() => () => downloadController.current?.abort(), []);
  useLayoutEffect(() => {
    let active = true;
    const controller = new AbortController();
    setError(""); setSlowLoad(false); onReady(record.cacheKey, false);
    const pendingNotice = setTimeout(() => { if (active) setSlowLoad(true); }, 250);
    async function prepare() {
      let pixels = cache.get(record.cacheKey);
      if (!pixels) {
        const bytes = record.archive && record.member ? await record.archive.read(record.member, controller.signal) : await verifiedArtifactBytes(executionId, record.artifact, 16 * 1024 * 1024, controller.signal);
        if (!active) return;
        if (record.encoding === "bgra8") {
          if (bytes.length !== record.width * record.height * 4) throw new Error("BGRA8 원본 크기와 저장된 영상 격자가 다릅니다.");
          pixels = new Uint8ClampedArray(bytes.length);
          for (let offset = 0; offset < bytes.length; offset += 4) { pixels[offset] = bytes[offset + 2]; pixels[offset + 1] = bytes[offset + 1]; pixels[offset + 2] = bytes[offset]; pixels[offset + 3] = bytes[offset + 3]; }
        } else {
          const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: "image/png" }));
          try {
            if (!active) return;
            if (bitmap.width !== record.width || bitmap.height !== record.height) throw new Error("PNG 격자가 원래 프레임 기록과 다릅니다.");
            const temporary = document.createElement("canvas"); temporary.width = bitmap.width; temporary.height = bitmap.height;
            const context = temporary.getContext("2d");
            if (!context) throw new Error("영상 표시용 Canvas를 열지 못했습니다.");
            context.drawImage(bitmap, 0, 0); pixels = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
          } finally { bitmap.close(); }
        }
        if (!active) return;
        cache.set(record.cacheKey, pixels);
        while (cache.size > 8) cache.delete(cache.keys().next().value!);
      } else { cache.delete(record.cacheKey); cache.set(record.cacheKey, pixels); }
      if (!active) return;
      clearTimeout(pendingNotice); setSlowLoad(false);
      // Commit provenance and pixels together. The layout effect paints this
      // verified buffer before the browser can display the new caption.
      setDisplayed({ record, pixels });
    }
    prepare().catch((reason: Error) => {
      if (active) { clearTimeout(pendingNotice); setSlowLoad(false); setDisplayed(null); setError(reason.message); }
    });
    return () => { active = false; clearTimeout(pendingNotice); controller.abort(); };
  }, [executionId, record, cache, onReady]);
  useLayoutEffect(() => {
    if (!displayed) return;
    try {
      const context = canvas.current?.getContext("2d");
      if (!context) throw new Error("영상 표시용 Canvas를 열지 못했습니다.");
      context.putImageData(new ImageData(new Uint8ClampedArray(displayed.pixels), displayed.record.width, displayed.record.height), 0, 0);
      // A newer request may have arrived while this verified buffer committed.
      // Still paint its matching caption; only the requested frame can advance playback.
      if (displayed.record.cacheKey === record.cacheKey) onReady(displayed.record.cacheKey, true);
    } catch (reason) {
      setDisplayed(null); setError(reason instanceof Error ? reason.message : String(reason));
      onReady(record.cacheKey, false);
    }
  }, [displayed, record.cacheKey, onReady]);
  async function downloadFrame() {
    if (!displayed) return;
    const source = displayed.record;
    if (!source.archive || !source.member) { onDownload?.(source.artifact); return; }
    const controller = new AbortController(); downloadController.current?.abort(); downloadController.current = controller;
    setDownloading(true); setDownloadError("");
    try {
      const bytes = await source.archive.read(source.member, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: "application/octet-stream" })), anchor = document.createElement("a");
      anchor.href = url; anchor.download = source.member.name.split("/").at(-1)!; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (reason) { if (!controller.signal.aborted) setDownloadError((reason as Error).message); }
    finally { if (!controller.signal.aborted) setDownloading(false); }
  }
  const shown = displayed?.record ?? record, ready = !!displayed && shown.cacheKey === record.cacheKey;
  return <figure className="carla-camera-frame" aria-busy={!ready} data-camera-state={error ? "error" : !displayed ? "loading" : ready ? "ready" : "holding"} data-requested-frame={record.frame} data-displayed-frame={displayed?.record.frame}>
    <canvas ref={canvas} width={shown.width} height={shown.height} role="img" aria-label={`CARLA ${shown.key} 원본 프레임 ${shown.frame}`} style={{ visibility: displayed ? "visible" : "hidden" }} />
    {!displayed && !error && <span role="status" className="expansion-clock">원본 프레임 확인 중…</span>}
    {displayed && slowLoad && !ready && <span role="status" className="expansion-clock">다음 원본 확인 중 · 이전 검증 프레임 유지</span>}
    {error && <p role="alert" className="expansion-error">{error}</p>}
    {downloadError && <p role="alert" className="expansion-error">{downloadError}</p>}
    <figcaption className="expansion-clock">{shown.width} × {shown.height} 원본 격자 · Capture index {shown.index}<br />Native frame {shown.frame} · {shown.timestampNs} ns<br />{displayed ? "SHA-256 verified" : "원본 검증 대기"} · {shown.encoding === "bgra8" ? "BGRA8 → RGBA 표시" : "보존 PNG"}</figcaption>
    <details><summary>현재 프레임 원본 근거</summary><p className="expansion-clock">{shown.member?.name ?? shown.artifact.name}<br />{shown.member?.size_bytes ?? shown.artifact.size_bytes} bytes<br />SHA-256: {shown.member?.sha256 ?? shown.artifact.sha256}{shown.member && <><br />원본 ZIP: {shown.artifact.name}<br />ZIP SHA-256: {shown.artifact.sha256}</>}</p></details>
    <button className="button secondary" aria-label={`Download CARLA ${shown.key} original frame`} disabled={!displayed || downloading || !(shown.archive && shown.member) && !onDownload} onClick={() => void downloadFrame()}>{downloading ? "원본 확인 중…" : shown.encoding === "bgra8" ? "현재 BGRA8 원본 다운로드" : "현재 PNG 원본 다운로드"}</button>
  </figure>;
}
