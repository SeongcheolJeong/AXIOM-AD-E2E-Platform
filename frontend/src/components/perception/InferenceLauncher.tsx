import { useRef, useState, type FormEvent } from "react";
import { ChevronRight, FileSearch, LoaderCircle, Play } from "lucide-react";
import { api } from "../../api";
import { Modal } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import type { SensorChannels } from "../sensors/SensorTypes";
import type {
  RecordingSamples,
  RecordingSummary,
} from "../recordings/RecordingTypes";
import type {
  InferenceDetail,
  InferenceRequest,
  InferenceSource,
  ModelDetail,
  RuntimeMetadata,
} from "./PerceptionTypes";

export default function InferenceLauncher({
  models,
  runtimes,
  source,
  model: initialModel,
  onClose,
  onSubmitted,
}: {
  models: ModelDetail[];
  runtimes: RuntimeMetadata[];
  source?: InferenceSource | null;
  model?: ModelDetail | null;
  onClose: () => void;
  onSubmitted: (job: InferenceDetail) => void;
}) {
  const [label, setLabel] = useState("Recorded image inference");
  const [modelId, setModelId] = useState(
    initialModel?.id ?? models[0]?.id ?? "",
  );
  const [modelRevision, setModelRevision] = useState(
    initialModel?.revision ?? models[0]?.revision ?? 1,
  );
  const [runtimeId, setRuntimeId] = useState(
    runtimes.find((runtime) => runtime.status === "available")?.id ?? "",
  );
  const [recordingId, setRecordingId] = useState(source?.recording_id ?? "");
  const [channelId, setChannelId] = useState(
    source ? String(source.channel_id) : "",
  );
  const [decoder, setDecoder] = useState(
    source ? `${source.decoder_id}\u0000${source.decoder_version}` : "",
  );
  const [ordinals, setOrdinals] = useState(
    source ? String(source.ordinal) : "",
  );
  const [seed, setSeed] = useState(42),
    [score, setScore] = useState(0.35),
    [iou, setIou] = useState(0.45),
    [max, setMax] = useState(100);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [browse, setBrowse] = useState(false),
    [after, setAfter] = useState<number | null>(null);
  const keys = useRef(new Map<string, string>());
  const recordings = useSensorResource<RecordingSummary[]>("/recordings");
  const support = useSensorResource<SensorChannels>(
    recordingId
      ? `/recordings/${encodeURIComponent(recordingId)}/sensor-channels`
      : null,
  );
  const revisions = useSensorResource<ModelDetail[]>(
    modelId
      ? `/perception/models/${encodeURIComponent(modelId)}/revisions`
      : null,
  );
  const model =
    revisions.data?.find(
      (entry) => entry.id === modelId && entry.revision === modelRevision,
    ) ?? null;
  const channel = support.data?.channels.find(
    (entry) => String(entry.channel_id) === channelId,
  );
  const decoderValue =
    decoder ||
    (channel?.decoders[0]
      ? `${channel.decoders[0].id}\u0000${channel.decoders[0].version}`
      : "");
  const samples = useSensorResource<RecordingSamples>(
    browse && recordingId && channelId
      ? `/recordings/${encodeURIComponent(recordingId)}/samples?channel_id=${channelId}&limit=64&${after === null ? "from_start=true" : `after_ordinal=${after}`}`
      : null,
  );
  const parsed = ordinals.trim()
    ? ordinals
        .trim()
        .split(/[\s,]+/)
        .map(Number)
    : [];
  const validOrdinals =
    parsed.length > 0 &&
    parsed.length <= 64 &&
    parsed.every(
      (value) => Number.isSafeInteger(value) && value >= 0 && value <= 100000,
    ) &&
    new Set(parsed).size === parsed.length &&
    /^\d+(?:[\s,]+\d+)*$/.test(ordinals.trim());
  const runtime = runtimes.find((entry) => entry.id === runtimeId);
  const canRun =
    model &&
    runtime?.status === "available" &&
    runtime.profiles.includes(model.profile) &&
    channel?.kind === "camera" &&
    channel.status === "supported" &&
    channel.decoders.some(
      (entry) => `${entry.id}\u0000${entry.version}` === decoderValue,
    );
  function selectOrdinal(ordinal: number, checked: boolean) {
    const selected = validOrdinals ? parsed : [];
    setOrdinals(
      (checked
        ? [...selected.filter((n) => n !== ordinal), ordinal]
        : selected.filter((n) => n !== ordinal)
      ).join(", "),
    );
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!validOrdinals) {
      setError(
        "Choose 1–64 unique file ordinals between 0 and 100000, separated by commas or newlines.",
      );
      return;
    }
    if (!canRun) {
      setError(
        "Select a fixed model revision, an available runtime for its profile, and a supported camera decoder.",
      );
      return;
    }
    const [decoderId, decoderVersion] = decoderValue.split("\u0000");
    const request: InferenceRequest = {
      label,
      model_id: modelId,
      model_revision: modelRevision,
      runtime_id: runtimeId,
      recording_id: recordingId,
      channel_id: Number(channelId),
      ordinals: parsed,
      decoder_id: decoderId,
      decoder_version: decoderVersion,
      seed,
      score_threshold: score,
      iou_threshold: iou,
      max_detections: max,
    };
    const body = JSON.stringify(request);
    let key = keys.current.get(body);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(body, key);
    }
    setBusy(true);
    try {
      onSubmitted(
        await api<InferenceDetail>("/perception/jobs", {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body,
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Inference submission failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Create perception experiment"
      subtitle="Pin original message ordinals and a model revision. The worker performs inference asynchronously."
      onClose={() => {
        if (!busy) onClose();
      }}
      className="perception-modal"
    >
      <form className="perception-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <label>
            Experiment label
            <input
              aria-label="Experiment label"
              required
              maxLength={180}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
          <div className="perception-form-grid two">
            <label>
              Model
              <select
                aria-label="Inference model"
                required
                value={modelId}
                onChange={(e) => {
                  const selected = models.find(
                    (entry) => entry.id === e.target.value,
                  )!;
                  setModelId(selected.id);
                  setModelRevision(selected.revision);
                }}
              >
                {models.map((entry) => (
                  <option value={entry.id} key={entry.id}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Model revision
              <select
                aria-label="Inference model revision"
                required
                value={modelRevision}
                onChange={(e) => setModelRevision(Number(e.target.value))}
              >
                {revisions.data?.map((entry) => (
                  <option value={entry.revision} key={entry.revision}>
                    r{entry.revision} · {entry.profile}
                  </option>
                )) ?? <option value={modelRevision}>r{modelRevision}</option>}
              </select>
            </label>
          </div>
          {revisions.error && (
            <p className="inline-error" role="alert">
              {revisions.error}
              <button type="button" onClick={revisions.reload}>
                Retry model lookup
              </button>
            </p>
          )}
          <label>
            Inference runtime
            <select
              aria-label="Inference runtime"
              required
              value={runtimeId}
              onChange={(e) => setRuntimeId(e.target.value)}
            >
              <option value="">Select an available runtime</option>
              {runtimes.map((entry) => (
                <option
                  key={entry.id}
                  value={entry.id}
                  disabled={
                    entry.status !== "available" ||
                    Boolean(model && !entry.profiles.includes(model.profile))
                  }
                >
                  {entry.name} · {entry.version} ·{" "}
                  {entry.status.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          {model && (
            <p className="perception-scope">
              Pinned r{model.revision} · {model.profile} · {model.input_width} ×{" "}
              {model.input_height}. Registration does not establish model
              compatibility or accuracy.
            </p>
          )}
          <div className="perception-form-grid two">
            <label>
              Recording
              <select
                aria-label="Inference recording"
                required
                value={recordingId}
                onChange={(e) => {
                  setRecordingId(e.target.value);
                  setChannelId("");
                  setDecoder("");
                  setOrdinals("");
                  setAfter(null);
                }}
              >
                {!source && <option value="">Choose a recording</option>}
                {source &&
                  !recordings.data?.some(
                    (entry) => entry.id === source.recording_id,
                  ) && (
                    <option value={source.recording_id}>
                      {source.recording_id}
                    </option>
                  )}
                {recordings.data?.map((entry) => (
                  <option value={entry.id} key={entry.id}>
                    {entry.name} · {entry.source_kind}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Camera channel
              <select
                aria-label="Inference camera channel"
                required
                value={channelId}
                onChange={(e) => {
                  setChannelId(e.target.value);
                  setDecoder("");
                  setOrdinals("");
                  setAfter(null);
                }}
              >
                <option value="">Choose a supported camera channel</option>
                {support.data?.channels
                  .filter(
                    (entry) =>
                      entry.kind === "camera" && entry.status === "supported",
                  )
                  .map((entry) => (
                    <option value={entry.channel_id} key={entry.channel_id}>
                      #{entry.channel_id} · {entry.topic}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          {recordings.error || support.error ? (
            <p className="inline-error" role="alert">
              {recordings.error || support.error}
              <button
                type="button"
                onClick={() => {
                  recordings.reload();
                  support.reload();
                }}
              >
                Retry source lookup
              </button>
            </p>
          ) : null}
          <label>
            Camera decoder
            <select
              aria-label="Inference camera decoder"
              required
              value={decoderValue}
              onChange={(e) => setDecoder(e.target.value)}
            >
              <option value="">Select an exact decoder version</option>
              {channel?.decoders.map((entry) => (
                <option
                  key={`${entry.id}/${entry.version}`}
                  value={`${entry.id}\u0000${entry.version}`}
                >
                  {entry.name} · {entry.id} / {entry.version}
                </option>
              ))}
            </select>
          </label>
          <label>
            Original file ordinals
            <textarea
              aria-label="Inference file ordinals"
              aria-describedby="inference-ordinals-help"
              required
              rows={2}
              value={ordinals}
              onChange={(e) => setOrdinals(e.target.value)}
              placeholder="For example: 0, 3, 6"
            />
            <small id="inference-ordinals-help">
              Explicit file ordinals, not timestamps or channel-local positions.
              Up to 64 unique inputs, in the order supplied. The server verifies
              channel membership.
            </small>
          </label>
          <div className="perception-small-heading">
            <span>
              {validOrdinals ? parsed.length : 0} input
              {parsed.length === 1 ? "" : "s"} selected
            </span>
            <button
              type="button"
              className="sensor-text-button"
              disabled={!channelId}
              onClick={() => setBrowse((v) => !v)}
            >
              <FileSearch size={14} />
              {browse ? "Hide source messages" : "Browse source messages"}
            </button>
          </div>
          {browse && (
            <div className="perception-sample-picker">
              {samples.loading ? (
                <p role="status">Loading actual source ordinals…</p>
              ) : samples.error ? (
                <p role="alert" className="inline-error">
                  {samples.error}
                  <button type="button" onClick={samples.reload}>
                    Retry source messages
                  </button>
                </p>
              ) : (
                <>
                  <div className="table-scroll">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Select</th>
                          <th>File ordinal</th>
                          <th>Original log time (ns)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {samples.data?.samples.map((sample) => (
                          <tr key={sample.ordinal}>
                            <td>
                              <input
                                type="checkbox"
                                aria-label={`Select inference ordinal ${sample.ordinal}`}
                                checked={parsed.includes(sample.ordinal)}
                                disabled={
                                  !parsed.includes(sample.ordinal) &&
                                  parsed.length >= 64
                                }
                                onChange={(e) =>
                                  selectOrdinal(
                                    sample.ordinal,
                                    e.target.checked,
                                  )
                                }
                              />
                            </td>
                            <td>#{sample.ordinal}</td>
                            <td className="mono">{sample.log_time_ns}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="perception-actions">
                    <button
                      type="button"
                      className="button secondary small"
                      disabled={after === null}
                      onClick={() => setAfter(null)}
                    >
                      First window
                    </button>
                    <button
                      type="button"
                      className="button secondary small"
                      disabled={samples.data?.next_after_ordinal == null}
                      onClick={() =>
                        setAfter(samples.data?.next_after_ordinal ?? null)
                      }
                    >
                      Next window
                      <ChevronRight size={14} />
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
          <div className="perception-form-grid four">
            {(
              [
                ["Score threshold", score, setScore, 0, 1, 0.01],
                ["NMS IoU threshold", iou, setIou, 0, 1, 0.01],
                ["Maximum detections", max, setMax, 1, 300, 1],
                ["Inference seed", seed, setSeed, 0, 2147483647, 1],
              ] as const
            ).map(([name, value, set, min, maxValue, step]) => (
              <label key={name}>
                {name}
                <input
                  aria-label={name}
                  type="number"
                  required
                  min={min}
                  max={maxValue}
                  step={step}
                  value={value}
                  onChange={(e) => set(Number(e.target.value))}
                />
              </label>
            ))}
          </div>
          <p className="perception-muted">
            Score filtering and class-aware NMS become part of this experiment.
            Scores are model outputs, not calibrated probabilities. This does
            not evaluate ground truth.
          </p>
        </fieldset>
        {error && (
          <div className="inline-error" role="alert">
            {error}
            <p>
              The same inputs retain the same submission key when retried. A
              lost response does not mean the request was rejected.
            </p>
          </div>
        )}
        <footer>
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="button primary"
            disabled={busy || !canRun || !validOrdinals}
          >
            {busy ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Play size={14} />
            )}
            {busy
              ? "Submitting inference…"
              : error
                ? "Retry inference submission"
                : "Submit inference"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
