/** Authored test pixels only. Mounts the production component; no simulator/API requests. */
import { useCallback, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { CarlaCameraFrame } from "../../src/pages/CarlaCameraFrame";
import type { CarlaCameraRecord } from "../../src/pages/CarlaCameraFrame";
import type { CarlaStoredZip } from "../../src/pages/carla-zip";

type FixtureFrame = { id: string; execution: string; record: CarlaCameraRecord; bytes: Uint8Array; rgba: number[] };
type Deferred = { resolve: (bytes: Uint8Array) => void; reject: (reason: Error) => void; signal: AbortSignal };
const pending = new Map<string, Deferred>();
const returned = new Set<string>();
const reads: string[] = [];
let notifyReads = () => {};
const hash = async (bytes: Uint8Array) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)))).map((v) => v.toString(16).padStart(2, "0")).join("");
const definitions = [
  ["A", "fixture-job-1", "front", 1, 4, 3, [230, 30, 40, 255]],
  ["B", "fixture-job-1", "front", 2, 4, 3, [30, 200, 60, 255]],
  ["C", "fixture-job-1", "front", 3, 4, 3, [40, 80, 230, 255]],
  ["D", "fixture-job-1", "front", 4, 4, 3, [240, 200, 30, 255]],
  ["E", "fixture-job-1", "front", 5, 4, 3, [220, 30, 200, 255]],
  ["F", "fixture-job-1", "front", 6, 4, 3, [20, 200, 210, 255]],
  ["CHASE", "fixture-job-1", "chase", 7, 8, 4, [230, 110, 30, 255]],
  ["OTHER_JOB", "fixture-job-2", "chase", 8, 6, 2, [130, 60, 210, 255]],
  ["RESIZED", "fixture-job-2", "chase", 9, 10, 6, [80, 170, 130, 255]],
] as const;

async function makeFrames() {
  const result: Record<string, FixtureFrame> = {};
  for (const [id, execution, key, frame, width, height, rgba] of definitions) {
    const bytes = new Uint8Array(width * height * 4);
    for (let i = 0; i < bytes.length; i += 4) bytes.set([rgba[2], rgba[1], rgba[0], rgba[3]], i);
    const checksum = await hash(bytes), name = `${id}-${key}/${String(frame).padStart(6, "0")}.bgra`;
    const archive: CarlaStoredZip = {
      has: (member) => member.name === name && member.sha256 === checksum,
      async read(member, signal) {
        reads.push(`read ${id} ${member.name}${returned.has(id) ? " (already resolved / download)" : " (deferred)"}`);
        notifyReads();
        // Deliberately do not reject on abort: a transport can settle after cancellation.
        signal.addEventListener("abort", () => { reads.push(`aborted ${id}; deferred completion still testable`); notifyReads(); }, { once: true });
        const value = returned.has(id) ? bytes : await new Promise<Uint8Array>((resolve, reject) => pending.set(id, { resolve, reject, signal }));
        if (value.length !== member.size_bytes || await hash(value) !== member.sha256) throw new Error("Fixture integrity failure: native frame SHA-256 differs");
        returned.add(id);
        return value;
      },
    };
    result[id] = {
      id, execution, bytes, rgba: [...rgba],
      record: { index: frame, frame, timestampNs: String(frame * 100_000_000), timestampSeconds: frame / 10,
        width, height, key, encoding: "bgra8", cacheKey: `${execution}:${key}:${frame}:${checksum}`,
        artifact: { id: `fixture-${id}`, name: `AUTHORED-TEST-${id}.zip`, media_type: "application/zip", size_bytes: bytes.length, sha256: checksum, download_url: "FIXTURE-NO-NETWORK" },
        member: { name, size_bytes: bytes.length, sha256: checksum }, archive },
    };
  }
  return result;
}

const frames = await makeFrames();
type Step = { label: string; expect: string; request?: string; resolve?: string; corrupt?: string };
const steps: Step[] = [
  { label: "Resolve A", resolve: "A", expect: "Visible RED A. Display metadata and frame 1 must match A. Requested readiness true." },
  { label: "Request B", request: "B", expect: "B pending: RED A stays visible, caption/evidence/download remain A; requested readiness false." },
  { label: "Resolve B", resolve: "B", expect: "Atomic replacement: GREEN B visible with frame 2 metadata; readiness true." },
  { label: "Request C", request: "C", expect: "C pending: GREEN B remains visible; readiness false." },
  { label: "Request D before C completes", request: "D", expect: "D pending and C aborted: GREEN B still visible; readiness false." },
  { label: "Resolve D first", resolve: "D", expect: "YELLOW D with frame 4 metadata is visible; readiness true." },
  { label: "Resolve stale C afterward", resolve: "C", expect: "Still YELLOW D/frame 4. Obsolete BLUE C must not draw or report ready true." },
  { label: "Request E", request: "E", expect: "E pending: YELLOW D remains visible; readiness false." },
  { label: "Corrupt E (hash rejection)", corrupt: "E", expect: "Image hidden, integrity error visible, E never reports ready true; no previous image presented as E." },
  { label: "Request F after error", request: "F", expect: "F pending: no visible unverified image; error may reset, readiness false." },
  { label: "Resolve F", resolve: "F", expect: "CYAN F/frame 6 appears; readiness true." },
  { label: "Switch sensor to chase", request: "CHASE", expect: "Different sensor: old CYAN front frame immediately hidden. Incoming chase dimensions 8×4." },
  { label: "Resolve chase", resolve: "CHASE", expect: "ORANGE chase frame 7 visible, canvas 8×4 and chase metadata/download identity." },
  { label: "Switch execution, same sensor", request: "OTHER_JOB", expect: "Different execution: old ORANGE frame hidden even though sensor stays chase; readiness false." },
  { label: "Resolve other execution", resolve: "OTHER_JOB", expect: "PURPLE fixture-job-2 frame 8 visible, canvas 6×2; readiness true." },
  { label: "Request same stream with different dimensions", request: "RESIZED", expect: "Hold PURPLE frame 8 at its 6×2 canvas while next 10×6 frame loads, or explicitly hide; never clear/relabel it as verified frame 9." },
  { label: "Resolve resized frame", resolve: "RESIZED", expect: "GREEN-GRAY frame 9 visible with matching metadata and 10×6 canvas; readiness true." },
];

