import { useEffect, useState } from "react";
import { ArrowDownToLine, RotateCcw } from "lucide-react";
import { authHeaders, formatBytes } from "../api";
import type { Artifact, RunDetail } from "../types";
import { PanelHeading } from "./ui";
import "./startup.css";

const MAX_BYTES = 2 * 1024 * 1024;
const POLICY_VERSION = "carla-startup-stability/1";
type RecordValue = Record<string, unknown>;
type Issue = { field: string; observed: string };
type ActorSample = {
  actor_id: number;
  stable: boolean;
  invalid_fields: Issue[];
  velocity_mps?: { x: number | null; y: number | null; z: number | null };
  derived?: { linear_speed_mps?: number | null };
};
type Sample = {
  tick: number;
  world_frame: number | null;
  tick_returned_frame: number | null;
  elapsed_seconds: number | null;
  delta_seconds: number | null;
  actors: { ego?: ActorSample; lead?: ActorSample };
  invalid_fields: Issue[];
  stable: boolean;
  stable_streak: number;
};
type StartupReport = {
  schema_version: "1";
  status: "passed" | "failed";
  reason_code: string;
  reason: string;
  policy: {
    version: string;
    min_ticks: number;
    max_ticks: number;
    consecutive_stable_samples: number;
    max_linear_speed_mps: number;
    max_vertical_speed_mps: number;
  };
  trace: Sample[];
  stable_streak: number;
  completed_ticks: number;
  elapsed_simulated_seconds: number;
  elapsed_wall_seconds: number;
  context: { server_version: string; map_name: string };
  limitations: string[];
};

function invalid(message: string): never {
  throw new Error(`Startup evidence rejected: ${message}`);
}
function object(value: unknown): RecordValue {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    invalid("expected a JSON object.");
  return value as RecordValue;
}
function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function integer(
  value: unknown,
  minimum = 0,
  maximum = Number.MAX_SAFE_INTEGER,
): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= minimum &&
    value <= maximum
  );
}
function boundedText(value: unknown, maximum = 4000): value is string {
  return (
    typeof value === "string" && value.length > 0 && value.length <= maximum
  );
}
function checkJson(value: unknown, depth = 0): void {
  if (depth > 16) invalid("JSON nesting exceeds its bound.");
  if (typeof value === "number" && !finite(value)) invalid("nonfinite number.");
  if (value !== null && typeof value === "object")
    for (const child of Object.values(value)) checkJson(child, depth + 1);
}
function issues(value: unknown): Issue[] {
  if (!Array.isArray(value) || value.length > 100)
    invalid("invalid_fields is not bounded.");
  for (const issue of value) {
    const item = object(issue);
    if (!boundedText(item.field, 500) || !boundedText(item.observed, 1000))
      invalid("invalid field description.");
  }
  return value as Issue[];
}
function nullableNumber(value: unknown): boolean {
  return value === null || finite(value);
}

