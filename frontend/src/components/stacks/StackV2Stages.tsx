import type { StackOutputV2 } from "./StackV2Types";
import { validateStackOutputV2 } from "./StackV2Data";
import StackCoverageGrid from "./StackCoverageGrid";
import "./stack-v2.css";
const human = (value: string) => value.replaceAll("_", " ");
const fmt = (value: number | null | undefined, unit = "") =>
  value == null
    ? "Unavailable"
    : `${Number(value.toFixed(5))}${unit ? ` ${unit}` : ""}`;

function StoppingEnvelope({ output }: { output: StackOutputV2 }) {
  const decision = output.decision,
    plan = decision.stop_plan;
  const distances = [
    ["Qualified sample prefix", decision.qualified_prefix_m, "sample"],
    ["Positive surface", decision.positive_surface_distance_m, "positive"],
    ["Limiting horizon", decision.limiting_horizon_m, "horizon"],
    [
      "Available after reserve",
      decision.available_stopping_distance_m,
      "available",
    ],
    ["Current stopping need", decision.current_stopping_distance_m, "needed"],
    ["Planned stopping need", decision.planned_stopping_distance_m, "planned"],
  ] as const;
  const maximum = Math.max(
    1,
    ...distances.flatMap(([, value]) => (value === null ? [] : [value])),
  );
  return (
    <>
      <div
        className="stack-envelope-bars"
        data-testid="stack-stopping-envelope"
      >
        {distances.map(([name, value, kind]) => (
          <div key={name} data-envelope-field={kind}>
            <span>{name}</span>
            <div className={`stack-envelope-track ${kind}`}>
              {value !== null && (
                <i style={{ width: `${(value / maximum) * 100}%` }} />
              )}
            </div>
            <strong>{fmt(value, "m")}</strong>
          </div>
        ))}
      </div>
      <p className="stack-stage-semantics">
        Distances start at the declared ego front. Reserve{" "}
        {fmt(decision.reserve_m, "m")}. Stopping needs come from an assumed,
        uncalibrated model; these bars are not measured braking performance.
      </p>
      <p
        className="stack-stage-semantics"
        data-testid="stack-stopping-bound-model"
      >
        The stopping bound allows positive acceleration of{" "}
        {fmt(decision.allowed_positive_acceleration_mps2, "m/s²")} during
        latency, releases it under the jerk limit, then uses a zero-acceleration
        S-curve. This conservative bound is separate from the stored nominal
        plan phases below.
      </p>
      {plan ? (
        <section className="stack-stop-plan" data-testid="stack-stop-plan">
          <h4>Stored S-curve stop plan</h4>
          <p>
            Started {plan.started_at_ns} ns · initial speed{" "}
            {fmt(plan.initial_speed_mps, "m/s")} · elapsed{" "}
            {fmt(decision.plan_elapsed_s, "s")}
          </p>
          <svg
            viewBox="0 0 600 160"
            role="img"
            aria-label="Assumed stop-plan acceleration phases"
          >
            {(() => {
              const duration = Math.max(plan.duration_s, 0.001),
                x = (t: number) => 54 + (t / duration) * 510;
              const times = [
                0,
                plan.latency_s,
                plan.latency_s + plan.ramp_s,
                plan.latency_s + plan.ramp_s + plan.constant_deceleration_s,
                plan.duration_s,
              ];
              const ordinates = [28, 28, 108, 108, 28];
              return (
                <>
                  <line x1="54" x2="564" y1="28" y2="28" stroke="#d2d9d0" />
                  <polyline
                    points={times
                      .map(
                        (t, index) =>
                          `${x(t)},${plan.peak_deceleration_mps2 > 0 ? ordinates[index] : 28}`,
                      )
                      .join(" ")}
                    stroke="#496e86"
                    fill="none"
                    strokeWidth="2.5"
                  />
                  <text x="12" y="31">
                    0
                  </text>
                  <text x="0" y="112">
                    −{fmt(plan.peak_deceleration_mps2)}
                  </text>
                  <text x="54" y="145">
                    0 s
                  </text>
                  <text x="564" y="145" textAnchor="end">
                    {fmt(plan.duration_s)} s · assumed a (m/s²)
                  </text>
                  {decision.plan_elapsed_s !== null &&
                    decision.plan_elapsed_s <= plan.duration_s && (
                      <line
                        x1={x(decision.plan_elapsed_s)}
                        x2={x(decision.plan_elapsed_s)}
                        y1="15"
                        y2="125"
                        stroke="#b37a49"
                        strokeDasharray="3 3"
                      />
                    )}
                </>
              );
            })()}
          </svg>
          <div className="stack-plan-phases">
            <span>Latency {fmt(plan.latency_s, "s")}</span>
            <span>Ramp deceleration up {fmt(plan.ramp_s, "s")}</span>
            <span>
              Constant deceleration {fmt(plan.constant_deceleration_s, "s")}
            </span>
            <span>Ramp back to zero {fmt(plan.ramp_s, "s")}</span>
          </div>
          <dl className="stack-v2-facts">
            <div>
              <dt>Plan distance / remaining distance</dt>
              <dd>
                {fmt(plan.distance_m, "m")} /{" "}
                {fmt(decision.plan_remaining_distance_m, "m")}
              </dd>
            </div>
            <div>
              <dt>Declared jerk / deceleration limits</dt>
              <dd>
                {fmt(plan.jerk_limit_mps3, "m/s³")} /{" "}
                {fmt(plan.deceleration_limit_mps2, "m/s²")}
              </dd>
            </div>
          </dl>
          <p className="stack-stage-semantics">
            Phase shape uses the saved plan values. Initial acceleration is
            assumed zero, not a measured value. Actual control requests and
            later physics remain separate evidence.
          </p>
        </section>
      ) : (
        <p className="stack-stage-empty" data-testid="stack-no-stop-plan">
          No controlled stopping plan was retained for this tick.
        </p>
      )}
    </>
  );
}
function Stages({ output, live }: { output: StackOutputV2; live: boolean }) {
  const observation = output.observation,
    decision = output.decision;
  return (
    <div
      className="stack-tick-stages stack-v2-stages"
      data-testid="stack-v2-stages"
    >
      <section className="stack-tick-stage">
        <div className="stack-stage-heading">
          <span>01</span>
          <h3>Sample evidence · kernel 2</h3>
        </div>
        <strong
          className="stack-decision-label"
          data-testid="stack-observation-status"
        >
          {human(observation.status)} ·{" "}
          {observation.input_valid ? "valid input" : "invalid input"}
        </strong>
        <p>
          Input / forward / ground points: {observation.input_points} /{" "}
          {observation.forward_points} / {observation.ground_points}
        </p>
        {observation.positive_surface ? (
          <div
            className="stack-positive-evidence"
            data-testid="stack-positive-surface"
          >
            <strong>
              Positive surface retained ·{" "}
              {observation.positive_surface.observed_points} height-band points
            </strong>
            <span>
              Distance from bumper{" "}
              <b data-testid="stack-observed-distance">
                {fmt(observation.positive_surface.distance_from_bumper_m, "m")}
              </b>
            </span>
            <code data-testid="stack-nearest-point">
              {observation.positive_surface.nearest_point_xyz_m
                .map((value) => fmt(value))
                .join(", ")}{" "}
              m
            </code>
            <span>
              Nearest actual XYZ · no object identity or velocity inferred
            </span>
          </div>
        ) : (
          <p className="stack-stage-empty">
            No valid positive surface retained. Absence is not evidence of free
            space.
          </p>
        )}
        <StackCoverageGrid observation={observation} />
        <ul>
          {observation.reasons.map((reason) => (
            <li key={reason}>{human(reason)}</li>
          ))}
        </ul>
      </section>
      <section className="stack-tick-stage">
        <div className="stack-stage-heading">
          <span>02</span>
          <h3>Stopping envelope and plan</h3>
        </div>
        <strong
          className={`stack-decision-label ${decision.mode}`}
          data-testid="stack-policy-mode"
        >
          {human(decision.mode)}
        </strong>
        <dl className="stack-v2-facts">
          <div>
            <dt>Requested speed</dt>
            <dd data-testid="stack-target-speed">
              {fmt(decision.target_speed_mps, "m/s")}
            </dd>
          </div>
          <div>
            <dt>Requested acceleration / jerk</dt>
            <dd>
              {fmt(decision.target_acceleration_mps2, "m/s²")} /{" "}
              {fmt(decision.target_jerk_mps3, "m/s³")}
            </dd>
          </div>
          <div>
            <dt>Force brake</dt>
            <dd>{decision.force_brake ? "Requested" : "No"}</dd>
          </div>
          <div>
            <dt>Observed speed-derived acceleration</dt>
            <dd>{fmt(decision.observed_speed_acceleration_mps2, "m/s²")}</dd>
          </div>
          <div>
            <dt>Applied dt / speed tracking error</dt>
            <dd>
              {fmt(decision.applied_dt_s, "s")} /{" "}
              {fmt(decision.speed_tracking_error_mps, "m/s")}
            </dd>
          </div>
        </dl>
        <StoppingEnvelope output={output} />
        <ul>
          {decision.reasons.map((reason) => (
            <li key={reason}>{human(reason)}</li>
          ))}
        </ul>
        <div className="stack-state-evidence" data-testid="stack-plan-state">
          <h4>Saved state transition</h4>
          <strong>
            {human(output.state_before.mode)} → {human(output.state_after.mode)}
          </strong>
          <p>
            Latch {output.state_after.latched ? "retained" : "not set"} ·{" "}
            {output.state_after.latch_reason ?? "no latch reason"}
          </p>
          <p>
            Reset: new session only. Selecting a tick inspects saved state; it
            does not restart or advance a plan.
          </p>
          <details>
            <summary>Exact state before / after and model diagnostics</summary>
            <pre>
              {JSON.stringify(
                {
                  before: output.state_before,
                  after: output.state_after,
                  decision,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </div>
      </section>
      <section className="stack-tick-stage">
        <div className="stack-stage-heading">
          <span>03</span>
          <h3>Control request</h3>
        </div>
        <div className="stack-control-values">
          {(["throttle", "brake", "steer"] as const).map((key) => (
            <div key={key}>
              <span>{key}</span>
              <strong data-testid={`stack-control-${key}`}>
                {fmt(output.control[key])}
              </strong>
              <div className={`stack-control-track ${key}`}>
                <i
                  style={{ width: `${Math.abs(output.control[key]) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </div>
        <p className="stack-stage-semantics">
          Normalized actuator requests. Steering is positive left.
          Brake-to-deceleration response is an explicit assumption, not a
          calibrated vehicle model.
        </p>
        <div
          className="stack-command-application"
          data-testid="stack-control-application"
        >
          <strong>
            {live
              ? "KERNEL REQUEST · SEE DELIVERY BELOW"
              : "NOT APPLIED · OPEN-LOOP"}
          </strong>
          <span>
            {live
              ? "Delivery and the next physics frame are recorded separately."
              : "The stored request does not change recorded motion or later observations."}
          </span>
        </div>
        <ul>
          {output.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
export default function StackV2Stages({
  output,
  live = false,
}: {
  output: StackOutputV2;
  live?: boolean;
}) {
  try {
    validateStackOutputV2(output);
  } catch (error) {
    return (
      <p className="inline-error" role="alert">
        Kernel 2 evidence rejected: {(error as Error).message}
      </p>
    );
  }
  return <Stages output={output} live={live} />;
}
