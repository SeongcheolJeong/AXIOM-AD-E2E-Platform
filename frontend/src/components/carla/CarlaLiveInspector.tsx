import { useEffect, useState } from "react";
import { ArrowDownToLine, ChevronLeft, ChevronRight } from "lucide-react";
import type { RunDetail } from "../../types";
import { formatBytes } from "../../api";
import {
  liveArtifact,
  readLiveArtifact,
  validateLiveTrace,
  validateLiveManifest,
} from "./CarlaLiveData";
import type {
  CarlaLiveTrace,
  CarlaLiveManifest,
  CarlaVehicleControl,
} from "./CarlaTypes";
import CarlaSavedContext from "./CarlaSavedContext";
import CarlaLivePoints from "./CarlaLivePoints";
import StackTickStages from "../stacks/StackTickStages";
import StackV2Signals from "../stacks/StackV2Signals";
import "../../stacks.css";
import "./carla.css";

const fmt = (value: number | null | undefined, suffix = "") =>
  value == null ? "Unavailable" : `${Number(value.toFixed(5))}${suffix}`;
const human = (value: string) => value.replaceAll("_", " ");
function Signals({
  trace,
  selected,
}: {
  trace: CarlaLiveTrace;
  selected: number;
}) {
  const signals = [
    {
      name: "Ego speed around each command interval",
      unit: "m/s",
      series: [
        {
          label: "Input ego speed",
          color: "#698d79",
          values: trace.ticks.map(
            (tick) => tick.packet?.ego?.speed_mps ?? null,
          ),
        },
        {
          label: "Next physics ego speed",
          color: "#bd7951",
          values: trace.ticks.map((tick) => tick.next_ego?.speed_mps ?? null),
        },
      ],
    },
    {
      name: "Brake request and next-frame control readback",
      unit: "normalized",
      series: [
        {
          label: "Requested CARLA brake",
          color: "#47788b",
          values: trace.ticks.map(
            (tick) => tick.requested_carla_control?.brake ?? null,
          ),
        },
        {
          label: "Observed control brake",
          color: "#9c7189",
          values: trace.ticks.map(
            (tick) => tick.observed_control?.brake ?? null,
          ),
        },
      ],
    },
  ];
  return (
    <div className="carla-live-signals">
      {signals.map((signal) => {
        const maximum = Math.max(
          1,
          ...signal.series.flatMap((series) =>
            series.values.filter((value): value is number => value !== null),
          ),
        );
        const x = (index: number) =>
          38 + (index / Math.max(trace.ticks.length - 1, 1)) * 622;
        const y = (value: number) => 133 - (value / maximum) * 110;
        return (
          <figure key={signal.name}>
            <figcaption>{signal.name}</figcaption>
            <svg role="img" aria-label={signal.name} viewBox="0 0 680 160">
              <line x1="38" y1="133" x2="660" y2="133" stroke="#cad3ca" />
              <line
                x1={x(selected)}
                x2={x(selected)}
                y1="20"
                y2="133"
                stroke="#b4b7b0"
                strokeDasharray="3 3"
              />
              <text x="5" y="26">
                {fmt(maximum)}
              </text>
              <text x="20" y="136">
                0
              </text>
              <text x="38" y="152">
                Interval 1
              </text>
              <text x="595" y="152">
                {trace.expected_intervals}
              </text>
              {signal.series.map((series) => {
                let path = "",
                  prior = false;
                for (const [index, value] of series.values.entries()) {
                  if (value === null) {
                    prior = false;
                    continue;
                  }
                  path += `${prior ? "L" : "M"}${x(index)},${y(value)} `;
                  prior = true;
                }
                return (
                  <g
                    key={series.label}
                    data-series={series.label}
                    data-valid-points={
                      series.values.filter((value) => value !== null).length
                    }
                  >
                    <path
                      d={path}
                      stroke={series.color}
                      fill="none"
                      strokeWidth="2"
                    />
                    {series.values.map((value, index) =>
                      value === null ? null : (
                        <circle
                          key={index}
                          cx={x(index)}
                          cy={y(value)}
                          r={index === selected ? 3.5 : 1.6}
                          fill={series.color}
                        />
                      ),
                    )}
                  </g>
                );
              })}
            </svg>
            <div className="carla-signal-legend">
              {signal.series.map((series) => (
                <span key={series.label}>
                  <i style={{ background: series.color }} />
                  {series.label}
                </span>
              ))}
              <span>{signal.unit}</span>
            </div>
          </figure>
        );
      })}
    </div>
  );
}
function ControlReadout({
  title,
  value,
}: {
  title: string;
  value: CarlaVehicleControl | null;
}) {
  return (
    <div>
      <h4>{title}</h4>
      {value ? (
        <dl>
          {(["throttle", "brake", "steer"] as const).map((key) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{fmt(value[key])}</dd>
            </div>
          ))}
          <div>
            <dt>reverse / hand brake</dt>
            <dd>
              {String(value.reverse)} / {String(value.hand_brake)}
            </dd>
          </div>
        </dl>
      ) : (
        <p className="muted">Unavailable</p>
      )}
    </div>
  );
}
export default function CarlaLiveInspector({ run }: { run: RunDetail }) {
  const key = `${run.id}/${run.provenance.input_hash}/${run.execution_context?.context_hash}/${JSON.stringify(run.artifacts)}`;
  const [attempt, setAttempt] = useState(0),
    [selected, setSelected] = useState(0);
  const [state, setState] = useState<{
    key: string;
    trace: CarlaLiveTrace | null;
    manifest: CarlaLiveManifest | null;
    error: string;
    traceUrl: string;
    manifestUrl: string;
  }>({
    key: "",
    trace: null,
    manifest: null,
    error: "",
    traceUrl: "",
    manifestUrl: "",
  });
  useEffect(() => {
    let active = true;
    const urls: string[] = [];
    const controller = new AbortController(),
      timer = setTimeout(() => controller.abort(), 20000);
    setSelected(0);
    setState({
      key,
      trace: null,
      manifest: null,
      error: "",
      traceUrl: "",
      manifestUrl: "",
    });
    void (async () => {
      const traceArtifact = liveArtifact(
        run,
        "carla-stack-trace.json",
        "live_stack_trace",
        20 * 1024 * 1024,
      );
      const manifestArtifact = liveArtifact(
        run,
        "carla-stack-manifest.json",
        "live_stack_manifest",
        4 * 1024 * 1024,
      );
      const [traceBytes, manifestBytes] = await Promise.all([
        readLiveArtifact(traceArtifact, controller.signal),
        readLiveArtifact(manifestArtifact, controller.signal),
      ]);
      const trace = validateLiveTrace(
        JSON.parse(
          new TextDecoder("utf-8", { fatal: true }).decode(traceBytes),
        ),
        run,
      );
      const manifest = validateLiveManifest(
        JSON.parse(
          new TextDecoder("utf-8", { fatal: true }).decode(manifestBytes),
        ),
        run,
        trace,
      );
      if (!active) return;
      const traceUrl = URL.createObjectURL(
          new Blob([traceBytes], { type: "application/json" }),
        ),
        manifestUrl = URL.createObjectURL(
          new Blob([manifestBytes], { type: "application/json" }),
        );
      urls.push(traceUrl, manifestUrl);
      setState({ key, trace, manifest, error: "", traceUrl, manifestUrl });
    })()
      .catch((error: unknown) => {
        if (active)
          setState({
            key,
            trace: null,
            manifest: null,
            error: controller.signal.aborted
              ? "Live evidence verification timed out."
              : (error as Error).message,
            traceUrl: "",
            manifestUrl: "",
          });
      })
      .finally(() => clearTimeout(timer));
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
      urls.forEach(URL.revokeObjectURL);
    };
  }, [key, attempt]);
  const trace = state.key === key ? state.trace : null,
    manifest = state.key === key ? state.manifest : null;
  const tick = trace?.ticks[Math.min(selected, trace.ticks.length - 1)];
  return (
    <section
      className="panel carla-live-inspector"
      data-testid="carla-live-inspector"
    >
      <div className="carla-live-heading">
        <div>
          <span className="eyebrow">SENSOR → SHARED STACK → PHYSICS</span>
          <h2>Live CARLA control evidence</h2>
        </div>
        <span className="mono">20 Hz · 0.05 s</span>
      </div>
      {run.execution_context && (
        <CarlaSavedContext context={run.execution_context} />
      )}
      {state.key === key && state.error ? (
        <div>
          <p className="inline-error" role="alert">
            Live evidence rejected: {state.error}
          </p>
          <button
            className="button secondary"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Retry live evidence
          </button>
        </div>
      ) : !trace || !manifest || !tick ? (
        <p role="status">
          Verifying live trace, manifest, hashes and run context…
        </p>
      ) : (
        <div data-testid="carla-live-verified">
          <div className="carla-live-counts">
            <strong>
              {trace.status === "completed"
                ? "Complete recorded sequence"
                : "Partial recorded sequence"}
            </strong>
            <span>
              {manifest.acknowledged_commands} / {trace.expected_intervals}{" "}
              command ACKs
            </span>
            <span>
              {manifest.next_frame_observed_commands} /{" "}
              {trace.expected_intervals} next-frame observations
            </span>
            <span>
              {manifest.observed_frames} / {manifest.expected_observations}{" "}
              physics frames
            </span>
            <span>
              {manifest.retained_source_observations} /{" "}
              {manifest.expected_observations} retained LiDAR + ego observations
            </span>
          </div>
          <p className="carla-subtle">
            N commands require N+1 observations. ACK records command delivery;
            next-frame readback does not prove achieved actuator performance.
            Geometric obstacle observations do not identify objects or establish
            free space. Metric verdict: <strong>{run.verdict}</strong>.
          </p>
          {manifest.error && (
            <p className="inline-error" role="alert">
              {manifest.failure_phase}: {manifest.error}
            </p>
          )}
          {manifest.cleanup_errors.length > 0 && (
            <p className="inline-error">
              Cleanup was not confirmed: {manifest.cleanup_errors.join(" · ")}
            </p>
          )}
          <Signals trace={trace} selected={selected} />
          <StackV2Signals
            samples={trace.ticks.map((item) => ({
              index: item.sequence,
              output: item.output,
            }))}
            selected={selected}
            onSelect={setSelected}
          />
          <div className="carla-tick-selection">
            <button
              className="icon-button"
              aria-label="Previous live interval"
              disabled={selected === 0}
              onClick={() => setSelected((value) => value - 1)}
            >
              <ChevronLeft size={18} />
            </button>
            <label>
              Command interval{" "}
              <strong>
                {selected + 1} / {trace.expected_intervals}
              </strong>
              <input
                aria-label="Live command interval"
                type="range"
                min={0}
                max={trace.expected_intervals - 1}
                step={1}
                value={selected}
                onChange={(event) => setSelected(Number(event.target.value))}
              />
            </label>
            <button
              className="icon-button"
              aria-label="Next live interval"
              disabled={selected === trace.expected_intervals - 1}
              onClick={() => setSelected((value) => value + 1)}
            >
              <ChevronRight size={18} />
            </button>
          </div>
          <div className="carla-tick-identity">
            <strong>{human(tick.status)}</strong>
            <span>
              Source world frame {tick.source_world_frame ?? "unavailable"} ·{" "}
              {fmt(tick.source_time_s, " s")}
            </span>
            <span>
              Kernel timestamp{" "}
              <code>{tick.packet?.at_ns ?? "unavailable"}</code> ns
            </span>
          </div>
          {tick.error && (
            <p className="inline-error" role="alert">
              {tick.error}
            </p>
          )}
          <StackTickStages tick={{ output: tick.output }} live />
          <section
            className="carla-actuation"
            data-testid="carla-live-actuation"
          >
            <div className="carla-actuation-title">
              <h3>04 · Command delivery and next physics observation</h3>
              <strong data-testid="carla-application-status">
                {human(tick.application_status)}
              </strong>
            </div>
            <p className="carla-subtle">
              Kernel steering is positive left. CARLA steering is reflected to
              positive right before send. Readback comes from get_control()
              after the next snapshot under sole tick ownership.
            </p>
            <div className="carla-actuation-grid">
              <ControlReadout
                title="Requested CARLA control"
                value={tick.requested_carla_control}
              />
              <ControlReadout
                title="Next-frame control readback"
                value={tick.observed_control}
              />
              <div>
                <h4>Next physics ego observation</h4>
                <dl>
                  <div>
                    <dt>ACK</dt>
                    <dd>
                      {tick.control_acknowledged ? "Recorded" : "Not recorded"}
                    </dd>
                  </div>
                  <div>
                    <dt>Batch RPC actor / error</dt>
                    <dd>
                      {tick.control_rpc_response
                        ? `${tick.control_rpc_response.actor_id ?? "Unavailable"} / ${tick.control_rpc_response.error ?? "No reported error"}`
                        : "No response retained"}
                    </dd>
                  </div>
                  <div>
                    <dt>world frame / simulation time</dt>
                    <dd>
                      {tick.next_world_frame ?? "Unavailable"} /{" "}
                      {fmt(tick.next_time_s, " s")}
                    </dd>
                  </div>
                  <div>
                    <dt>ego speed</dt>
                    <dd>{fmt(tick.next_ego?.speed_mps, " m/s")}</dd>
                  </div>
                  <div>
                    <dt>RH x / y / yaw</dt>
                    <dd>
                      {tick.next_ego
                        ? `${fmt(tick.next_ego.x)} / ${fmt(tick.next_ego.y)} / ${fmt(tick.next_ego.yaw)}`
                        : "Unavailable"}
                    </dd>
                  </div>
                </dl>
              </div>
            </div>
          </section>
          <CarlaLivePoints run={run} points={tick.points} />
          <details className="carla-source-details">
            <summary>
              Original source identity and timing · interval {selected + 1}
            </summary>
            <p className="carla-subtle">
              Original MCAP message SHA, raw native XYZI SHA and consumed ego
              XYZ SHA describe different bytes. This inspector verifies
              trace/point bytes; source message contents remain available in the
              original MCAP artifact.
            </p>
            <div className="carla-context-grid">
              {(["lidar", "ego"] as const).map((role) => (
                <div key={role}>
                  <strong>
                    {role === "ego" ? "Ego/map oracle" : "LiDAR source"}
                  </strong>
                  {tick.source_refs[role] ? (
                    <pre>{JSON.stringify(tick.source_refs[role], null, 2)}</pre>
                  ) : (
                    <p>No source reference retained.</p>
                  )}
                </div>
              ))}
            </div>
          </details>
          <details>
            <summary>
              Inspect all {trace.expected_intervals} scheduled intervals
            </summary>
            <div className="carla-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Interval</th>
                    <th>Status</th>
                    <th>Source → next frame</th>
                    <th>Decision</th>
                    <th>Delivery</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.ticks.map((item) => (
                    <tr key={item.sequence}>
                      <td>
                        <button
                          className="button text"
                          onClick={() => setSelected(item.sequence)}
                        >
                          {item.sequence + 1}
                        </button>
                      </td>
                      <td>{human(item.status)}</td>
                      <td>
                        {item.source_world_frame ?? "—"} →{" "}
                        {item.next_world_frame ?? "—"}
                      </td>
                      <td>
                        {item.output
                          ? human(item.output.decision.mode)
                          : "Unavailable"}
                      </td>
                      <td>{human(item.application_status)}</td>
                      <td>{item.error ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <details>
            <summary>Recorded limitations</summary>
            <ul>
              {manifest.limitations.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </details>
          <details>
            <summary>
              Worker sensor recipe and command transport readback
            </summary>
            <p className="carla-subtle">
              Desired attributes remain in the fixed context. Native typed
              readback below preserves float32 representation and is not a
              calibration measurement.
            </p>
            <pre>
              {JSON.stringify(
                {
                  actual_lidar_attributes:
                    manifest.actual_lidar_attributes ?? null,
                  control_transport: manifest.control_transport ?? null,
                  ego_actor_id: manifest.ego_actor_id,
                },
                null,
                2,
              )}
            </pre>
          </details>
          <div className="carla-live-downloads">
            <a
              className="button secondary"
              href={state.traceUrl}
              download="carla-stack-trace.json"
            >
              <ArrowDownToLine size={15} />
              Download verified live trace JSON
            </a>
            <a
              className="button secondary"
              href={state.manifestUrl}
              download="carla-stack-manifest.json"
            >
              <ArrowDownToLine size={15} />
              Download verified live manifest
            </a>
            <span>
              {formatBytes(
                run.artifacts.find(
                  (item) => item.name === "carla-stack-trace.json",
                )!.size_bytes,
              )}{" "}
              · verified SHA-256
            </span>
          </div>
        </div>
      )}
    </section>
  );
}
