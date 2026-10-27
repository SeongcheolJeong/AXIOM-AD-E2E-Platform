import {
  ChevronDown,
  GitBranch,
  Plus,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import {
  categoryLabels,
  metricLabels,
  type Parameters,
  type Requirement,
  type Scenario,
} from "../../types";
import { useResource } from "../../api";
import { criterionOperator, overrideFields } from "./SuiteCommon";

export interface SuiteCaseDraft {
  clientId: string;
  key: string;
  name: string;
  scenarioId: string;
  scenarioRevision: number;
  sourceSnapshot: Scenario | null;
  sourceIsEffective?: boolean;
  seed: string;
  overrides: Partial<Record<keyof Parameters, string>>;
  requirementIds: string[];
}

export function newSuiteCase(scenario?: Scenario, ordinal = 1): SuiteCaseDraft {
  return {
    clientId: crypto.randomUUID(),
    key: `case-${ordinal}`,
    name: scenario?.name ?? "",
    scenarioId: scenario?.id ?? "",
    scenarioRevision: scenario?.revision ?? 1,
    sourceSnapshot: scenario ?? null,
    seed: "",
    overrides: {},
    requirementIds: [],
  };
}

export function requirementApplies(
  requirement: Requirement,
  scenario: Scenario | null,
) {
  if (!scenario || (!requirement.category && !requirement.scenario_ids.length))
    return false;
  return requirement.scenario_ids.length > 0
    ? requirement.scenario_ids.includes(scenario.id)
    : requirement.category === scenario.category;
}

export default function SuiteCaseEditor({
  value,
  index,
  scenarios,
  requirements,
  disabled,
  onChange,
  onRemove,
  onDuplicate,
}: {
  value: SuiteCaseDraft;
  index: number;
  scenarios: Scenario[];
  requirements: Requirement[];
  disabled: boolean;
  onChange: (value: SuiteCaseDraft) => void;
  onRemove: () => void;
  onDuplicate: () => void;
}) {
  const history = useResource<Scenario[]>(
    value.scenarioId
      ? `/scenarios/${encodeURIComponent(value.scenarioId)}/revisions`
      : null,
  );
  const revisions =
    history.data?.filter((scenario) => scenario.id === value.scenarioId) ?? [];
  const original = revisions.find(
    (scenario) => scenario.revision === value.scenarioRevision,
  );
  const source = original ?? value.sourceSnapshot;
  const baseKnown = !!original || !value.sourceIsEffective;
  const latest = scenarios.find((scenario) => scenario.id === value.scenarioId);
  const applicable = requirements.filter((requirement) =>
    requirementApplies(requirement, source),
  );
  const pinnedOlder = latest && latest.revision !== value.scenarioRevision;
  const update = <K extends keyof SuiteCaseDraft>(
    key: K,
    field: SuiteCaseDraft[K],
  ) => onChange({ ...value, [key]: field });
  return (
    <article
      className="suite-case-editor"
      aria-label={`Suite case ${index + 1}`}
    >
      <div className="suite-case-editor-heading">
        <span className="suite-case-number">
          {String(index + 1).padStart(2, "0")}
        </span>
        <strong>{value.name || "Untitled case"}</strong>
        <button
          type="button"
          className="icon-button"
          aria-label={`Duplicate case ${index + 1}`}
          disabled={disabled}
          onClick={onDuplicate}
        >
          <Plus size={16} />
        </button>
        <button
          type="button"
          className="icon-button"
          aria-label={`Remove case ${index + 1}`}
          disabled={disabled}
          onClick={onRemove}
        >
          <Trash2 size={15} />
        </button>
      </div>
      <div className="suite-case-fields">
        <label>
          Case key
          <input
            required
            maxLength={64}
            pattern="[A-Za-z0-9][A-Za-z0-9_.\-]*"
            title="Start with a letter or digit; use letters, digits, underscores, dots, and hyphens."
            value={value.key}
            disabled={disabled}
            onChange={(event) => update("key", event.target.value)}
            placeholder="Stable identity across suite revisions"
          />
        </label>
        <label>
          Case name
          <input
            required
            maxLength={180}
            value={value.name}
            disabled={disabled}
            onChange={(event) => update("name", event.target.value)}
          />
        </label>
        <label className="suite-field-wide">
          Scenario
          <select
            required
            value={value.scenarioId}
            disabled={disabled}
            onChange={(event) => {
              const scenario = scenarios.find(
                (candidate) => candidate.id === event.target.value,
              );
              if (!scenario) return;
              onChange({
                ...value,
                name:
                  !value.name || value.name === source?.name
                    ? scenario.name
                    : value.name,
                scenarioId: scenario.id,
                scenarioRevision: scenario.revision,
                sourceSnapshot: scenario,
                sourceIsEffective: false,
                requirementIds: value.requirementIds.filter((id) =>
                  requirements.some(
                    (requirement) =>
                      requirement.id === id &&
                      requirementApplies(requirement, scenario),
                  ),
                ),
              });
            }}
          >
            <option value="" disabled>
              Select an existing scenario
            </option>
            {scenarios.map((scenario) => (
              <option key={scenario.id} value={scenario.id}>
                {scenario.name} · current r{scenario.revision}
              </option>
            ))}
          </select>
        </label>
        <div className="suite-case-revision">
          <GitBranch size={15} />
          <span>
            Pinned scenario revision <strong>r{value.scenarioRevision}</strong>
            {source && (
              <small>
                {categoryLabels[source.category]} · {source.weather} ·{" "}
                {source.duration_s} s
              </small>
            )}
          </span>
          {pinnedOlder && (
            <button
              type="button"
              className="suite-text-button"
              disabled={disabled}
              onClick={() =>
                onChange({
                  ...value,
                  scenarioRevision: latest.revision,
                  sourceSnapshot: latest,
                  sourceIsEffective: false,
                  requirementIds: value.requirementIds.filter((id) =>
                    requirements.some(
                      (requirement) =>
                        requirement.id === id &&
                        requirementApplies(requirement, latest),
                    ),
                  ),
                })
              }
            >
              Use current r{latest.revision}
            </button>
          )}
        </div>
        <label>
          Scenario revision
          <select
            disabled={disabled || !revisions.length}
            value={value.scenarioRevision}
            onChange={(event) => {
              const revision = revisions.find(
                (scenario) => scenario.revision === Number(event.target.value),
              );
              if (revision)
                onChange({
                  ...value,
                  scenarioRevision: revision.revision,
                  sourceSnapshot: revision,
                  sourceIsEffective: false,
                  requirementIds: value.requirementIds.filter((id) =>
                    requirements.some(
                      (requirement) =>
                        requirement.id === id &&
                        requirementApplies(requirement, revision),
                    ),
                  ),
                });
            }}
          >
            {!revisions.length && (
              <option value={value.scenarioRevision}>
                r{value.scenarioRevision} · pinned
              </option>
            )}
            {revisions.map((revision) => (
              <option key={revision.revision} value={revision.revision}>
                r{revision.revision}
                {revision.revision === latest?.revision
                  ? " · current"
                  : " · historical"}
              </option>
            ))}
          </select>
          {history.error && (
            <small>
              Revision history unavailable. The selected revision remains
              pinned.
            </small>
          )}
        </label>
        <label>
          Seed override <span className="optional">optional</span>
          <input
            type="number"
            min={0}
            max={2147483647}
            step={1}
            value={value.seed}
            disabled={disabled}
            onChange={(event) => update("seed", event.target.value)}
            placeholder={`Inherit ${baseKnown ? (source?.seed ?? "scenario seed") : "scenario seed"}`}
          />
        </label>
      </div>
      <details className="suite-override-editor">
        <summary>
          <SlidersHorizontal size={15} />
          Parameter overrides
          <span>
            {
              Object.values(value.overrides).filter((entry) => entry !== "")
                .length
            }{" "}
            specified
          </span>
          <ChevronDown size={14} />
        </summary>
        <p>
          Blank fields inherit the pinned scenario. Values are SI units.
          {!baseKnown && " Original revision values are loading."}
        </p>
        <div className="suite-override-grid">
          {overrideFields.map((field) => (
            <label key={field.key}>
              {field.label}
              <div className="unit-input">
                <input
                  type="number"
                  min={field.min}
                  max={field.max}
                  step="any"
                  disabled={disabled}
                  value={value.overrides[field.key] ?? ""}
                  placeholder={
                    baseKnown ? String(source?.parameters[field.key] ?? "") : ""
                  }
                  onChange={(event) =>
                    update("overrides", {
                      ...value.overrides,
                      [field.key]: event.target.value,
                    })
                  }
                />
                <span>{field.unit}</span>
              </div>
            </label>
          ))}
        </div>
      </details>
      <fieldset className="suite-requirement-picker" disabled={disabled}>
        <legend>
          Applicable requirements <span>select at least one</span>
        </legend>
        {applicable.length ? (
          applicable.map((requirement) => (
            <label key={requirement.id}>
              <input
                type="checkbox"
                checked={value.requirementIds.includes(requirement.id)}
                onChange={(event) =>
                  update(
                    "requirementIds",
                    event.target.checked
                      ? [...value.requirementIds, requirement.id]
                      : value.requirementIds.filter(
                          (id) => id !== requirement.id,
                        ),
                  )
                }
              />
              <span>
                <strong>{requirement.name}</strong>
                <small>
                  {metricLabels[requirement.metric_name] ??
                    requirement.metric_name}{" "}
                  {criterionOperator(requirement.operator)}{" "}
                  {requirement.threshold}
                </small>
              </span>
            </label>
          ))
        ) : (
          <p className="suite-inline-empty">
            {source
              ? "No scoped requirement applies to this scenario. Add a requirement in Validation first."
              : "Select a scenario to see applicable requirements."}
          </p>
        )}
      </fieldset>
    </article>
  );
}