export function validateStartupReport(
  value: unknown,
  run: RunDetail,
): StartupReport {
  checkJson(value);
  const report = object(value),
    policy = object(report.policy),
    context = object(report.context);
  if (report.schema_version !== "1" || policy.version !== POLICY_VERSION)
    invalid("unsupported schema or policy version.");
  if (report.status !== "passed" && report.status !== "failed")
    invalid("startup report is not terminal.");
  if (!boundedText(report.reason) || !boundedText(report.reason_code, 100))
    invalid("missing terminal reason.");
  if (
    run.adapter_id !== "carla" ||
    context.adapter_version !== run.provenance.adapter_version ||
    context.scenario_id !== run.scenario_id ||
    context.scenario_revision !== run.scenario_revision ||
    context.seed !== run.provenance.seed ||
    context.stage !== "rest_before_initial_target_velocity" ||
    run.scenario_snapshot.id !== run.scenario_id ||
    run.scenario_snapshot.revision !== run.scenario_revision ||
    run.scenario_snapshot.seed !== run.provenance.seed ||
    !boundedText(context.server_version, 200) ||
    !boundedText(context.map_name, 500) ||
    context.map_name.split("/").pop() !== run.scenario_snapshot.map_name
  )
    invalid("report context does not match the selected run.");
  const speeds = object(context.requested_initial_speeds_mps);
  if (
    speeds.ego !== run.scenario_snapshot.parameters.ego_speed_mps ||
    speeds.lead !== run.scenario_snapshot.parameters.target_speed_mps
  )
    invalid("requested initial speeds differ from the scenario snapshot.");
  if (
    !integer(policy.min_ticks, 1, 100) ||
    !integer(policy.max_ticks, policy.min_ticks as number, 100) ||
    !integer(policy.consecutive_stable_samples, 2, policy.max_ticks as number)
  )
    invalid("invalid tick policy.");
  const positiveBounds = [
    "max_linear_speed_mps",
    "max_vertical_speed_mps",
    "max_angular_speed_deg_s",
    "max_position_step_m",
    "max_rotation_step_deg",
    "max_acceleration_mps2",
    "max_wall_time_s",
    "max_tick_rpc_time_s",
    "clock_tolerance_s",
  ];
  for (const key of positiveBounds)
    if (!finite(policy[key]) || policy[key] <= 0)
      invalid(`invalid policy ${key}.`);
  if (
    (policy.max_wall_time_s as number) > 60 ||
    (policy.max_tick_rpc_time_s as number) > 10 ||
    !finite(report.fixed_delta_seconds) ||
    report.fixed_delta_seconds <= 0 ||
    report.fixed_delta_seconds * (policy.max_ticks as number) > 5.000001 ||
    !finite(report.max_simulated_duration_s) ||
    Math.abs(
      report.max_simulated_duration_s -
        report.fixed_delta_seconds * (policy.max_ticks as number),
    ) > 1e-6
  )
    invalid("startup time bound is inconsistent.");
  if (
    !Array.isArray(report.trace) ||
    report.trace.length > (policy.max_ticks as number) ||
    !integer(report.completed_ticks, 0, 100) ||
    report.completed_ticks !== report.trace.length ||
    !integer(report.stable_streak, 0, report.completed_ticks) ||
    !finite(report.elapsed_simulated_seconds) ||
    report.elapsed_simulated_seconds < 0 ||
    !finite(report.elapsed_wall_seconds) ||
    report.elapsed_wall_seconds < 0
  )
    invalid("trace counters or duration are invalid.");
  const ids = object(report.actor_ids);
  if (!integer(ids.ego) || !integer(ids.lead) || ids.ego === ids.lead)
    invalid("actor identities are invalid.");
  for (let index = 0; index < report.trace.length; index++) {
    const row = object(report.trace[index]);
    if (
      row.tick !== index + 1 ||
      !integer(row.stable_streak, 0, index + 1) ||
      typeof row.stable !== "boolean" ||
      !nullableNumber(row.elapsed_seconds) ||
      !nullableNumber(row.delta_seconds) ||
      (row.world_frame !== null &&
        !integer(row.world_frame, Number.MIN_SAFE_INTEGER)) ||
      (row.tick_returned_frame !== null &&
        !integer(row.tick_returned_frame, Number.MIN_SAFE_INTEGER))
    )
      invalid("malformed tick row.");
    issues(row.invalid_fields);
    const actors = object(row.actors);
    if (Object.keys(actors).some((role) => role !== "ego" && role !== "lead"))
      invalid("unexpected actor role.");
    for (const role of ["ego", "lead"] as const) {
      if (actors[role] === undefined) continue; // A clock error can precede actor sampling.
      const actor = object(actors[role]);
      if (actor.actor_id !== ids[role] || typeof actor.stable !== "boolean")
        invalid("actor sample identity is invalid.");
      const badFields = issues(actor.invalid_fields);
      if (actor.velocity_mps !== undefined) {
        const velocity = object(actor.velocity_mps);
        if (["x", "y", "z"].some((axis) => !nullableNumber(velocity[axis])))
          invalid("invalid velocity vector.");
        if (
          badFields.length === 0 &&
          ["x", "y", "z"].some((axis) => velocity[axis] === null)
        )
          invalid("null velocity lacks an invalid-field explanation.");
      } else if (badFields.length === 0)
        invalid(
          "actor velocity is absent without an invalid-field explanation.",
        );
      if (actor.derived !== undefined) {
        const derived = object(actor.derived);
        if (
          !nullableNumber(derived.linear_speed_mps) ||
          (derived.linear_speed_mps === null && badFields.length === 0) ||
          (finite(derived.linear_speed_mps) && derived.linear_speed_mps < 0)
        )
          invalid("invalid derived linear speed.");
      } else if (badFields.length === 0)
        invalid(
          "derived speed is absent without an invalid-field explanation.",
        );
    }
  }
  if (
    !Array.isArray(report.limitations) ||
    report.limitations.length > 30 ||
    report.limitations.some((item) => !boundedText(item))
  )
    invalid("invalid limitations.");
  if (report.status === "passed") {
    const last = report.trace.at(-1) as Sample | undefined;
    if (
      !last ||
      report.completed_ticks < (policy.min_ticks as number) ||
      report.stable_streak < (policy.consecutive_stable_samples as number) ||
      last.stable_streak !== report.stable_streak ||
      !last.stable ||
      last.invalid_fields.length ||
      !last.actors.ego?.stable ||
      !last.actors.lead?.stable ||
      last.actors.ego.invalid_fields.length ||
      last.actors.lead.invalid_fields.length
    )
      invalid("reported settlement contradicts its final sample/counters.");
  }
  return value as StartupReport;
}

