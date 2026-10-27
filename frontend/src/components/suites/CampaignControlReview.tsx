import type { Adapter } from "../../types";
import type { SimulationControlSelection } from "../carla/CarlaTypes";
import { isConfiguredControl } from "../carla/CarlaTypes";
import type { SuiteCaseSnapshot, SuiteDetail } from "./SuiteTypes";

/** Local, pinned-input checks only. The API preflights every case atomically. */
export function campaignCaseIssues(
  entry: SuiteCaseSnapshot,
  live: boolean,
  carla: boolean,
): string[] {
  const scenario = entry.scenario_snapshot;
  const issues: string[] = [];
  if (carla && !["lane_follow", "emergency_brake"].includes(scenario.category))
    issues.push(
      `CARLA does not support ${scenario.category} in this execution path.`,
    );
  if (
    carla &&
    Math.abs(
      scenario.duration_s / 0.05 - Math.round(scenario.duration_s / 0.05),
    ) > 1e-6
  )
    issues.push("Duration must contain complete 0.05-second intervals.");
  if (live && (scenario.duration_s < 2 || scenario.duration_s > 4))
    issues.push("Live LiDAR execution requires a duration of 2–4 seconds.");
  if (carla && !live && scenario.duration_s > 20)
    issues.push("The default CARLA controller supports at most 20 seconds.");
  return issues;
}

export default function CampaignControlReview({
  suite,
  adapter,
  adapterId,
  live,
  control,
  controlValid,
}: {
  suite: SuiteDetail;
  adapter?: Adapter;
  adapterId: string;
  live: boolean;
  control: SimulationControlSelection;
  controlValid: boolean;
}) {
  const issues = suite.cases.filter(
    (entry) => campaignCaseIssues(entry, live, adapterId === "carla").length,
  );
  return (
    <section
      className="suite-campaign-selection"
      aria-label="Campaign execution review"
      data-testid="campaign-execution-review"
    >
      <h3>Review all pinned cases</h3>
      <p>
        <strong>
          {adapter?.name ?? adapterId} ·{" "}
          {adapter?.version ?? "version unavailable"}
        </strong>
        {" · "}
        {live
          ? "Saved shared stack · live LiDAR"
          : adapterId === "carla"
            ? "Surrounding actor ground-truth controller"
            : "Adapter default controller"}
      </p>
      {live && (
        <>
          <dl className="suite-plan-fields">
            <div>
              <dt>Stack</dt>
              <dd>
                <code>{control.stack_id || "Not selected"}</code> · r
                {control.stack_revision || "?"}
              </dd>
            </div>
            <div>
              <dt>Rig / LiDAR key</dt>
              <dd>
                <code>{control.rig_id || "Not selected"}</code> · r
                {control.rig_revision || "?"} ·{" "}
                {control.lidar_sensor_key || "Not selected"}
              </dd>
            </div>
            <div>
              <dt>
                {isConfiguredControl(control)
                  ? "Sensor configuration"
                  : "Sensor profile"}
              </dt>
              <dd>
                {isConfiguredControl(control) ? (
                  <>
                    <code>
                      {control.sensor_configuration_id || "Not selected"}
                    </code>{" "}
                    · r{control.sensor_configuration_revision || "?"}
                  </>
                ) : (
                  control.sensor_profile_id
                )}
              </dd>
            </div>
          </dl>
          <p>
            {controlValid
              ? "Exact selected revisions loaded. Each case receives its own effective seed and context hash."
              : "Live selection is incomplete or could not be verified. Submission is blocked."}
          </p>
          <p>
            The sampling preview uses the first case's seed only. Every
            effective seed is listed below; a shared selection does not make the
            per-case context hashes identical. Sensor RNG application is
            unverified.
          </p>
        </>
      )}
      <p>
        {suite.cases.length} / {suite.case_count} pinned cases · {issues.length}{" "}
        with local execution constraints. This review does not query mutable
        scenario heads or certify engine readiness. The API checks each case,
        current capabilities, recipe budget and connection before dispatch.
      </p>
      {issues.length > 0 && (
        <p className="inline-error" role="alert">
          Unsupported pinned cases block the entire submission. Publish a
          deliberate suite revision or choose another controller/engine; cases
          are not silently skipped.
        </p>
      )}
      <div className="suite-campaign-inputs">
        {suite.cases.map((entry) => {
          const problems = campaignCaseIssues(
            entry,
            live,
            adapterId === "carla",
          );
          const scenario = entry.scenario_snapshot;
          return (
            <details
              key={entry.key}
              open={problems.length > 0}
              data-testid={`campaign-input-${entry.key}`}
            >
              <summary>
                <strong>
                  {entry.key} · {entry.name}
                </strong>
                <span>
                  r{entry.scenario_revision} · {scenario.duration_s}s · seed{" "}
                  {entry.seed ?? scenario.seed} · {entry.requirements.length}{" "}
                  criteria{problems.length ? " · blocked" : ""}
                </span>
              </summary>
              <p>
                <code>{entry.scenario_id}</code> · {scenario.category} ·{" "}
                {scenario.map_name}
              </p>
              {problems.map((problem) => (
                <p key={problem} className="inline-error">
                  {problem}
                </p>
              ))}
              {entry.requirements.length === 0 && (
                <p className="suite-incomplete-note">
                  No criteria are pinned for this case. Execution completion
                  alone cannot provide a criteria-based pass.
                </p>
              )}
              <pre>
                {JSON.stringify(
                  {
                    scenario: scenario,
                    seed: entry.seed ?? scenario.seed,
                    parameter_overrides: entry.parameter_overrides,
                    effective_input_hash: entry.effective_input_hash,
                    criteria_hash: entry.criteria_hash,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          );
        })}
      </div>
    </section>
  );
}
