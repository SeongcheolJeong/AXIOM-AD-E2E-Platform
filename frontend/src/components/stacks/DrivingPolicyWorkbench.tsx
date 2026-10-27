import { useState } from "react";
import { api, useResource } from "../../api";

type Point = [number, number];
type Output = {
  sample_token: string;
  timestamp_us: number;
  modes: Record<string, Point[]>;
};
type Inspection = {
  receipt_hash: string;
  input_hash: string;
  origin_verification: string;
  verification_verdict: string;
  outputs: Output[];
  model: { checkpoint_sha256: string };
  limitations: string[];
};
type Check = {
  input_hash: string;
  structural_status: string;
  native_execution_status: string;
  file_identity_status: string;
  frames: number;
  camera_observations: number;
  limitations: string[];
};

export default function DrivingPolicyWorkbench() {
  const capabilities = useResource<{ native_execution_status: string }>(
    "/driving-policies/capabilities",
  );
  const [check, setCheck] = useState<Check | null>(null),
    [result, setResult] = useState<Inspection | null>(null);
  const [frame, setFrame] = useState(0),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function inspect(file: File | undefined, kind: "input" | "result") {
    if (!file) return;
    setError("");
    setCheck(null);
    setResult(null);
    setFrame(0);
    setBusy(true);
    try {
      if (file.size > 2 * 1024 * 1024)
        throw new Error("Select a JSON receipt smaller than 2 MiB");
      const raw = await file.text();
      JSON.parse(raw); // Preserve native numeric representation for canonical hash checks.
      if (kind === "input")
        setCheck(
          await api<Check>("/driving-policies/preflight", {
            method: "POST",
            body: raw,
          }),
        );
      else
        setResult(
          await api<Inspection>("/driving-policies/inspect-result", {
            method: "POST",
            body: raw,
          }),
        );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to inspect input");
    } finally {
      setBusy(false);
    }
  }
  const output = result?.outputs[frame];
  const paths = output ? Object.entries(output.modes) : [];
  const points: Point[] = [[0, 0], ...paths.flatMap(([, p]) => p)];
  const maxX = Math.max(1, ...points.map((p) => Math.abs(p[0]))),
    maxY = Math.max(1, ...points.map((p) => Math.abs(p[1])));
  const scale = 140 / Math.max(maxX, maxY);
  const colors = ["#b44d36", "#416db6", "#367951"];
  return (
    <section
      className="panel verification-panel"
      aria-label="Recorded driving policies"
    >
      <h2>Learned driving policy inputs and trajectories</h2>
      <p>
        Check a pinned six-camera sequence, then inspect a native VAD-Tiny
        prediction receipt.
      </p>
      <p data-testid="driving-native-status">
        Native execution:{" "}
        {capabilities.data?.native_execution_status ?? "checking"}
      </p>
      <p>
        Research connector. This screen checks uploaded receipts; execution
        origin is self-reported. It does not submit GPU jobs, approve commercial
        data rights, or evaluate driving safety.
      </p>
      {capabilities.error && <p role="alert">{capabilities.error}</p>}
      <div className="verification-grid">
        <label>
          Prepared driving input JSON
          <input
            aria-label="Prepared driving input JSON"
            type="file"
            accept="application/json,.json"
            disabled={busy}
            onChange={(e) => void inspect(e.target.files?.[0], "input")}
          />
        </label>
        <label>
          Native driving result JSON
          <input
            aria-label="Native driving result JSON"
            type="file"
            accept="application/json,.json"
            disabled={busy}
            onChange={(e) => void inspect(e.target.files?.[0], "result")}
          />
        </label>
      </div>
      {busy && <p role="status">Checking receipt…</p>}
      {error && <p role="alert">{error}</p>}
      {check && (
        <div data-testid="driving-input-check">
          <p>
            Structure: {check.structural_status} · {check.frames} frames ·{" "}
            {check.camera_observations} camera observations
          </p>
          <p>
            Native execution: {check.native_execution_status} · Files:{" "}
            {check.file_identity_status}
          </p>
          <code>{check.input_hash}</code>
          <ul>
            {check.limitations.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {result && (
        <div data-testid="driving-result-inspection">
          <p>
            Origin: {result.origin_verification} · Verification:{" "}
            {result.verification_verdict}
          </p>
          <p>
            Receipt SHA-256: <code>{result.receipt_hash}</code>
          </p>
          <p>
            Input SHA-256: <code>{result.input_hash}</code>
          </p>
          <p>
            Checkpoint SHA-256: <code>{result.model.checkpoint_sha256}</code>
          </p>
          <label>
            Recorded frame
            <select
              aria-label="Recorded driving frame"
              value={frame}
              onChange={(e) => setFrame(Number(e.target.value))}
            >
              {result.outputs.map((x, i) => (
                <option key={x.sample_token} value={i}>
                  {i + 1} · {x.sample_token} · {x.timestamp_us} µs
                </option>
              ))}
            </select>
          </label>
          <p>
            Current LiDAR XY, meters. Each mode predicts six positions at
            0.5-second intervals. No future-label route selection.
          </p>
          <svg
            role="img"
            aria-label="Predicted driving trajectory modes"
            viewBox="0 0 380 360"
            style={{ width: "100%", maxWidth: 520, background: "#f4f6f1" }}
          >
            <line x1="20" x2="360" y1="180" y2="180" stroke="#ccd3c8" />
            <line x1="190" x2="190" y1="15" y2="340" stroke="#ccd3c8" />
            <circle cx="190" cy="180" r="5" fill="#223c2a" />
            {paths.map(([name, trajectory], i) => (
              <g key={name}>
                <polyline
                  points={[[0, 0], ...trajectory]
                    .map((p) => `${190 + p[0] * scale},${180 - p[1] * scale}`)
                    .join(" ")}
                  fill="none"
                  stroke={colors[i]}
                  strokeWidth="3"
                />
                {trajectory.map((p, j) => (
                  <circle
                    key={j}
                    cx={190 + p[0] * scale}
                    cy={180 - p[1] * scale}
                    r="3"
                    fill={colors[i]}
                  >
                    <title>
                      {name} · {(j + 1) * 0.5}s · ({p[0].toFixed(2)},{" "}
                      {p[1].toFixed(2)}) m
                    </title>
                  </circle>
                ))}
                <text x={20 + i * 115} y="355" fill={colors[i]}>
                  {name}
                </text>
              </g>
            ))}
            <text x="350" y="174">
              +X
            </text>
            <text x="197" y="25">
              +Y
            </text>
          </svg>
          <ul>
            {result.limitations.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
