import { ScanLine, Route, SlidersHorizontal } from "lucide-react";
import type { StackReplayTick } from "./StackReplayTypes";
import StackV2Stages from "./StackV2Stages";
const human = (text: string) => text.replaceAll("_", " ");
const number = (value: number | null | undefined, unit = "") =>
  value == null
    ? "Unavailable"
    : `${Number(value.toFixed(4))}${unit ? ` ${unit}` : ""}`;
export default function StackTickStages({
  tick,
  live = false,
}: {
  tick: Pick<StackReplayTick, "output">;
  live?: boolean;
}) {
  const output = tick.output;
  if (output?.kernel_version === "stack-kernel/2")
    return <StackV2Stages output={output} live={live} />;
  return (
    <div className="stack-tick-stages" data-testid="stack-tick-stages">
      <section className="stack-tick-stage">
        <div className="stack-stage-heading">
          <span>01</span>
          <h3>Observation</h3>
          <ScanLine size={16} />
        </div>
        {output ? (
          <>
            <strong
              className={`stack-decision-label ${output.observation.status}`}
              data-testid="stack-observation-status"
            >
              {human(output.observation.status)}
            </strong>
            <dl>
              <div>
                <dt>Observed surface distance</dt>
                <dd data-testid="stack-observed-distance">
                  {number(output.observation.distance_m, "m")}
                </dd>
              </div>
              <div>
                <dt>Input / forward / obstacle points</dt>
                <dd>
                  {output.observation.input_points} /{" "}
                  {output.observation.forward_points} /{" "}
                  {output.observation.obstacle_points}
                </dd>
              </div>
              <div>
                <dt>Ground points / observed bins</dt>
                <dd>
                  {output.observation.ground_points} /{" "}
                  {output.observation.ground_bins_observed}
                </dd>
              </div>
              <div>
                <dt>Observed ground extent beyond ego front</dt>
                <dd data-testid="stack-supported-distance">
                  {number(output.observation.supported_distance_m, "m")}
                </dd>
              </div>
              <div>
                <dt>Sample coverage qualified</dt>
                <dd>
                  {output.observation.coverage_qualified
                    ? "Yes · sample coverage only"
                    : "No"}
                </dd>
              </div>
            </dl>
            {output.observation.nearest_point_xyz_m && (
              <p className="stack-nearest">
                Nearest observed point ·{" "}
                <code data-testid="stack-nearest-point">
                  {output.observation.nearest_point_xyz_m
                    .map((value) => Number(value.toFixed(5)))
                    .join(", ")}{" "}
                  m
                </code>
              </p>
            )}
            <p className="stack-stage-semantics">
              Observed surface geometry, not free space, object identity,
              velocity, or TTC.
            </p>
            {output.observation.reasons.length > 0 && (
              <ul>
                {output.observation.reasons.map((reason) => (
                  <li key={reason}>{human(reason)}</li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className="stack-stage-empty">
            No observation output published for this tick.
          </p>
        )}
      </section>
      <section className="stack-tick-stage">
        <div className="stack-stage-heading">
          <span>02</span>
          <h3>Policy</h3>
          <Route size={16} />
        </div>
        {output ? (
          <>
            <strong
              className={`stack-decision-label ${output.decision.mode}`}
              data-testid="stack-policy-mode"
            >
              {human(output.decision.mode)}
            </strong>
            <dl>
              <div>
                <dt>Target speed</dt>
                <dd data-testid="stack-target-speed">
                  {number(output.decision.target_speed_mps, "m/s")}
                </dd>
              </div>
              <div>
                <dt>Declared stopping/headway distance</dt>
                <dd>{number(output.decision.stopping_distance_m, "m")}</dd>
              </div>
              <div>
                <dt>Force brake</dt>
                <dd>{output.decision.force_brake ? "Requested" : "No"}</dd>
              </div>
            </dl>
            <ul>
              {output.decision.reasons.map((reason) => (
                <li key={reason}>{human(reason)}</li>
              ))}
            </ul>
            <p className="stack-stage-semantics">
              Stationary-surface and authored braking assumptions; this is not
              measured stopping performance.
            </p>
          </>
        ) : (
          <p className="stack-stage-empty">
            No policy output published for this tick.
          </p>
        )}
      </section>
      <section className="stack-tick-stage">
        <div className="stack-stage-heading">
          <span>03</span>
          <h3>Control request</h3>
          <SlidersHorizontal size={16} />
        </div>
        {output ? (
          <>
            <div className="stack-control-values">
              {(["throttle", "brake", "steer"] as const).map((key) => (
                <div key={key}>
                  <span>{key}</span>
                  <strong data-testid={`stack-control-${key}`}>
                    {number(output.control[key])}
                  </strong>
                  <div className={`stack-control-track ${key}`}>
                    <i
                      style={{
                        width: `${Math.abs(output.control[key]) * 100}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
            <p className="stack-stage-semantics">
              Normalized commands. Steering is positive left.
            </p>
          </>
        ) : (
          <p className="stack-stage-empty">
            No control output published for this tick.
          </p>
        )}
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
              ? "This output alone does not establish command delivery or a next physics observation."
              : "Recorded ego motion and later sensor samples are unchanged by these commands."}
          </span>
        </div>
      </section>
    </div>
  );
}
