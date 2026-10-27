import { useCallback, useState, type FormEvent } from "react";
import { Archive, Layers3 } from "lucide-react";
import { api } from "../../api";
import type { Adapter } from "../../types";
import { Modal } from "../ui";
import {
  SuitePendingButton,
  useSuiteSubmissionKey,
  useSuiteResource,
} from "./SuiteCommon";
import type { BaselineDetail, CampaignDetail, SuiteDetail } from "./SuiteTypes";

import CarlaControlFields from "../carla/CarlaControlFields";
import {
  EMPTY_CARLA_CONTROL,
  type SimulationControlSelection,
} from "../carla/CarlaTypes";
import CampaignControlReview, {
  campaignCaseIssues,
} from "./CampaignControlReview";

export function CampaignLauncher({
  suite,
  adapters,
  onClose,
  onSubmitted,
}: {
  suite: SuiteDetail;
  adapters: Adapter[];
  onClose: () => void;
  onSubmitted: (campaign: CampaignDetail) => void;
}) {
  const [label, setLabel] = useState("");
  const [adapterId, setAdapterId] = useState(
    adapters.find((adapter) => adapter.status === "available")?.id ??
      "reference",
  );
  const adapterCatalog = useSuiteResource<Adapter[]>("/adapters");
  const [controlMode, setControlMode] = useState<"legacy" | "stack">("legacy");
  const [control, setControl] = useState<SimulationControlSelection>({
    ...EMPTY_CARLA_CONTROL,
  });
  const [validation, setValidation] = useState<{
    identity: string;
    valid: boolean;
  } | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const keyFor = useSuiteSubmissionKey();
  const availableAdapters = adapterCatalog.data ?? adapters;
  const adapter = availableAdapters.find((entry) => entry.id === adapterId);
  const live = adapterId === "carla" && controlMode === "stack";
  const controlIdentity = JSON.stringify([
    control,
    adapterId,
    adapter?.version,
  ]);
  const onControlValidity = useCallback(
    (valid: boolean) => setValidation({ identity: controlIdentity, valid }),
    [controlIdentity],
  );
  const controlValid =
    validation?.identity === controlIdentity && validation.valid;
  const adapterUsable =
    !adapterCatalog.loading &&
    !adapterCatalog.error &&
    !!adapterCatalog.data &&
    adapter?.status === "available";
  const casesValid =
    suite.cases.length === suite.case_count &&
    suite.cases.every(
      (entry) => !campaignCaseIssues(entry, live, adapterId === "carla").length,
    );
  const canSubmit =
    adapterUsable &&
    casesValid &&
    (!live ||
      (!!controlValid && !!adapter?.capabilities.includes("live_lidar_stack")));
  function resetControl() {
    setControl({ ...EMPTY_CARLA_CONTROL });
    setValidation(null);
    setError("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!canSubmit) {
      setError(
        "Verify the exact controller inputs and every pinned case before submitting.",
      );
      return;
    }
    setPending(true);
    const body = JSON.stringify({
      suite_id: suite.id,
      suite_revision: suite.revision,
      adapter_id: adapterId,
      label,
      ...(live ? { control } : {}),
    });
    try {
      onSubmitted(
        await api<CampaignDetail>("/campaigns", {
          method: "POST",
          headers: { "Idempotency-Key": keyFor("campaign", body) },
          body,
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Campaign submission failed.",
      );
    } finally {
      setPending(false);
    }
  }
  return (
    <Modal
      className={live ? "carla-launch-modal" : ""}
      title="Run a test suite"
      subtitle="Submit all pinned cases as one campaign using the execution queue."
      onClose={() => {
        if (!pending) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="modal-body">
          <div className="suite-form-note">
            <Layers3 size={20} />
            <span>
              <strong>
                {suite.name} · r{suite.revision}
              </strong>
              <small>
                {suite.case_count} cases ·{" "}
                {suite.cases.reduce(
                  (sum, entry) => sum + entry.requirements.length,
                  0,
                )}{" "}
                pinned criteria
              </small>
            </span>
          </div>
          <label>
            Campaign label
            <input
              required
              autoFocus
              maxLength={180}
              value={label}
              disabled={pending}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="A name for this execution attempt"
            />
          </label>
          <label>
            Campaign adapter
            <select
              aria-label="Campaign adapter"
              value={adapterId}
              disabled={pending}
              onChange={(event) => {
                setAdapterId(event.target.value);
                setControlMode("legacy");
                resetControl();
              }}
            >
              {availableAdapters.map((entry) => (
                <option
                  key={entry.id}
                  value={entry.id}
                  disabled={entry.status !== "available"}
                >
                  {entry.name} · {entry.version}
                  {entry.status !== "available"
                    ? ` (${entry.status.replaceAll("_", " ")})`
                    : ""}
                </option>
              ))}
            </select>
          </label>
          {adapter && (
            <div className="form-note">
              <strong>
                {adapter.name} · {adapter.version}
              </strong>
              <p>{adapter.description}</p>
              {adapter.limitations.map((limitation) => (
                <p key={limitation}>{limitation}</p>
              ))}
            </div>
          )}
          {!adapterUsable && (
            <div role="alert" className="inline-error">
              {adapterCatalog.error ||
                (adapterCatalog.loading
                  ? "Loading current adapters…"
                  : "The selected adapter is unavailable for submission.")}
              <button
                type="button"
                className="button secondary small"
                disabled={pending || adapterCatalog.loading}
                onClick={adapterCatalog.reload}
              >
                Retry campaign adapters
              </button>
            </div>
          )}
          {adapterId === "carla" && (
            <>
              <label>
                CARLA controller
                <select
                  aria-label="CARLA campaign controller mode"
                  disabled={pending}
                  value={controlMode}
                  onChange={(event) => {
                    setControlMode(event.target.value as "legacy" | "stack");
                    resetControl();
                  }}
                >
                  <option value="legacy">
                    Existing ground-truth controller
                  </option>
                  <option
                    value="stack"
                    disabled={
                      !adapter?.capabilities.includes("live_lidar_stack")
                    }
                  >
                    Saved shared stack · live sensor inputs
                  </option>
                </select>
              </label>
              {live ? (
                <CarlaControlFields
                  value={control}
                  disabled={pending}
                  adapterVersion={adapter?.version}
                  effectiveSeed={
                    suite.cases[0]?.seed ??
                    suite.cases[0]?.scenario_snapshot.seed
                  }
                  onChange={(value) => {
                    setValidation(null);
                    setControl(value);
                    setError("");
                  }}
                  onValidity={onControlValidity}
                />
              ) : (
                <p className="suite-form-help">
                  Surrounding actor ground truth drives this controller.
                  Captured RGB/LiDAR do not drive its decisions. No shared-stack
                  control selection is sent.
                </p>
              )}
            </>
          )}
          <CampaignControlReview
            suite={suite}
            adapter={adapter}
            adapterId={adapterId}
            live={live}
            control={control}
            controlValid={!!controlValid}
          />
          <p className="suite-form-help">
            The API validates the complete submission before creating member
            jobs. Submission acceptance does not establish sensor fidelity or a
            passing verdict.
          </p>
          {error && (
            <div>
              <p className="inline-error" role="alert">
                {error}
              </p>
              <p className="suite-form-help">
                Retrying unchanged inputs reuses this campaign's request key. A
                lost response does not imply that its jobs were rejected.
              </p>
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
            label={error ? "Retry campaign submission" : "Submit campaign"}
            pendingLabel="Submitting campaign…"
            disabled={!canSubmit}
          />
        </footer>
      </form>
    </Modal>
  );
}

export function BaselineCapture({
  campaign,
  onClose,
  onCaptured,
}: {
  campaign: CampaignDetail;
  onClose: () => void;
  onCaptured: (baseline: BaselineDetail) => void;
}) {
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const keyFor = useSuiteSubmissionKey();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    const body = JSON.stringify({ campaign_id: campaign.id, name, note });
    try {
      onCaptured(
        await api<BaselineDetail>("/baselines", {
          method: "POST",
          headers: { "Idempotency-Key": keyFor("baseline", body) },
          body,
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Baseline capture failed.",
      );
    } finally {
      setPending(false);
    }
  }
  return (
    <Modal
      title="Capture an immutable baseline"
      subtitle="Freeze this campaign's report and preserve its original verdict."
      onClose={() => {
        if (!pending) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="modal-body">
          <div className="suite-form-note">
            <Archive size={20} />
            <span>
              <strong>{campaign.label}</strong>
              <small>
                {campaign.counts.total} cases · {campaign.verdict} · suite r
                {campaign.suite_revision}
              </small>
            </span>
          </div>
          <label>
            Baseline name
            <input
              required
              autoFocus
              maxLength={180}
              disabled={pending}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            Baseline note
            <textarea
              required
              rows={3}
              maxLength={4000}
              disabled={pending}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Explain why this evidence is being retained."
            />
          </label>
          <p className="suite-capture-boundary">
            A baseline preserves evidence; it does not approve a release. Failed
            or incomplete evidence stays labelled as such after capture.
          </p>
          {error && (
            <div>
              <p className="inline-error" role="alert">
                {error}
              </p>
              <p className="suite-form-help">
                Unchanged retries reuse the original capture key.
              </p>
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
            label={error ? "Retry baseline capture" : "Capture baseline"}
            pendingLabel="Capturing baseline…"
            disabled={campaign.status !== "finished" || !note.trim()}
          />
        </footer>
      </form>
    </Modal>
  );
}