async function verifiedReport(
  run: RunDetail,
  artifact: Artifact,
  signal: AbortSignal,
) {
  const matching = run.artifacts.filter(
    (item) =>
      item.kind === "startup_manifest" && item.name === "carla-startup.json",
  );
  if (
    matching.length !== 1 ||
    run.artifacts.filter((item) => item.id === artifact.id).length !== 1 ||
    artifact.run_id !== run.id ||
    !/^[A-Za-z0-9_-]{1,100}$/.test(artifact.id) ||
    artifact.media_type !== "application/json" ||
    !integer(artifact.size_bytes, 1, MAX_BYTES) ||
    !/^[a-f0-9]{64}$/.test(artifact.sha256)
  )
    invalid("artifact identity, content type or size is invalid.");
  const path = `/api/v1/artifacts/${artifact.id}/download`;
  if (artifact.download_url !== path)
    invalid("artifact URL differs from its same-origin identity.");
  const response = await fetch(path, {
    headers: authHeaders(),
    signal,
    redirect: "error",
  });
  if (!response.ok)
    throw new Error(`Startup evidence unavailable (${response.status}).`);
  if (!response.body) invalid("empty artifact response.");
  const length = response.headers.get("content-length");
  if (
    length !== null &&
    (!/^\d+$/.test(length) || Number(length) !== artifact.size_bytes)
  )
    invalid("response length differs from its descriptor.");
  const buffer = new Uint8Array(artifact.size_bytes),
    reader = response.body.getReader();
  let offset = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      if (offset + chunk.value.length > buffer.length)
        invalid("response exceeds its declared size.");
      buffer.set(chunk.value, offset);
      offset += chunk.value.length;
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  if (offset !== artifact.size_bytes)
    invalid("artifact size differs from its descriptor.");
  const hash = [
    ...new Uint8Array(await crypto.subtle.digest("SHA-256", buffer)),
  ]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  if (hash !== artifact.sha256) invalid("artifact SHA-256 mismatch.");
  const report = validateStartupReport(
    JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(buffer)),
    run,
  );
  return { report, bytes: buffer };
}