function Fixture() {
  const [requested, setRequested] = useState(frames.A);
  const [step, setStep] = useState(0);
  const [expected, setExpected] = useState("Initial A is pending: no verified pixels are visible. Advance after the read log contains A.");
  const [readiness, setReadiness] = useState<string[]>([]);
  const [readLog, setReadLog] = useState<string[]>([]);
  const [snapshot, setSnapshot] = useState<unknown>(null);
  const [fixtureError, setFixtureError] = useState("");
  const cache = useRef(new Map<string, Uint8ClampedArray>());
  notifyReads = () => queueMicrotask(() => setReadLog([...reads]));
  const ready = useCallback((id: string, value: boolean) => setReadiness((log) => [...log, `${value ? "READY" : "WAIT"} ${id}`]), []);
  function advance() {
    const action = steps[step];
    if (!action) return;
    setFixtureError("");
    if (action.request) setRequested(frames[action.request]);
    else {
      const id = action.resolve ?? action.corrupt!;
      const deferred = pending.get(id);
      if (!deferred) { setFixtureError(`No pending read for ${id}. Wait for the component effect, then retry.`); return; }
      pending.delete(id);
      const bytes = new Uint8Array(frames[id].bytes);
      if (action.corrupt) bytes[0] ^= 255;
      deferred.resolve(bytes);
      reads.push(`settled ${id}${action.corrupt ? " with corrupt bytes" : ""}; signal.aborted=${deferred.signal.aborted}`);
      setReadLog([...reads]);
    }
    setExpected(action.expect);
    setStep((value) => value + 1);
  }
  function inspect() {
    const figure = document.querySelector(".carla-camera-frame");
    const canvas = figure?.querySelector("canvas");
    setSnapshot({
      requested: requested.id,
      width: canvas?.width, height: canvas?.height,
      visibility: canvas && getComputedStyle(canvas).visibility,
      rgba: canvas ? [...canvas.getContext("2d")!.getImageData(0, 0, 1, 1).data] : null,
      label: canvas?.getAttribute("aria-label"), caption: figure?.querySelector("figcaption")?.textContent,
      status: figure?.querySelector('[role="status"]')?.textContent,
      error: figure?.querySelector('[role="alert"]')?.textContent,
      evidence: figure?.querySelector("details")?.textContent,
      downloadLabel: figure?.querySelector("button")?.getAttribute("aria-label"),
      latestReady: readiness.at(-1), pending: [...pending.keys()],
    });
  }
  return <>
    <h1>CarlaCameraFrame · deterministic display regression</h1>
    <p className="notice">AUTHORED TEST FIXTURE ONLY. Solid in-memory BGRA pixels and a deferred archive transport. This mounts the real production component; no CARLA execution, source evidence, API call, or simulator validation is claimed.</p>
    <p id="requested" data-requested={requested.id}>Requested: {requested.id} · {requested.execution} · {requested.record.key} · frame {requested.record.frame} · {requested.record.width}×{requested.record.height}</p>
    <div className="controls"><button id="advance" disabled={step === steps.length} onClick={advance}>{step === steps.length ? "Sequence complete" : `${step + 1}. ${steps[step].label}`}</button><button id="inspect" onClick={inspect}>Inspect displayed pixels and metadata</button><button onClick={() => location.reload()}>Reset fixture</button></div>
    <p id="expected" data-completed-steps={step}>{expected}</p>
    {fixtureError && <p role="alert">{fixtureError}</p>}
    <div className="columns"><section className="panel" aria-label="Real component under test"><CarlaCameraFrame executionId={requested.execution} record={requested.record} cache={cache.current} onReady={ready} /></section>
      <section className="panel"><h2>Actual DOM/canvas snapshot</h2><pre id="display-snapshot">{JSON.stringify(snapshot, null, 2)}</pre><h2>Requested frame readiness</h2><pre id="readiness-log">{readiness.join("\n")}</pre></section></div>
    <h2>Deferred transport log</h2><pre id="read-log">{readLog.join("\n")}</pre>
  </>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
