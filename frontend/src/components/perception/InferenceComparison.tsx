import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowDownToLine,
  Columns2,
  Fingerprint,
  LoaderCircle,
  Plus,
} from "lucide-react";
import { api, formatDate } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { Empty, ErrorState, Loading, Modal } from "../ui";
import { useJobResource } from "../jobs/useJobResource";
import { useSensorResource } from "../sensors/SensorData";
import InferenceFramePanel from "./InferenceFramePanel";
import {
  terminalInference,
  type InferenceJobs,
  type PredictionComparison,
  type PredictionComparisonSummary,
  type PredictionComparisonRow,
} from "./PerceptionTypes";

const rowLabels: Record<PredictionComparisonRow["status"], string> = {
  same_input: "Same input",
  input_changed: "Input changed",
  left_only: "Left only",
  right_only: "Right only",
  not_evaluated: "Not evaluated",
};

export default function InferenceComparison({
  initialJob,
}: {
  initialJob: string | null;
}) {
  const list = useSensorResource<PredictionComparisonSummary[]>(
    "/perception/comparisons",
  );
  const jobs = useJobResource<InferenceJobs>(
    "/perception/jobs?limit=100",
    5000,
  );
  const [id, setId] = useState(""),
    [rowKey, setRowKey] = useState("");
  const [open, setOpen] = useState(false);
  const [left, setLeft] = useState(initialJob ?? ""),
    [right, setRight] = useState("");
  const [name, setName] = useState(""),
    [note, setNote] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const keys = useRef(new Map<string, string>());
  const detail = useSensorResource<PredictionComparison>(
    id ? `/perception/comparisons/${encodeURIComponent(id)}` : null,
  );
  const data = detail.data?.id === id ? detail.data : null;
  const terminal =
    jobs.data?.items.filter((job) => terminalInference(job.status)) ?? [];
  useEffect(() => {
    if (!id && list.data?.[0]) setId(list.data[0].id);
  }, [id, list.data]);
  const row = data?.rows.find((item) => item.key === rowKey) ?? data?.rows[0];
  async function capture(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!left || !right || left === right) {
      setError("Choose two different terminal experiments.");
      return;
    }
    const body = JSON.stringify({
      name,
      note,
      left_job_id: left,
      right_job_id: right,
    });
    let key = keys.current.get(body);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(body, key);
    }
    setBusy(true);
    try {
      const result = await api<PredictionComparison>(
        "/perception/comparisons",
        { method: "POST", headers: { "Idempotency-Key": key }, body },
      );
      setId(result.id);
      setRowKey("");
      setOpen(false);
      list.reload();
      detail.reload();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Comparison capture failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="perception-comparisons">
      <div className="perception-section-toolbar">
        <div>
          <h3>Saved prediction comparisons</h3>
        </div>
        <button className="button primary" onClick={() => setOpen(true)}>
          <Plus size={15} />
          Capture comparison
        </button>
      </div>
      <details className="perception-comparison-scope">
        <summary>Comparison scope · predictions, not accuracy</summary>
        <p>
          Model or threshold changes are disclosed. Differences are not accuracy
          improvements or regressions without ground truth. Failed and
          unattempted inputs remain in the denominator.
        </p>
      </details>
      {list.error ? (
        <ErrorState message={list.error} retry={list.reload} />
      ) : list.loading ? (
        <Loading label="Loading saved comparisons…" />
      ) : !list.data?.length ? (
        <section className="panel">
          <Empty
            title="Compare recorded predictions"
            description="Complete two experiments, then capture an immutable comparison. It preserves exact evidence from both sides."
          />
        </section>
      ) : (
        <>
          <label className="perception-select-field">
            Saved comparison
            <select
              aria-label="Select prediction comparison"
              value={id}
              onChange={(e) => {
                setId(e.target.value);
                setRowKey("");
              }}
            >
              {list.data.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {formatDate(item.created_at)}
                </option>
              ))}
            </select>
          </label>
          {detail.error ? (
            <ErrorState message={detail.error} retry={detail.reload} />
          ) : !data ? (
            <Loading label="Loading captured comparison evidence…" />
          ) : (
            <div data-testid="prediction-comparison-detail">
              <section className="panel perception-comparison-header">
                <div className="perception-section-toolbar">
                  <div>
                    <span className="perception-eyebrow">
                      IMMUTABLE COMPARISON
                    </span>
                    <h2>{data.name}</h2>
                    <p>{data.note}</p>
                  </div>
                  <DownloadLink
                    className="button secondary"
                    href={data.download_url}
                    download
                  >
                    <ArrowDownToLine size={14} />
                    Download prediction comparison
                  </DownloadLink>
                </div>
                <div className="perception-comparison-counts">
                  {Object.entries(data.counts).map(([key, value]) => (
                    <div key={key}>
                      <strong>{value}</strong>
                      <span>
                        {key.replaceAll("_", " ")}
                        {key === "total" ? " inputs" : ""}
                      </span>
                    </div>
                  ))}
                </div>
                <details className="perception-details">
                  <summary>
                    Captured jobs & configuration differences ·{" "}
                    {data.configuration_differences.length}
                  </summary>
                  <div className="perception-comparison-identities">
                    <div>
                      <span>LEFT · A</span>
                      <code>{data.left_job_id}</code>
                      <small>{data.left_manifest_hash}</small>
                    </div>
                    <div>
                      <span>RIGHT · B</span>
                      <code>{data.right_job_id}</code>
                      <small>{data.right_manifest_hash}</small>
                    </div>
                  </div>
                  {data.configuration_differences.length ? (
                    <div className="table-scroll">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>Captured field</th>
                            <th>Left · A</th>
                            <th>Right · B</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.configuration_differences.map((difference) => (
                            <tr key={difference.field}>
                              <td>{difference.field}</td>
                              <td className="perception-value">
                                {typeof difference.left === "string"
                                  ? difference.left
                                  : JSON.stringify(difference.left)}
                              </td>
                              <td className="perception-value">
                                {typeof difference.right === "string"
                                  ? difference.right
                                  : JSON.stringify(difference.right)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p>No captured configuration differences.</p>
                  )}
                </details>
              </section>
              <section
                className="panel perception-comparison-report"
                data-testid="prediction-comparison-report"
              >
                <div className="perception-section-toolbar">
                  <h3>
                    <Columns2 size={17} />
                    Same-source prediction review
                  </h3>
                  <label>
                    Input pair
                    <select
                      aria-label="Comparison input pair"
                      value={row?.key ?? ""}
                      onChange={(e) => setRowKey(e.target.value)}
                    >
                      {data.rows.map((item) => {
                        const input = (item.left ?? item.right)!.input;
                        return (
                          <option key={item.key} value={item.key}>
                            Channel #{input.channel_id} · ordinal #
                            {input.ordinal} · {rowLabels[item.status]}
                          </option>
                        );
                      })}
                    </select>
                  </label>
                </div>
                {row && (
                  <>
                    <div className="perception-comparison-outcome">
                      <span
                        className={`perception-pill ${row.status === "same_input" ? "ready" : "warning"}`}
                      >
                        {rowLabels[row.status]}
                      </span>
                      {row.status === "same_input" ? (
                        <>
                          <span>
                            Detection count Δ{" "}
                            {row.detection_count_delta !== null
                              ? `${row.detection_count_delta > 0 ? "+" : ""}${row.detection_count_delta}`
                              : "unavailable"}
                          </span>
                          <span>
                            Predictions{" "}
                            {row.predictions_identical === true
                              ? "identical"
                              : row.predictions_identical === false
                                ? "differ"
                                : "not compared"}
                          </span>
                        </>
                      ) : (
                        <span>
                          Same-input prediction comparison unavailable for this
                          pair.
                        </span>
                      )}
                    </div>
                    <div className="perception-prediction-pair">
                      {(
                        [
                          ["Left · A", row.left],
                          ["Right · B", row.right],
                        ] as const
                      ).map(([label, frame]) => (
                        <div className="perception-prediction-side" key={label}>
                          <h3>{label}</h3>
                          {frame ? (
                            <InferenceFramePanel
                              frame={frame}
                              label={`${label} saved image and model predictions`}
                            />
                          ) : (
                            <div className="perception-frame-empty">
                              No corresponding input was submitted on this side.
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </section>
              <section className="panel perception-comparison-footer">
                <div className="perception-hash">
                  <Fingerprint size={15} />
                  <span>Comparison SHA-256</span>
                  <code>{data.manifest_hash}</code>
                </div>
                <p>
                  Policy {data.policy_version} · Captured{" "}
                  {formatDate(data.created_at)}. No bounding-box association or
                  ground-truth evaluation is inferred.
                </p>
              </section>
            </div>
          )}
        </>
      )}
      {open && (
        <Modal
          title="Capture prediction comparison"
          subtitle="Preserve both terminal manifests and source-aware prediction differences."
          onClose={() => {
            if (!busy) setOpen(false);
          }}
          className="perception-modal"
        >
          <form className="perception-form" onSubmit={capture}>
            <fieldset disabled={busy}>
              <label>
                Comparison name
                <input
                  aria-label="Prediction comparison name"
                  required
                  maxLength={180}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <div className="perception-form-grid two">
                {(
                  [
                    ["Left inference experiment", left, setLeft],
                    ["Right inference experiment", right, setRight],
                  ] as const
                ).map(([label, value, set]) => (
                  <label key={label}>
                    {label}
                    <select
                      aria-label={label}
                      required
                      value={value}
                      onChange={(e) => set(e.target.value)}
                    >
                      <option value="">Choose a terminal experiment</option>
                      {terminal.map((job) => (
                        <option key={job.id} value={job.id}>
                          {job.label} · {job.model_name} r{job.model_revision} ·{" "}
                          {job.status}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
              <label>
                Comparison note
                <textarea
                  aria-label="Prediction comparison note"
                  required
                  maxLength={4000}
                  rows={3}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              <p className="perception-muted">
                This picker lists up to 100 recent experiments. Completed,
                failed, cancelled, and interrupted experiments can be captured;
                incomplete evidence does not become a successful evaluation.
              </p>
            </fieldset>
            {jobs.error && (
              <p role="alert" className="inline-error">
                Experiment list refresh failed: {jobs.error}
                <button type="button" onClick={jobs.reload}>
                  Retry experiment list
                </button>
              </p>
            )}
            {error && (
              <p role="alert" className="inline-error">
                {error} The same request retains its key when retried.
              </p>
            )}
            <footer>
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                Cancel
              </button>
              <button
                className="button primary"
                disabled={busy || !left || !right || left === right}
              >
                {busy && <LoaderCircle size={15} className="spin" />}
                {busy ? "Capturing comparison…" : "Save prediction comparison"}
              </button>
            </footer>
          </form>
        </Modal>
      )}
    </div>
  );
}