function format(value: number | null | undefined) {
  return value == null
    ? "—"
    : Math.abs(value) >= 10000
      ? value.toExponential(2)
      : value.toFixed(3);
}
function sampleValue(
  row: Sample,
  role: "ego" | "lead",
  kind: "vertical" | "linear",
): number | null {
  if (row.invalid_fields.length) return null;
  const actor = row.actors[role];
  if (!actor) return null;
  const field =
    kind === "vertical"
      ? `${role}.velocity_mps.z`
      : `${role}.derived.linear_speed_mps`;
  if (
    actor.invalid_fields.some(
      (issue) =>
        issue.field === role ||
        issue.field === field ||
        (kind === "linear" && issue.field.startsWith(`${role}.velocity_mps.`)),
    )
  )
    return null;
  return (
    (kind === "vertical"
      ? actor.velocity_mps?.z
      : actor.derived?.linear_speed_mps) ?? null
  );
}
function evaluated(row: Sample) {
  return (
    !row.invalid_fields.length &&
    [row.actors.ego, row.actors.lead].every(
      (actor) => actor && !actor.invalid_fields.length,
    )
  );
}
type Series = { name: string; color: string; values: (number | null)[] };
function TraceChart({
  title,
  unit,
  series,
  threshold,
  signed = false,
}: {
  title: string;
  unit: string;
  series: Series[];
  threshold: number;
  signed?: boolean;
}) {
  const maximum = Math.max(
    threshold,
    ...series.flatMap((item) =>
      item.values
        .filter((value): value is number => value !== null)
        .map(Math.abs),
    ),
    0.001,
  );
  const count = Math.max(...series.map((item) => item.values.length), 1);
  const x = (index: number) => 52 + (index / Math.max(1, count - 1)) * 520;
  // Divide before subtraction/multiplication, keeping even very large finite observations drawable.
  const y = (value: number) =>
    signed ? 85 - (value / maximum) * 60 : 145 - (value / maximum) * 120;
  return (
    <figure className="startup-chart">
      <figcaption>
        {title} <span>{unit}</span>
      </figcaption>
      <svg
        viewBox="0 0 600 180"
        role="img"
        aria-label={`${title} by startup tick`}
      >
        <title>{title} by startup tick; missing observations remain gaps</title>
        <line x1="52" x2="575" y1="145" y2="145" className="startup-axis" />
        <line x1="52" x2="52" y1="20" y2="145" className="startup-axis" />
        <text x="47" y="28" textAnchor="end">
          {format(maximum)}
        </text>
        <text x="47" y="147" textAnchor="end">
          {signed ? format(-maximum) : "0"}
        </text>
        <text x="52" y="168">
          tick 1
        </text>
        <text x="572" y="168" textAnchor="end">
          tick {count}
        </text>
        {(signed ? [threshold, -threshold] : [threshold]).map((value) => (
          <line
            key={value}
            x1="52"
            x2="575"
            y1={y(value)}
            y2={y(value)}
            className="startup-threshold"
          />
        ))}
        {series.map((item) => {
          let drawing = false;
          const path = item.values
            .map((value, index) => {
              if (value === null) {
                drawing = false;
                return "";
              }
              const instruction = `${drawing ? "L" : "M"}${x(index)},${y(value)}`;
              drawing = true;
              return instruction;
            })
            .join(" ");
          return (
            <g
              key={item.name}
              data-series={item.name}
              data-valid-points={
                item.values.filter((value) => value !== null).length
              }
            >
              <path d={path} stroke={item.color} fill="none" strokeWidth="2" />
              {item.values.map((value, index) =>
                value === null ? null : (
                  <circle
                    key={index}
                    cx={x(index)}
                    cy={y(value)}
                    r="2.4"
                    fill={item.color}
                  >
                    <title>
                      {item.name} · tick {index + 1}: {format(value)} {unit}
                    </title>
                  </circle>
                ),
              )}
            </g>
          );
        })}
      </svg>
      <div className="startup-legend">
        {series.map((item) => (
          <span key={item.name}>
            <i style={{ background: item.color }} />
            {item.name}
          </span>
        ))}
        <span>
          Dashed: recorded policy {format(threshold)} {unit}
        </span>
      </div>
    </figure>
  );
}

