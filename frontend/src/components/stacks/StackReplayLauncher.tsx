import { useRef, useState, type FormEvent } from "react";
import { LoaderCircle, Play } from "lucide-react";
import { api } from "../../api";
import { Modal } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import { stackAvailable } from "./StackKernels";
import type { StackComponentMetadata } from "./StackTypes";
import type { StackDetail } from "./StackTypes";
import type { StackReplayCreate, StackReplayDetail } from "./StackReplayTypes";
import ReplaySourceFields, {
  emptyReplaySource,
  parseEgoOrdinals,
  type ReplaySourceSelection,
} from "./ReplaySourceFields";

export default function StackReplayLauncher({
  stacks,
  components,
  initialStack,
  onClose,
  onSubmitted,
}: {
  stacks: StackDetail[];
  components: StackComponentMetadata[];
  initialStack?: StackDetail | null;
  onClose: () => void;
  onSubmitted: (job: StackReplayDetail) => void;
}) {
  const [label, setLabel] = useState("Recorded stack replay");
  const [id, setId] = useState(initialStack?.id ?? stacks[0]?.id ?? ""),
    [revision, setRevision] = useState(
      initialStack?.revision ?? stacks[0]?.revision ?? 1,
    );
  const [source, setSource] =
      useState<ReplaySourceSelection>(emptyReplaySource),
    [ordinals, setOrdinals] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const keys = useRef(new Map<string, string>());
  const history = useSensorResource<StackDetail[]>(
    id ? `/stacks/${encodeURIComponent(id)}/revisions` : null,
  );
  const stack = history.data?.find(
    (item) => item.id === id && item.revision === revision,
  );
  const executable = !!stack && stackAvailable(stack, components);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    const parsed = parseEgoOrdinals(ordinals);
    if (
      !executable ||
      !source.recording_id ||
      source.binding_revision < 1 ||
      source.lidar_channel_id < 0 ||
      source.ego_channel_id < 0 ||
      !parsed?.length
    ) {
      setError(
        "Select a pinned stack and binding revision, supported LiDAR/ego channels, and 1–128 unique ego ordinals from 0 to 99999.",
      );
      return;
    }
    if (
      !/^(0|-?[1-9][0-9]{0,19})$/.test(source.ego_log_time_offset_ns) ||
      BigInt(source.ego_log_time_offset_ns) < -18446744073709551615n ||
      BigInt(source.ego_log_time_offset_ns) > 18446744073709551615n
    ) {
      setError(
        "Ego offset must be a canonical signed decimal nanosecond string within the uint64 magnitude range.",
      );
      return;
    }
    const request: StackReplayCreate = {
      label,
      stack_id: id,
      stack_revision: revision,
      ...source,
      ego_ordinals: parsed,
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
        await api<StackReplayDetail>("/stack-replays", {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body,
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Recorded stack replay submission failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Replay stack on recorded inputs"
      subtitle="Pin original ego ticks and LiDAR source selection. Commands are stored, never applied to this recording."
      onClose={() => {
        if (!busy) onClose();
      }}
      className="stack-launch-modal"
    >
      <form className="stack-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <div className="stack-mode-notice">
            <strong>RECORDED OPEN-LOOP</strong>
            <span>
              Control commands do not change the recorded world or subsequent
              sensor observations.
            </span>
          </div>
          <label>
            Replay label
            <input
              aria-label="Stack replay label"
              required
              maxLength={180}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
          <div className="stack-form-grid two">
            <label>
              Component stack
              <select
                aria-label="Stack replay definition"
                required
                value={id}
                onChange={(e) => {
                  const selected = stacks.find(
                    (item) => item.id === e.target.value,
                  )!;
                  setId(selected.id);
                  setRevision(selected.revision);
                }}
              >
                {stacks.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Immutable stack revision
              <select
                aria-label="Stack replay revision"
                value={revision}
                onChange={(e) => setRevision(Number(e.target.value))}
              >
                {history.data?.map((item) => (
                  <option key={item.revision} value={item.revision}>
                    r{item.revision} · {item.kernel_version}
                  </option>
                )) ?? <option value={revision}>r{revision}</option>}
              </select>
            </label>
          </div>
          {history.error && (
            <p className="inline-error" role="alert">
              {history.error}
              <button type="button" onClick={history.reload}>
                Retry stack revisions
              </button>
            </p>
          )}
          {stack && (
            <p className="stack-subtle">
              Pinned r{stack.revision} · reference frame{" "}
              {stack.definition.reference_frame} · {stack.manifest_hash}
            </p>
          )}
          {stack && !executable && (
            <p className="inline-error" role="alert">
              The exact saved kernel and component versions are unavailable.
              Choose a compatible revision; no version is substituted.
            </p>
          )}
          <ReplaySourceFields
            value={source}
            ordinalText={ordinals}
            onChange={setSource}
            onOrdinals={setOrdinals}
          />
        </fieldset>
        {error && (
          <p className="inline-error" role="alert">
            {error} An identical retry preserves the submission key.
          </p>
        )}
        <footer className="stack-form-footer">
          <p>
            These are diagnostic outputs on recorded observations. No driving
            outcome, free-space certification, or CARLA shared-stack validation
            is inferred.
          </p>
          <div className="stack-actions">
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </button>
            <button className="button primary" disabled={busy || !executable}>
              {busy ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <Play size={14} />
              )}{" "}
              {busy
                ? "Submitting replay…"
                : error
                  ? "Retry stack replay submission"
                  : "Submit stack replay"}
            </button>
          </div>
        </footer>
      </form>
    </Modal>
  );
}
