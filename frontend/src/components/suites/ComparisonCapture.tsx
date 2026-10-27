import { useState, type FormEvent } from "react";
import { GitCompareArrows } from "lucide-react";
import { api } from "../../api";
import { Modal } from "../ui";
import { SuitePendingButton, useSuiteSubmissionKey } from "./SuiteCommon";
import type {
  BaselineSummary,
  CampaignDetail,
  ComparisonDetail,
} from "./SuiteTypes";

export default function ComparisonCapture({
  campaign,
  baseline,
  onClose,
  onCaptured,
}: {
  campaign: CampaignDetail;
  baseline: BaselineSummary;
  onClose: () => void;
  onCaptured: (capture: ComparisonDetail) => void;
}) {
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const keyFor = useSuiteSubmissionKey();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setPending(true);
    const body = JSON.stringify({
      campaign_id: campaign.id,
      baseline_id: baseline.id,
      name,
      note,
    });
    try {
      onCaptured(
        await api<ComparisonDetail>("/comparisons", {
          method: "POST",
          headers: { "Idempotency-Key": keyFor("comparison", body) },
          body,
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Comparison capture failed.",
      );
    } finally {
      setPending(false);
    }
  }
  return (
    <Modal
      title="Capture an immutable comparison"
      subtitle="Preserve the baseline, candidate report, comparison policy, and exact export bytes."
      onClose={() => {
        if (!pending) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="modal-body">
          <div className="suite-form-note">
            <GitCompareArrows size={21} />
            <span>
              <strong>
                {baseline.name} → {campaign.label}
              </strong>
              <small>
                Candidate verdict: {campaign.verdict} ·{" "}
                {campaign.evaluation_complete
                  ? "fully evaluated"
                  : "evaluation incomplete"}
              </small>
            </span>
          </div>
          <label>
            Comparison name
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
            Comparison note
            <textarea
              required
              maxLength={4000}
              rows={3}
              value={note}
              disabled={pending}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Record the review context and remaining limitations."
            />
          </label>
          <p className="suite-capture-boundary">
            Capturing records this comparison; it does not approve a release or
            resolve missing evidence. Existing failure and incompleteness labels
            remain in the manifest.
          </p>
          {error && (
            <div>
              <p className="inline-error" role="alert">
                {error}
              </p>
              <p className="suite-form-help">
                Retrying the same name, note, and selection reuses the original
                capture key.
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
            label={error ? "Retry comparison capture" : "Capture comparison"}
            pendingLabel="Capturing comparison…"
            disabled={campaign.status !== "finished" || !note.trim()}
          />
        </footer>
      </form>
    </Modal>
  );
}