export default function CarlaStartupInspector({ run }: { run: RunDetail }) {
  const artifact = run.artifacts.find(
    (item) =>
      item.kind === "startup_manifest" && item.name === "carla-startup.json",
  );
  const identity = JSON.stringify([
    run.id,
    run.scenario_id,
    run.scenario_revision,
    run.provenance,
    run.scenario_snapshot,
    run.artifacts,
  ]);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    identity: string;
    report?: StartupReport;
    url?: string;
    error?: string;
  }>({ identity: "" });
  useEffect(() => {
    if (!artifact) return;
    let active = true,
      url = "";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20_000);
    setState({ identity });
    void verifiedReport(run, artifact, controller.signal)
      .then(({ report, bytes }) => {
        if (!active) return;
        url = URL.createObjectURL(
          new Blob([bytes], { type: "application/json" }),
        );
        setState({ identity, report, url });
      })
      .catch((error: unknown) => {
        if (active)
          setState({
            identity,
            error: controller.signal.aborted
              ? "Startup evidence request timed out."
              : error instanceof Error
                ? error.message
                : "Startup evidence unavailable.",
          });
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      active = false;
      clearTimeout(timeout);
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [identity, attempt]);
  if (!artifact) return null;
  const current = state.identity === identity ? state : null,
    report = current?.report;
  const actorSeries = (kind: "vertical" | "linear") =>
    (["ego", "lead"] as const).map((role) => ({
      name: role === "ego" ? "Ego" : "Lead",
      color: role === "ego" ? "#65d9bd" : "#f6b267",
      values: report?.trace.map((row) => sampleValue(row, role, kind)) ?? [],
    }));
  return (
    <section
      className="panel startup-inspector"
      data-testid="carla-startup-inspector"
      aria-label="CARLA startup diagnostics"
    >
      <PanelHeading
        title="CARLA startup diagnostics"
        subtitle="Rest-state initialization · outside the measured driving interval"
      />
      <p className="startup-scope">
        Settlement is not a measured-driving pass. These snapshots precede the
        imposed initial target velocity; post-imposition wheel or speed
        convergence is not established.
      </p>
      {!report ? (
        current?.error ? (
          <div className="startup-error">
            <p role="alert">{current.error}</p>
            <button
              className="button secondary"
              onClick={() => setAttempt((value) => value + 1)}
            >
              <RotateCcw size={14} />
              Retry startup evidence
            </button>
          </div>
        ) : (
          <p role="status">
            Verifying startup artifact identity, size and SHA-256…
          </p>
        )
      ) : (
        <div data-testid="carla-startup-verified">
          <div className="startup-summary">
            <strong
              className={
                report.status === "passed"
                  ? "startup-settled"
                  : "startup-unsettled"
              }
            >
              {report.status === "passed"
                ? "Rest criteria met"
                : "Rest criteria not established"}
            </strong>
            <span>
              {report.completed_ticks} / {report.policy.max_ticks} startup ticks
            </span>
            <span>
              Stable streak {report.stable_streak} /{" "}
              {report.policy.consecutive_stable_samples}
            </span>
            <span>
              {format(report.elapsed_simulated_seconds)} s simulation ·{" "}
              {format(report.elapsed_wall_seconds)} s wall
            </span>
          </div>
          <p className="startup-reason">
            <code>{report.reason_code}</code> · {report.reason}
          </p>
          <div className="startup-context">
            <span>Server {report.context.server_version}</span>
            <span>{report.context.map_name}</span>
            <span>{report.policy.version}</span>
            <span>Native CARLA axes · z up</span>
          </div>
          {report.trace.length ? (
            <>
              <div className="startup-charts">
                <TraceChart
                  title="Vertical velocity"
                  unit="m/s"
                  series={actorSeries("vertical")}
                  threshold={report.policy.max_vertical_speed_mps}
                  signed
                />
                <TraceChart
                  title="Linear speed"
                  unit="m/s"
                  series={actorSeries("linear")}
                  threshold={report.policy.max_linear_speed_mps}
                />
                <TraceChart
                  title="Consecutive stable snapshots"
                  unit="samples"
                  series={[
                    {
                      name: "Both actors",
                      color: "#a6b8ff",
                      values: report.trace.map((row) =>
                        evaluated(row) ? row.stable_streak : null,
                      ),
                    },
                  ]}
                  threshold={report.policy.consecutive_stable_samples}
                />
              </div>
              <details className="startup-samples">
                <summary>
                  Inspect {report.trace.length} original sample rows
                </summary>
                <p>
                  Missing or invalid observations remain gaps. Reported
                  stability uses all recorded policy criteria, not only the
                  plotted speeds.
                </p>
                <div className="startup-table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Tick</th>
                        <th>World frame</th>
                        <th>Ego vz (m/s)</th>
                        <th>Lead vz (m/s)</th>
                        <th>Ego speed (m/s)</th>
                        <th>Lead speed (m/s)</th>
                        <th>Streak</th>
                        <th>Reported assessment / invalid fields</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.trace.map((row) => {
                        const observedIssues = [
                          ...row.invalid_fields,
                          ...Object.values(row.actors).flatMap(
                            (actor) => actor?.invalid_fields ?? [],
                          ),
                        ];
                        return (
                          <tr key={row.tick}>
                            <td>{row.tick}</td>
                            <td>{row.world_frame ?? "—"}</td>
                            <td>
                              {format(sampleValue(row, "ego", "vertical"))}
                            </td>
                            <td>
                              {format(sampleValue(row, "lead", "vertical"))}
                            </td>
                            <td>{format(sampleValue(row, "ego", "linear"))}</td>
                            <td>
                              {format(sampleValue(row, "lead", "linear"))}
                            </td>
                            <td>{evaluated(row) ? row.stable_streak : "—"}</td>
                            <td>
                              {evaluated(row)
                                ? row.stable
                                  ? "Stable"
                                  : "Not stable"
                                : "Unassessed"}
                              {observedIssues.map((issue, index) => (
                                <div key={index}>
                                  {issue.field}: {issue.observed}
                                </div>
                              ))}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </details>
            </>
          ) : (
            <p className="muted">
              No completed startup samples. The failure happened before an
              inspectable tick was captured.
            </p>
          )}
          <details className="startup-limitations">
            <summary>Recorded limitations</summary>
            <ul>
              {report.limitations.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </details>
          <div className="startup-download">
            <a
              className="button secondary"
              href={current?.url}
              download={artifact.name}
            >
              <ArrowDownToLine size={14} />
              Download verified startup JSON
            </a>
            <span>
              {formatBytes(artifact.size_bytes)} · run {run.id}
            </span>
            <code>{artifact.sha256}</code>
          </div>
        </div>
      )}
    </section>
  );
}
