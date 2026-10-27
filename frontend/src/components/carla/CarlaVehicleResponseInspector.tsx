import { useEffect, useState } from "react";
import { ArrowDownToLine, ChevronLeft, ChevronRight } from "lucide-react";
import type { RunDetail } from "../../types";
import { formatBytes } from "../../api";
import type { CarlaLiveTrace, CarlaVehicleControl } from "./CarlaTypes";
import type { DiagnosticValue, VehicleResponse } from "./VehicleResponseTypes";
import {
  headingProjection,
  loadVehicleResponse,
  normSpeed,
} from "./VehicleResponseData";
import VehicleResponseChart, {
  responseNumber as fmt,
} from "./VehicleResponseChart";
import "./vehicle-response.css";

const human = (value: string) => value.replaceAll("_", " ");
const diagnosticNumber = (value: DiagnosticValue | undefined) =>
  value?.status === "available" && typeof value.value === "number"
    ? value.value
    : null;
function Diagnostic({ value }: { value: DiagnosticValue | undefined }) {
  if (!value) return <>Unrecorded</>;
  return value.status === "available" ? (
    <>
      {typeof value.value === "boolean"
        ? String(value.value)
        : fmt(value.value)}
    </>
  ) : (
    <span title={value.reason ?? undefined}>
      {human(value.status)} · {value.reason}
    </span>
  );
}
function Control({ value }: { value: CarlaVehicleControl | null | undefined }) {
  if (!value) return <p>Unavailable · no retained control value</p>;
  return (
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
  );
}

