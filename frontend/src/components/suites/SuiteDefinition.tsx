import { FileJson, Fingerprint, GitBranch } from "lucide-react";
import { categoryLabels, metricLabels } from "../../types";
import { CopyButton } from "../ui";
import { criterionOperator, suiteNumber } from "./SuiteCommon";
import type { SuiteDetail } from "./SuiteTypes";

export default function SuiteDefinition({ suite }: { suite: SuiteDetail }) {
  const checks = suite.cases.reduce(
    (sum, entry) => sum + entry.requirements.length,
    0,
  );
  return (
    <section
      className="panel suite-definition"
      aria-label="Pinned suite definition"
    >
      <div className="suite-panel-heading">
        <GitBranch size={17} />
        <h3>Pinned cases</h3>
        <span>
          {suite.case_count} cases · {checks} criteria
        </span>
      </div>
      <div className="suite-case-definition-list">
        {suite.cases.map((entry) => (
          <details key={entry.key} className="suite-definition-case">
            <summary>
              <span className="suite-case-key">{entry.key}</span>
              <span>
                <strong>{entry.name}</strong>
                <small>
                  {categoryLabels[entry.scenario_snapshot.category]} · scenario
                  r{entry.scenario_revision} · seed{" "}
                  {entry.scenario_snapshot.seed}
                </small>
              </span>
              <span className="suite-criteria-count">
                {entry.requirements.length} criteria
              </span>
            </summary>
            <div className="suite-definition-body">
              <p className="suite-form-help">
                {entry.scenario_snapshot.name} ·{" "}
                {entry.scenario_snapshot.map_name} ·{" "}
                {entry.scenario_snapshot.weather} ·{" "}
                {entry.scenario_snapshot.duration_s} s
              </p>
              <div className="suite-pinned-criteria">
                {entry.requirements.map((criterion) => (
                  <div key={criterion.id}>
                    <div>
                      <strong>{criterion.definition.name}</strong>
                      <span>
                        {metricLabels[criterion.definition.metric_name] ??
                          criterion.definition.metric_name}{" "}
                        {criterionOperator(criterion.definition.operator)}{" "}
                        {suiteNumber(criterion.definition.threshold)}{" "}
                        {criterion.unit}
                      </span>
                    </div>
                    <code title={criterion.definition_hash}>
                      {criterion.definition_hash}
                    </code>
                  </div>
                ))}
              </div>
              <dl className="suite-hash-list">
                <div>
                  <dt>Effective input</dt>
                  <dd>{entry.effective_input_hash}</dd>
                </div>
                <div>
                  <dt>Criteria</dt>
                  <dd>{entry.criteria_hash}</dd>
                </div>
              </dl>
              <details className="suite-json-disclosure">
                <summary>
                  <FileJson size={14} />
                  Effective scenario snapshot
                </summary>
                <pre aria-label={`Pinned scenario JSON for ${entry.key}`}>
                  {JSON.stringify(entry.scenario_snapshot, null, 2)}
                </pre>
              </details>
            </div>
          </details>
        ))}
      </div>
      <div className="suite-definition-hash">
        <Fingerprint size={15} />
        <span>Definition SHA-256</span>
        <code>{suite.definition_hash}</code>
        <CopyButton text={suite.definition_hash} label="Copy suite hash" />
      </div>
      <p className="suite-panel-footnote">
        Evaluator {suite.evaluator_version}. Requirements are captured
        definitions; a suite is test content, not evidence that the tests
        passed.
      </p>
    </section>
  );
}
