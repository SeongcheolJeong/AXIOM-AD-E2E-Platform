import { useState, type FormEvent } from "react";
import { Link2, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { api } from "../../api";
import { Modal } from "../ui";
import { MAX_TIMESTAMP_NS } from "../recordings/RecordingTime";
import { useSensorResource } from "./SensorData";
import type {
  BindingCreate,
  BindingDetail,
  ChannelBinding,
  RigDetail,
  SensorChannelSupport,
} from "./SensorTypes";

interface BindingDraft {
  localId: string;
  channelId: string;
  sensorKey: string;
  decoder: string;
  age: string;
  offset: string;
}
const decoderKey = (id: string, version: string) => `${id}\u0000${version}`;
function fromBinding(binding: ChannelBinding): BindingDraft {
  return {
    localId: crypto.randomUUID(),
    channelId: String(binding.channel_id),
    sensorKey: binding.sensor_key,
    decoder: decoderKey(binding.decoder_id, binding.decoder_version),
    age: binding.max_age_ns ?? "",
    offset: binding.log_time_offset_ns,
  };
}
function newBinding(
  channel?: SensorChannelSupport,
  rig?: RigDetail,
  index = 0,
): BindingDraft {
  const decoder = channel?.decoders[0];
  return {
    localId: crypto.randomUUID(),
    channelId: channel ? String(channel.channel_id) : "",
    sensorKey:
      rig?.sensors.filter((sensor) => sensor.kind === channel?.kind)[index]
        ?.key ??
      rig?.sensors.find((sensor) => sensor.kind === channel?.kind)?.key ??
      "",
    decoder: decoder ? decoderKey(decoder.id, decoder.version) : "",
    age: "",
    offset: "0",
  };
}
export default function SensorBindingEditor({
  recordingId,
  binding,
  rigs,
  channels,
  initialRigId,
  selectedChannelIds,
  onClose,
  onSaved,
}: {
  recordingId: string;
  binding: BindingDetail | null;
  rigs: RigDetail[];
  channels: SensorChannelSupport[];
  initialRigId?: string;
  selectedChannelIds: number[];
  onClose: () => void;
  onSaved: (binding: BindingDetail) => void;
}) {
  const initialRig =
    rigs.find((rig) => rig.id === (initialRigId ?? binding?.rig_id)) ?? rigs[0];
  const [rigId, setRigId] = useState(initialRig?.id ?? "");
  const [rigRevision, setRigRevision] = useState(
    binding?.rig_id === initialRig?.id && !initialRigId
      ? binding.rig_revision
      : (initialRig?.revision ?? 1),
  );
  const [guard] = useState(binding?.revision ?? 0);
  const [note, setNote] = useState("");
  const [rows, setRows] = useState<BindingDraft[]>(
    binding?.channels.map(fromBinding) ??
      channels
        .filter(
          (channel) =>
            selectedChannelIds.includes(channel.channel_id) &&
            channel.status === "supported",
        )
        .map((channel, index, all) =>
          newBinding(
            channel,
            initialRig,
            all.slice(0, index).filter((other) => other.kind === channel.kind)
              .length,
          ),
        ),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const revisions = useSensorResource<RigDetail[]>(
    rigId ? `/sensor-rigs/${encodeURIComponent(rigId)}/revisions` : null,
  );
  const head = rigs.find((rig) => rig.id === rigId);
  const selectedRig =
    revisions.data?.find(
      (rig) => rig.revision === rigRevision && rig.id === rigId,
    ) ?? (head?.revision === rigRevision ? head : undefined);
  const revisionOptions = revisions.data ?? (head ? [head] : []);
  const update = (localId: string, patch: Partial<BindingDraft>) =>
    setRows((items) =>
      items.map((row) =>
        row.localId === localId ? { ...row, ...patch } : row,
      ),
    );
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (!selectedRig)
        throw new Error("Load the selected immutable rig revision first.");
      const mapped = rows.map((row): ChannelBinding => {
        const [decoderId, decoderVersion] = row.decoder.split("\u0000");
        if (!decoderId || !decoderVersion || !row.sensorKey || !row.channelId)
          throw new Error(
            "Every binding needs a channel, rig sensor and exact decoder version.",
          );
        const age = row.age.trim();
        const offset = row.offset.trim();
        if (age && (!/^\d+$/.test(age) || BigInt(age) > MAX_TIMESTAMP_NS))
          throw new Error(
            "Maximum sample age must be an unsigned 64-bit integer nanosecond value, or blank for unknown.",
          );
        if (
          !/^-?\d+$/.test(offset) ||
          BigInt(offset) > MAX_TIMESTAMP_NS ||
          BigInt(offset) < -MAX_TIMESTAMP_NS
        )
          throw new Error(
            "Log-time offset must be integer nanoseconds within ±uint64.",
          );
        return {
          channel_id: Number(row.channelId),
          sensor_key: row.sensorKey,
          decoder_id: decoderId,
          decoder_version: decoderVersion,
          max_age_ns: age ? BigInt(age).toString() : null,
          log_time_offset_ns: BigInt(offset).toString(),
        };
      });
      if (!mapped.length) throw new Error("Add at least one channel binding.");
      if (
        new Set(mapped.map((row) => row.channel_id)).size !== mapped.length ||
        new Set(mapped.map((row) => row.sensor_key)).size !== mapped.length
      )
        throw new Error(
          "Each channel and rig sensor may appear only once in a binding revision.",
        );
      const body: BindingCreate = {
        expected_revision: guard,
        rig_id: rigId,
        rig_revision: rigRevision,
        note: note.trim(),
        channels: mapped,
      };
      onSaved(
        await api<BindingDetail>(
          `/recordings/${encodeURIComponent(recordingId)}/sensor-bindings`,
          { method: "POST", body: JSON.stringify(body) },
        ),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The recording binding could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Bind recording to sensor rig"
      subtitle="Pin an immutable rig revision and explicit channel, decoder and clock declarations."
      onClose={() => {
        if (!busy) onClose();
      }}
      className="sensor-modal"
    >
      <form className="sensor-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <div className="sensor-form-grid two">
            <label>
              Binding rig
              <select
                autoFocus
                value={rigId}
                required
                onChange={(event) => {
                  const rig = rigs.find(
                    (entry) => entry.id === event.target.value,
                  );
                  setRigId(event.target.value);
                  setRigRevision(rig?.revision ?? 1);
                  setRows((items) =>
                    items.map((row) => ({ ...row, sensorKey: "" })),
                  );
                }}
              >
                <option value="">Select a rig</option>
                {rigs.map((rig) => (
                  <option key={rig.id} value={rig.id}>
                    {rig.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Binding rig revision
              <select
                value={rigRevision}
                onChange={(event) => setRigRevision(Number(event.target.value))}
              >
                {revisionOptions.map((rig) => (
                  <option key={rig.revision} value={rig.revision}>
                    Revision {rig.revision} · {rig.reference_frame}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {revisions.error && (
            <p className="inline-error" role="alert">
              {revisions.error}
            </p>
          )}
          {selectedRig && (
            <p className="sensor-form-help">
              {selectedRig.coordinate_convention.replaceAll("_", " ")} ·{" "}
              {selectedRig.calibration_status.replaceAll("_", " ")} ·{" "}
              {selectedRig.source || "No source label"}
            </p>
          )}
          <label>
            Binding note
            <textarea
              required
              maxLength={4000}
              rows={2}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Describe channel mapping, age policy and any clock offsets."
            />
          </label>
          <div className="sensor-form-section-title">
            <h3>Channel assignments</h3>
            <span>{rows.length} / 32</span>
            <button
              type="button"
              className="button secondary small"
              disabled={rows.length >= 32}
              onClick={() =>
                setRows((items) => [
                  ...items,
                  newBinding(
                    channels.find(
                      (channel) =>
                        channel.status === "supported" &&
                        !items.some(
                          (row) => Number(row.channelId) === channel.channel_id,
                        ),
                    ),
                    selectedRig,
                  ),
                ])
              }
            >
              <Plus size={14} /> Add channel binding
            </button>
          </div>
          {rows.map((row, index) => {
            const channel = channels.find(
              (entry) => String(entry.channel_id) === row.channelId,
            );
            const availableDecoders = channel?.decoders ?? [];
            return (
              <article
                className="sensor-rig-row"
                key={row.localId}
                aria-label={`Channel binding ${index + 1}`}
              >
                <header>
                  <strong>Channel binding {index + 1}</strong>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Remove channel binding ${index + 1}`}
                    onClick={() =>
                      setRows((items) =>
                        items.filter((entry) => entry.localId !== row.localId),
                      )
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                </header>
                <div className="sensor-form-grid two">
                  <label>
                    Recording channel
                    <select
                      required
                      value={row.channelId}
                      onChange={(event) => {
                        const channel = channels.find(
                          (entry) =>
                            String(entry.channel_id) === event.target.value,
                        );
                        const decoder = channel?.decoders[0];
                        update(row.localId, {
                          channelId: event.target.value,
                          decoder: decoder
                            ? decoderKey(decoder.id, decoder.version)
                            : "",
                          sensorKey: "",
                        });
                      }}
                    >
                      <option value="">Select a channel</option>
                      {channels.map((channel) => (
                        <option
                          key={channel.channel_id}
                          value={channel.channel_id}
                          disabled={channel.status !== "supported"}
                        >
                          #{channel.channel_id} · {channel.topic}{" "}
                          {channel.status !== "supported"
                            ? "· unsupported"
                            : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Rig sensor
                    <select
                      required
                      value={row.sensorKey}
                      onChange={(event) =>
                        update(row.localId, { sensorKey: event.target.value })
                      }
                    >
                      <option value="">Select a sensor</option>
                      {selectedRig?.sensors.map((sensor) => (
                        <option
                          key={sensor.key}
                          value={sensor.key}
                          disabled={channel?.kind !== sensor.kind}
                        >
                          {sensor.key} · {sensor.frame_id} ({sensor.kind})
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label>
                  Decoder
                  <select
                    required
                    value={row.decoder}
                    onChange={(event) =>
                      update(row.localId, { decoder: event.target.value })
                    }
                  >
                    <option value="">Select a decoder version</option>
                    {row.decoder &&
                      !availableDecoders.some(
                        (decoder) =>
                          decoderKey(decoder.id, decoder.version) ===
                          row.decoder,
                      ) && (
                        <option value={row.decoder}>
                          Pinned decoder unavailable ·{" "}
                          {row.decoder.replace("\u0000", " / ")}
                        </option>
                      )}
                    {availableDecoders.map((decoder) => (
                      <option
                        key={decoderKey(decoder.id, decoder.version)}
                        value={decoderKey(decoder.id, decoder.version)}
                      >
                        {decoder.name} · {decoder.id} / {decoder.version}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="sensor-form-grid two">
                  <label>
                    Maximum sample age (ns)
                    <input
                      aria-label="Maximum sample age (ns)"
                      aria-describedby={`sensor-age-help-${row.localId}`}
                      value={row.age}
                      inputMode="numeric"
                      onChange={(event) =>
                        update(row.localId, { age: event.target.value })
                      }
                      placeholder="Unknown — no threshold"
                    />
                    <small id={`sensor-age-help-${row.localId}`}>
                      Blank means freshness is unknown. Zero requires an exact
                      timestamp.
                    </small>
                  </label>
                  <label>
                    Log-time offset (ns)
                    <input
                      aria-label="Log-time offset (ns)"
                      aria-describedby={`sensor-offset-help-${row.localId}`}
                      required
                      value={row.offset}
                      inputMode="text"
                      onChange={(event) =>
                        update(row.localId, { offset: event.target.value })
                      }
                    />
                    <small id={`sensor-offset-help-${row.localId}`}>
                      Aligned time = original log time + this declared offset.
                    </small>
                  </label>
                </div>
              </article>
            );
          })}
        </fieldset>
        <p className="sensor-form-help">
          Publication pins rig r{rigRevision} and decoder versions. It does not
          verify physical clock synchronization. Current binding revision guard:{" "}
          {guard}.
        </p>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <footer className="sensor-form-footer">
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={busy || !selectedRig || !rows.length}
          >
            {busy ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Link2 size={15} />
            )}
            Save recording binding
          </button>
        </footer>
      </form>
    </Modal>
  );
}
