import CarlaSavedContext from "../carla/CarlaSavedContext";
import type { CampaignCaseReport, CampaignDetail } from "./SuiteTypes";

export default function CampaignPinnedPlan({
  campaign,
}: {
  campaign: CampaignDetail;
}) {
  const plan = campaign.execution_plan;
  if (!plan)
    return (
      <p
        className="suite-panel-footnote"
        data-testid="campaign-default-control"
      >
        No shared-stack execution plan is recorded for this campaign.
        Adapter-default and historical reports retain their original evidence;
        the adapter name alone cannot establish a control context.
      </p>
    );
  const valid =
    plan.schema_version === "campaign-execution-plan/1" &&
    plan.metric_contract_version === "carla-observed-contact-count/1" &&
    plan.members.length === campaign.cases.length &&
    plan.members.every((member, index) => {
      const entry = campaign.cases[index];
      const expected = entry.expected_execution;
      return (
        member.key === entry.key &&
        !!expected &&
        member.key === expected.key &&
        member.job_id === expected.job_id &&
        member.input_hash === expected.input_hash &&
        member.context_hash === expected.context_hash &&
        member.request_hash === expected.request_hash &&
        (entry.job_id === member.job_id ||
          (entry.job_id === null && entry.job_status === "missing"))
      );
    }) &&
    plan.members[0]?.context_hash === plan.context_template.context_hash;
  if (!valid)
    return (
      <p className="inline-error" role="alert">
        Saved campaign execution plan does not match its case identities.
        Controller evidence is unavailable; no current library configuration is
        substituted.
      </p>
    );
  return (
    <section
      className="suite-campaign-plan"
      aria-label="Pinned campaign control"
      data-testid="campaign-pinned-plan"
    >
      <h3>Pinned campaign control</h3>
      <p>
        The same exact selection applies to all {plan.members.length} members.
        The template below belongs to the first case, including that case's
        seed. Every case has a separate expected input/context fingerprint shown
        with its result.
      </p>
      <dl className="suite-plan-fields">
        <div>
          <dt>Measurement contract</dt>
          <dd>
            <code>{plan.metric_contract_version}</code>
          </dd>
        </div>
        <div>
          <dt>Execution plan SHA-256</dt>
          <dd>
            <code>{plan.plan_hash}</code>
          </dd>
        </div>
      </dl>
      <details>
        <summary>Inspect the saved control selection</summary>
        <pre>{JSON.stringify(plan.control, null, 2)}</pre>
      </details>
      <CarlaSavedContext context={plan.context_template} />
    </section>
  );
}

export function CampaignCaseFingerprint({
  entry,
}: {
  entry: CampaignCaseReport;
}) {
  const expected = entry.expected_execution;
  const actual = entry.execution_context;
  const hasActual = !!actual.control_context_hash;
  const matches =
    !!expected &&
    hasActual &&
    expected.context_hash === actual.control_context_hash &&
    expected.input_hash === actual.input_hash;
  return (
    <section
      className="suite-case-fingerprint"
      aria-label={`Execution fingerprints for ${entry.key}`}
    >
      <dl className="suite-plan-fields">
        {expected && (
          <>
            <div>
              <dt>Submitted member job ID</dt>
              <dd>
                <code>{expected.job_id}</code>
              </dd>
            </div>
            <div>
              <dt>Expected member input SHA-256</dt>
              <dd>
                <code>{expected.input_hash}</code>
              </dd>
            </div>
            <div>
              <dt>Expected control context SHA-256</dt>
              <dd>
                <code>{expected.context_hash}</code>
              </dd>
            </div>
            <div>
              <dt>Prepared request SHA-256</dt>
              <dd>
                <code>{expected.request_hash}</code>
              </dd>
            </div>
          </>
        )}
        <div>
          <dt>Observed input SHA-256</dt>
          <dd>
            <code>{actual.input_hash ?? "Not available"}</code>
          </dd>
        </div>
        <div>
          <dt>Observed control context SHA-256</dt>
          <dd>
            <code>
              {actual.control_context_hash ?? "Not recorded / no result"}
            </code>
          </dd>
        </div>
      </dl>
      {expected && entry.job_status === "missing" && (
        <p className="suite-incomplete-note">
          The original member job is missing. Its submitted plan and expected
          hashes remain preserved; actual result evidence is unavailable.
        </p>
      )}
      {expected && (
        <p className={matches ? "suite-form-help" : "suite-incomplete-note"}>
          {matches
            ? "Expected and observed member fingerprints match. This is input identity evidence, not a passing verdict."
            : hasActual
              ? "Expected and observed fingerprints do not match. Use the server's case/check reasons; do not treat this as comparable evidence."
              : "The member's expected inputs are pinned, but its observed control fingerprint is missing or pending. No fingerprint is inferred from adapter metadata."}
        </p>
      )}
      {!expected && !hasActual && (
        <p className="suite-form-help">
          This historical or adapter-default case has no recorded shared-control
          fingerprint. Comparisons requiring it remain subject to the server's
          missing-evidence policy.
        </p>
      )}
    </section>
  );
}