function ResponseView({
  report,
  trace,
}: {
  report: VehicleResponse;
  trace: CarlaLiveTrace;
}) {
  const [selected, setSelected] = useState(0),
    [role, setRole] = useState<"ego" | "lead">("ego"),
    [wheelField, setWheelField] = useState("omega");
  const rows = report.observations,
    row = rows[Math.min(selected, rows.length - 1)],
    actor = row?.actors[role];
  const times = rows.map((item) => item.source_time_s),
    frames = rows.map((item) => item.world_frame);
  const actors = rows.map((item) => item.actors[role]);
  const heading = actors.map((item) =>
    headingProjection(item.velocity_mps, item.rotation_deg.yaw),
  );
  const norms = actors.map((item) => normSpeed(item.velocity_mps));
  const derivative = (values: number[]) =>
    values.map((value, index) =>
      index === 0
        ? null
        : (value - values[index - 1]) / (times[index] - times[index - 1]),
    );
  const outgoing = trace.ticks.find(
    (item) => item.source_world_frame === row?.world_frame,
  );
  const next =
    outgoing?.next_world_frame === null
      ? undefined
      : rows.find((item) => item.world_frame === outgoing?.next_world_frame);
  const egoControl = next?.actors.ego.reported_control;
  const final = selected === report.expected_observations - 1;
  const chart = { times, frames, selected, onSelect: setSelected };
  return (
    <div data-testid="carla-response-verified">
      <div className="vehicle-response-summary">
        <strong>
          {report.status === "completed"
            ? "Complete response sequence"
            : "Partial response sequence"}
        </strong>
        <span>
          {rows.length} / {report.expected_observations} measured observations
        </span>
        <span>
          {report.worker_adapter_version} · server {report.server_version}
        </span>
        <span data-testid="carla-response-friction">
          Configured tire scale · ego{" "}
          {report.physics_configuration.ego?.friction_status ?? "unrecorded"} /
          lead{" "}
          {report.physics_configuration.lead?.friction_status ?? "unrecorded"}
        </span>
      </div>
      <div
        className="vehicle-response-boundaries"
        data-testid="carla-response-boundaries"
      >
        <div>
          <h3>1 · Rest boundary</h3>
          <p>
            {report.after_rest ? (
              <>
                Frame {report.after_rest.world_frame} ·{" "}
                {fmt(report.after_rest.source_time_s)} s
              </>
            ) : (
              "Not reached or not retained"
            )}
          </p>
          <p>Verified startup sample, before initial target velocity.</p>
          {report.after_rest && (
            <p>
              {role} |v|{" "}
              {fmt(normSpeed(report.after_rest.actors[role].velocity_mps))} m/s
            </p>
          )}
        </div>
        <div>
          <h3>2 · Initial velocity injection</h3>
          {report.initial_velocity_requests.length ? (
            report.initial_velocity_requests.map((request) => (
              <p key={request.role}>
                {request.role}: {fmt(request.requested_speed_mps)} m/s after
                frame {request.after_world_frame} · ACK unknown
              </p>
            ))
          ) : (
            <p>No request retained.</p>
          )}
          <p>Direct velocity request; wheel convergence is not established.</p>
        </div>
        <div>
          <h3>3 · Measured observations</h3>
          <p>
            {rows.length ? (
              <>
                Frames {frames[0]}–{frames.at(-1)} · {fmt(times[0])}–
                {fmt(times.at(-1))} s
              </>
            ) : (
              "No measured observation retained."
            )}
          </p>
          <p>
            N commands → N+1 observations. The final observation has no new
            command.
          </p>
        </div>
      </div>
      {report.error && (
        <p className="inline-error" role="alert">
          {report.error}
        </p>
      )}
      {row && actor ? (
        <>
          <div className="vehicle-response-toolbar">
            <label>
              Observed actor
              <select
                aria-label="Vehicle response actor"
                value={role}
                onChange={(event) =>
                  setRole(event.target.value as "ego" | "lead")
                }
              >
                <option value="ego">Ego</option>
                <option value="lead">Lead · observer only</option>
              </select>
            </label>
            <button
              className="icon-button"
              aria-label="Previous response observation"
              disabled={selected === 0}
              onClick={() => setSelected((value) => value - 1)}
            >
              <ChevronLeft size={17} />
            </button>
            <label className="response-frame-control">
              Observation {selected + 1}/{rows.length}
              <input
                type="range"
                aria-label="Vehicle response observation"
                min={0}
                max={rows.length - 1}
                step={1}
                value={selected}
                onChange={(event) => setSelected(Number(event.target.value))}
              />
            </label>
            <button
              className="icon-button"
              aria-label="Next response observation"
              disabled={selected >= rows.length - 1}
              onClick={() => setSelected((value) => value + 1)}
            >
              <ChevronRight size={17} />
            </button>
          </div>
          <div className="vehicle-response-charts">
            <VehicleResponseChart
              {...chart}
              title="Velocity components"
              unit="m/s"
              series={[
                {
                  name: "Signed horizontal-heading speed",
                  color: "#29785d",
                  values: heading,
                },
                {
                  name: "World vertical velocity",
                  color: "#aa6f42",
                  values: actors.map((item) => item.velocity_mps.z),
                },
                {
                  name: "Existing 3D speed norm",
                  color: "#6576a4",
                  values: norms,
                  dashed: true,
                },
              ]}
            />
            <VehicleResponseChart
              {...chart}
              title="Acceleration sources"
              unit="m/s²"
              series={[
                {
                  name: "Snapshot acceleration projected on heading",
                  color: "#29785d",
                  values: actors.map((item) =>
                    headingProjection(
                      item.acceleration_mps2,
                      item.rotation_deg.yaw,
                    ),
                  ),
                },
                {
                  name: "Finite difference of signed heading speed",
                  color: "#aa6f42",
                  values: derivative(heading),
                },
                {
                  name: "Finite difference of 3D speed norm",
                  color: "#6576a4",
                  values: derivative(norms),
                  dashed: true,
                },
              ]}
            />
            <VehicleResponseChart
              {...chart}
              title="Ego brake request and next-frame readback"
              unit="normalized"
              series={[
                {
                  name: "Brake requested at F",
                  color: "#347d97",
                  values: rows.map(
                    (item) =>
                      trace.ticks.find(
                        (tick) => tick.source_world_frame === item.world_frame,
                      )?.requested_carla_control?.brake ?? null,
                  ),
                },
                {
                  name: "Brake readback at F+1",
                  color: "#a06f8e",
                  values: rows.map((item) =>
                    trace.ticks.some(
                      (tick) => tick.next_world_frame === item.world_frame,
                    )
                      ? (item.actors.ego.reported_control.data?.brake ?? null)
                      : null,
                  ),
                  dashed: true,
                },
              ]}
            />
            <VehicleResponseChart
              {...chart}
              title="Ego throttle request and next-frame readback"
              unit="normalized"
              series={[
                {
                  name: "Throttle requested at F",
                  color: "#347d97",
                  values: rows.map(
                    (item) =>
                      trace.ticks.find(
                        (tick) => tick.source_world_frame === item.world_frame,
                      )?.requested_carla_control?.throttle ?? null,
                  ),
                },
                {
                  name: "Throttle readback at F+1",
                  color: "#a06f8e",
                  values: rows.map((item) =>
                    trace.ticks.some(
                      (tick) => tick.next_world_frame === item.world_frame,
                    )
                      ? (item.actors.ego.reported_control.data?.throttle ??
                        null)
                      : null,
                  ),
                  dashed: true,
                },
              ]}
            />
          </div>
          <p>
            Dots are original frame samples. Signed heading uses vx·cos(yaw) +
            vy·sin(yaw); 3D norm includes vz. Differences use the recorded time
            interval and start with an undefined value. Heading changes can
            affect this derivative. Existing run metrics and acceptance
            thresholds are unchanged; no samples are trimmed or smoothed.
          </p>
          <section
            className="vehicle-response-selected"
            data-testid="carla-response-selected"
          >
            <strong>
              Frame {row.world_frame} · {fmt(row.source_time_s)} s · {role}{" "}
              actor {actor.actor_id}
            </strong>
            <div className="vehicle-response-readouts">
              <div className="vehicle-response-readout">
                <h3>Coherent native snapshot</h3>
                <dl>
                  <div>
                    <dt>signed heading speed</dt>
                    <dd>{fmt(heading[selected])} m/s</dd>
                  </div>
                  <div>
                    <dt>world vz</dt>
                    <dd>{fmt(actor.velocity_mps.z)} m/s</dd>
                  </div>
                  <div>
                    <dt>3D speed norm</dt>
                    <dd>{fmt(norms[selected])} m/s</dd>
                  </div>
                  <div>
                    <dt>snapshot ax / ay / az</dt>
                    <dd>
                      {Object.values(actor.acceleration_mps2)
                        .map((value) => fmt(value))
                        .join(" / ")}{" "}
                      m/s²
                    </dd>
                  </div>
                  <div>
                    <dt>pitch / yaw / roll</dt>
                    <dd>
                      {[
                        actor.rotation_deg.pitch,
                        actor.rotation_deg.yaw,
                        actor.rotation_deg.roll,
                      ]
                        .map((value) => fmt(value))
                        .join(" / ")}
                      °
                    </dd>
                  </div>
                </dl>
              </div>
              <div
                className="vehicle-response-readout"
                data-testid="carla-response-command"
              >
                <h3>Ego outgoing command · F</h3>
                {outgoing ? (
                  <>
                    <p>
                      Frame {outgoing.source_world_frame} →{" "}
                      {outgoing.next_world_frame ??
                        "next observation unavailable"}{" "}
                      · {human(outgoing.application_status)}
                    </p>
                    <Control value={outgoing.requested_carla_control} />
                    <p>
                      RPC ACK:{" "}
                      {outgoing.control_acknowledged
                        ? "recorded"
                        : "not established"}
                    </p>
                  </>
                ) : (
                  <p>
                    {final
                      ? "Final observation · no outgoing command scheduled."
                      : "No outgoing command evidence retained at this frame."}
                  </p>
                )}
              </div>
              <div className="vehicle-response-readout">
                <h3>Ego next-frame readback · F+1</h3>
                {outgoing ? (
                  <>
                    <p>
                      {next ? (
                        <>
                          Frame {next.world_frame} · {fmt(next.source_time_s)} s
                        </>
                      ) : (
                        "Next raw snapshot unavailable"
                      )}
                    </p>
                    <Control value={egoControl?.data} />
                    <p>
                      {egoControl ? (
                        <>
                          RPC status {egoControl.status} · bracket{" "}
                          {egoControl.before_world_frame ?? "—"} /{" "}
                          {egoControl.after_world_frame ?? "—"}
                          {egoControl.error && ` · ${egoControl.error}`}
                        </>
                      ) : (
                        "No raw control read retained."
                      )}
                    </p>
                  </>
                ) : (
                  <p>No subsequent command interval.</p>
                )}
              </div>
            </div>
          </section>
          <details>
            <summary>Wheel and drivetrain API observations · {role}</summary>
            <p>
              These optional API fields retain their reported values and
              individual availability. Installed-build units are unverified. API
              wheel friction and the configured tire-friction scale are
              different quantities. A missing, null or nonfinite field is not
              zero.
            </p>
            <p>
              Selected-frame telemetry: {actor.telemetry.status} · RPC bracket{" "}
              {actor.telemetry.before_world_frame ?? "—"} /{" "}
              {actor.telemetry.after_world_frame ?? "—"}
              {actor.telemetry.error && ` · ${actor.telemetry.error}`}. Reported
              control gear: {actor.reported_control.data?.gear ?? "unavailable"}
              .
            </p>
            {actor.telemetry.status === "available" && (
              <div className="vehicle-response-table">
                <table>
                  <thead>
                    <tr>
                      <th>API field</th>
                      <th>Reported value / status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(actor.telemetry.scalars).map(
                      ([field, value]) => (
                        <tr key={field}>
                          <td>{field}</td>
                          <td>
                            <Diagnostic value={value} />
                          </td>
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
                <table>
                  <thead>
                    <tr>
                      <th>Wheel index</th>
                      {["omega", "long_slip", "lat_slip", "tire_friction"].map(
                        (field) => (
                          <th key={field}>{field}</th>
                        ),
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {actor.telemetry.wheels.map((wheel) => (
                      <tr key={wheel.index}>
                        <td>{wheel.index}</td>
                        {[
                          "omega",
                          "long_slip",
                          "lat_slip",
                          "tire_friction",
                        ].map((field) => (
                          <td key={field}>
                            <Diagnostic value={wheel.fields[field]} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="vehicle-response-toolbar">
              <label>
                Wheel signal
                <select
                  aria-label="Vehicle response wheel signal"
                  value={wheelField}
                  onChange={(event) => setWheelField(event.target.value)}
                >
                  {["omega", "long_slip", "lat_slip", "tire_friction"].map(
                    (field) => (
                      <option key={field} value={field}>
                        {field}
                      </option>
                    ),
                  )}
                </select>
              </label>
            </div>
            <div className="vehicle-response-charts">
              <VehicleResponseChart
                {...chart}
                title={`Wheel ${wheelField}`}
                unit="raw API · units unverified"
                series={[0, 1, 2, 3].map((index) => ({
                  name: `Wheel ${index}`,
                  color: ["#29785d", "#aa6f42", "#6576a4", "#9f6f8d"][index],
                  values: actors.map((item) =>
                    diagnosticNumber(
                      item.telemetry.wheels[index]?.fields[wheelField],
                    ),
                  ),
                }))}
              />
              <VehicleResponseChart
                {...chart}
                title="Engine RPM API field"
                unit="API engine_rpm · build units unverified"
                series={[
                  {
                    name: "engine_rpm",
                    color: "#6576a4",
                    values: actors.map((item) =>
                      diagnosticNumber(item.telemetry.scalars.engine_rpm),
                    ),
                  },
                ]}
              />
            </div>
          </details>
          <details>
            <summary>
              Inspect all {rows.length} original measured observations
            </summary>
            <div className="vehicle-response-table">
              <table>
                <thead>
                  <tr>
                    <th>Frame</th>
                    <th>Simulation s</th>
                    <th>Signed heading m/s</th>
                    <th>World vz m/s</th>
                    <th>3D norm m/s</th>
                    <th>Control / telemetry availability</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item, index) => (
                    <tr key={item.world_frame}>
                      <td>
                        <button
                          className="button text"
                          onClick={() => setSelected(index)}
                        >
                          {item.world_frame}
                        </button>
                      </td>
                      <td>{fmt(item.source_time_s)}</td>
                      <td>{fmt(heading[index])}</td>
                      <td>{fmt(item.actors[role].velocity_mps.z)}</td>
                      <td>{fmt(norms[index])}</td>
                      <td>
                        {item.actors[role].reported_control.status} /{" "}
                        {item.actors[role].telemetry.status}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <details>
            <summary>
              Selected original snapshot, control and telemetry JSON
            </summary>
            <pre>{JSON.stringify(row, null, 2)}</pre>
          </details>
        </>
      ) : (
        <p>
          No measured raw snapshot was retained before this failure. The
          scheduled denominator remains {report.expected_observations}.
        </p>
      )}
      <details>
        <summary>Post-rest physics configuration and friction readback</summary>
        <p>
          Requested tire-friction scale is checked against float32 API readback
          before velocity injection. These are simulator parameters, not a
          measured road-friction coefficient or calibrated vehicle model.
        </p>
        {(["ego", "lead"] as const).map((name) => {
          const config = report.physics_configuration[name];
          return (
            <div key={name}>
              <h3>{name}</h3>
              {config ? (
                <>
                  <p>
                    Requested {config.requested_tire_friction} · float32{" "}
                    {config.requested_tire_friction_float32} · friction{" "}
                    {config.friction_status} · RPC{" "}
                    {config.before_world_frame ?? "—"} /{" "}
                    {config.after_world_frame ?? "—"}
                  </p>
                  <pre>{JSON.stringify(config, null, 2)}</pre>
                </>
              ) : (
                <p>Unrecorded</p>
              )}
            </div>
          );
        })}
      </details>
      <details>
        <summary>Recorded limitations and original initial requests</summary>
        <ul>
          {report.limitations.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
        <pre>
          {JSON.stringify(
            {
              after_rest: report.after_rest,
              initial_velocity_requests: report.initial_velocity_requests,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}

export default function CarlaVehicleResponseInspector({
  run,
}: {
  run: RunDetail;
}) {
  const artifact = run.artifacts.find(
    (item) =>
      item.kind === "vehicle_response" ||
      item.name === "carla-vehicle-response.json",
  );
  const key = JSON.stringify([
    run.id,
    run.provenance,
    run.execution_context,
    run.scenario_snapshot,
    run.artifacts,
  ]);
  const [attempt, setAttempt] = useState(0),
    [state, setState] = useState<{
      key: string;
      result?: Awaited<ReturnType<typeof loadVehicleResponse>>;
      url?: string;
      error?: string;
    }>({ key: "" });
  useEffect(() => {
    if (!artifact) return;
    let active = true,
      url = "";
    const controller = new AbortController(),
      timer = setTimeout(() => controller.abort(), 20000);
    setState({ key });
    void loadVehicleResponse(run, controller.signal)
      .then((result) => {
        if (!active) return;
        url = URL.createObjectURL(
          new Blob([result.bytes], { type: "application/json" }),
        );
        setState({ key, result, url });
      })
      .catch((error: unknown) => {
        if (active)
          setState({
            key,
            error: controller.signal.aborted
              ? "Vehicle response verification timed out."
              : error instanceof Error
                ? error.message
                : "Vehicle response unavailable.",
          });
      })
      .finally(() => clearTimeout(timer));
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [key, attempt]);
  const current = state.key === key ? state : undefined;
  return (
    <section
      className="panel vehicle-response-inspector"
      data-testid="carla-response-inspector"
      aria-label="CARLA vehicle response diagnostics"
    >
      <div className="vehicle-response-heading">
        <div>
          <span className="eyebrow">RAW SNAPSHOT → COMMAND → NEXT FRAME</span>
          <h2>Vehicle response diagnostics</h2>
        </div>
        <span className="mono">Native CARLA world axes</span>
      </div>
      {!artifact ? (
        <p
          className="response-unrecorded"
          data-testid="carla-response-unrecorded"
        >
          Unrecorded for this run. Optional vehicle-response evidence is
          supported for live stack executions using adapters 0.4.1, 0.5.0, 0.6.0
          0.7.0 and 0.8.0; older results remain unchanged.
        </p>
      ) : (
        <>
          <p>
            Simulator observations, separate from perception and acceptance
            metrics. Native world y points right; signed heading and vertical
            motion remain visible. These diagnostics do not establish calibrated
            plant dynamics, physical wheel units or the cause of a braking
            transient.
          </p>
          {current?.error ? (
            <div>
              <p role="alert" className="inline-error">
                Vehicle response evidence rejected: {current.error}
              </p>
              <button
                className="button secondary"
                onClick={() => setAttempt((value) => value + 1)}
              >
                Retry vehicle response evidence
              </button>
            </div>
          ) : current?.result ? (
            <>
              <ResponseView
                key={key}
                report={current.result.report}
                trace={current.result.trace}
              />
              <div className="vehicle-response-download">
                <a
                  className="button secondary"
                  href={current.url}
                  download={artifact.name}
                >
                  <ArrowDownToLine size={14} />
                  Download verified vehicle response JSON
                </a>
                <span>
                  {formatBytes(artifact.size_bytes)} · run {run.id}
                </span>
                <code>{artifact.sha256}</code>
              </div>
            </>
          ) : (
            <p role="status">
              Verifying response, startup, live trace and physics hashes against
              this execution…
            </p>
          )}
        </>
      )}
    </section>
  );
}
