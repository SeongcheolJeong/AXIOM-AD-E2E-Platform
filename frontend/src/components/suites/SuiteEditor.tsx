import { useState, type FormEvent } from "react";
import { GitBranch, Plus } from "lucide-react";
import { api } from "../../api";
import type { Parameters, Requirement, Scenario } from "../../types";
import { Modal } from "../ui";
import SuiteCaseEditor, {
  newSuiteCase,
  type SuiteCaseDraft,
} from "./SuiteCaseEditor";
import { SuitePendingButton } from "./SuiteCommon";
import type { SuiteCreate, SuiteDetail } from "./SuiteTypes";

export default function SuiteEditor({
  suite,
  expectedRevision,
  scenarios,
  requirements,
  onClose,
  onSaved,
}: {
  suite?: SuiteDetail;
  expectedRevision?: number;
  scenarios: Scenario[];
  requirements: Requirement[];
  onClose: () => void;
  onSaved: (suite: SuiteDetail) => void;
}) {
  const [name, setName] = useState(suite?.name ?? "");
  const [guardRevision] = useState(expectedRevision ?? suite?.revision ?? 1);
  const [sourceRevision] = useState(suite?.revision ?? 1);
  const [description, setDescription] = useState(suite?.description ?? "");
  const [cases, setCases] = useState<SuiteCaseDraft[]>(
    suite
      ? suite.cases.map((entry) => ({
          clientId: crypto.randomUUID(),
          key: entry.key,
          name: entry.name,
          scenarioId: entry.scenario_id,
          scenarioRevision: entry.scenario_revision,
          sourceSnapshot: entry.scenario_snapshot,
          sourceIsEffective: true,
          seed:
            entry.seed === null || entry.seed === undefined
              ? ""
              : String(entry.seed),
          overrides: Object.fromEntries(
            Object.entries(entry.parameter_overrides)
              .filter(([, value]) => value !== null)
              .map(([key, value]) => [key, String(value)]),
          ),
          requirementIds: [...entry.requirement_ids],
        }))
      : [newSuiteCase(scenarios[0])],
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  function nextKey() {
    let index = cases.length + 1;
    while (cases.some((entry) => entry.key === `case-${index}`)) index++;
    return `case-${index}`;
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!cases.length || cases.length > 100) {
      setError("A suite needs between 1 and 100 cases.");
      return;
    }
    if (new Set(cases.map((entry) => entry.key.trim())).size !== cases.length) {
      setError(
        "Every case must have a unique key. Keep keys stable between revisions for comparisons.",
      );
      return;
    }
    const missing = cases.find(
      (entry) =>
        entry.requirementIds.length < 1 || entry.requirementIds.length > 32,
    );
    if (missing) {
      setError(
        `Case ${missing.key || "without a key"} needs 1–32 applicable requirements.`,
      );
      return;
    }
    const body: SuiteCreate = {
      name,
      description,
      cases: cases.map((entry) => ({
        key: entry.key.trim(),
        name: entry.name,
        scenario_id: entry.scenarioId,
        scenario_revision: entry.scenarioRevision,
        ...(entry.seed !== "" ? { seed: Number(entry.seed) } : {}),
        parameter_overrides: Object.fromEntries(
          Object.entries(entry.overrides)
            .filter(([, value]) => value !== "")
            .map(([key, value]) => [key, Number(value)]),
        ) as Partial<Parameters>,
        requirement_ids: entry.requirementIds,
      })),
    };
    setPending(true);
    try {
      const result = await api<SuiteDetail>(
        suite ? `/suites/${encodeURIComponent(suite.id)}/revisions` : "/suites",
        {
          method: "POST",
          body: JSON.stringify(
            suite ? { ...body, expected_revision: guardRevision } : body,
          ),
        },
      );
      onSaved(result);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Suite definition could not be saved.",
      );
    } finally {
      setPending(false);
    }
  }
  return (
    <Modal
      className="suite-editor-dialog"
      title={suite ? "Create suite revision" : "Create a test suite"}
      subtitle="Pin scenario revisions and requirement definitions into a reproducible test set."
      onClose={() => {
        if (!pending) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="modal-body">
          <div className="suite-authoring-intro">
            <label>
              Suite name
              <input
                required
                autoFocus
                maxLength={180}
                disabled={pending}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Highway braking regression"
              />
            </label>
            <label>
              Description
              <textarea
                rows={2}
                maxLength={4000}
                disabled={pending}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
          </div>
          {suite && (
            <p className="suite-form-note">
              <GitBranch size={16} />
              Starting from r{sourceRevision}; saving checks that the latest
              revision is still r{guardRevision}. Historical suite revisions
              remain unchanged.
            </p>
          )}
          <div className="suite-editor-section">
            <h3>Cases & pinned criteria</h3>
            <span>{cases.length} / 100 cases</span>
          </div>
          <p className="suite-form-help">
            Case keys identify the same test across revisions. Each case selects
            requirements that apply to its pinned scenario; current coverage
            verdicts are not copied into the suite.
          </p>
          {cases.map((entry, index) => (
            <SuiteCaseEditor
              key={entry.clientId}
              value={entry}
              index={index}
              scenarios={scenarios}
              requirements={requirements}
              disabled={pending}
              onChange={(value) =>
                setCases((previous) =>
                  previous.map((candidate) =>
                    candidate.clientId === entry.clientId ? value : candidate,
                  ),
                )
              }
              onRemove={() =>
                setCases((previous) =>
                  previous.filter(
                    (candidate) => candidate.clientId !== entry.clientId,
                  ),
                )
              }
              onDuplicate={() => {
                if (cases.length >= 100) return;
                setCases((previous) => [
                  ...previous,
                  {
                    ...entry,
                    clientId: crypto.randomUUID(),
                    key: nextKey(),
                    name: `${entry.name.slice(0, 170)} · copy`,
                    overrides: { ...entry.overrides },
                    requirementIds: [...entry.requirementIds],
                  },
                ]);
              }}
            />
          ))}
          <button
            type="button"
            className="button secondary suite-add-case"
            disabled={pending || cases.length >= 100 || !scenarios.length}
            onClick={() => {
              const entry = newSuiteCase(scenarios[0], cases.length + 1);
              entry.key = nextKey();
              setCases((previous) => [...previous, entry]);
            }}
          >
            <Plus size={15} />
            Add case
          </button>
          {error && (
            <div>
              <p className="inline-error" role="alert">
                {error}
              </p>
              {suite && (
                <p className="suite-form-help">
                  If another editor published a revision, close this form and
                  reload the suite before editing again.
                </p>
              )}
            </div>
          )}
        </div>
        <footer>
          <button
            type="button"
            className="button secondary"
            disabled={pending}
            onClick={onClose}
          >
            Cancel
          </button>
          <SuitePendingButton
            pending={pending}
            label={suite ? "Publish suite revision" : "Create suite"}
            pendingLabel="Saving suite…"
            disabled={!cases.length}
          />
        </footer>
      </form>
    </Modal>
  );
}
