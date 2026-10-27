import { useState } from "react";
import { ChevronLeft, ChevronRight, ListOrdered } from "lucide-react";
import { useSensorResource } from "../sensors/SensorData";
import type { BindingDetail } from "../sensors/SensorTypes";
import type {
  RecordingSummary,
  RecordingSamples,
} from "../recordings/RecordingTypes";
import type { StackReplayCreate, StackReplaySources } from "./StackReplayTypes";

export type ReplaySourceSelection = Omit<
  StackReplayCreate,
  "label" | "stack_id" | "stack_revision"
>;
export const emptyReplaySource: ReplaySourceSelection = {
  recording_id: "",
  binding_revision: 0,
  lidar_channel_id: -1,
  ego_channel_id: -1,
  ego_decoder_id: "axiom-ego-json",
  ego_decoder_version: "1",
  ego_log_time_offset_ns: "0",
  ego_ordinals: [],
};
export function parseEgoOrdinals(text: string): number[] | null {
  if (!/^\d+(?:[\s,]+\d+)*$/.test(text.trim())) return null;
  const values = text
    .trim()
    .split(/[\s,]+/)
    .map(Number);
  return values.length <= 128 &&
    new Set(values).size === values.length &&
    values.every(
      (value) => Number.isSafeInteger(value) && value >= 0 && value <= 99999,
    )
    ? values
    : null;
}
export default function ReplaySourceFields({
  value,
  ordinalText,
  onChange,
  onOrdinals,
}: {
  value: ReplaySourceSelection;
  ordinalText: string;
  onChange: (value: ReplaySourceSelection) => void;
  onOrdinals: (text: string) => void;
}) {
  const [browse, setBrowse] = useState(false),
    [after, setAfter] = useState<number | null>(null);
  const recordings = useSensorResource<RecordingSummary[]>("/recordings");
  const sources = useSensorResource<StackReplaySources>(
    value.recording_id
      ? `/stack-replay-sources/${encodeURIComponent(value.recording_id)}`
      : null,
  );
  const bindings = useSensorResource<BindingDetail[]>(
    value.recording_id
      ? `/recordings/${encodeURIComponent(value.recording_id)}/sensor-bindings/revisions`
      : null,
  );
  const binding = bindings.data?.find(
    (item) => item.revision === value.binding_revision,
  );
  const choices = sources.data?.channels ?? [];
  const lidars = choices.filter((item) => item.role === "lidar");
  const egos = choices.filter((item) => item.role === "ego");
  const mapping = binding?.channels.find(
    (item) => item.channel_id === value.lidar_channel_id,
  );
  const sensor = binding?.rig_snapshot.sensors.find(
    (item) => item.key === mapping?.sensor_key,
  );
  const chosenRecording = recordings.data?.find(
    (item) => item.id === value.recording_id,
  );
  const samples = useSensorResource<RecordingSamples>(
    browse && value.recording_id && value.ego_channel_id >= 0
      ? `/recordings/${encodeURIComponent(value.recording_id)}/samples?channel_id=${value.ego_channel_id}&limit=64&${after === null ? "from_start=true" : `after_ordinal=${after}`}`
      : null,
  );
  const selected = parseEgoOrdinals(ordinalText) ?? [];
  function update(patch: Partial<ReplaySourceSelection>) {
    onChange({ ...value, ...patch });
  }
  function toggle(ordinal: number, checked: boolean) {
    onOrdinals(
      (checked
        ? [...selected.filter((item) => item !== ordinal), ordinal]
        : selected.filter((item) => item !== ordinal)
      ).join(", "),
    );
  }
  return (
    <div className="stack-source-fields">
      <label>
        Source recording
        <select
          aria-label="Stack replay recording"
          required
          value={value.recording_id}
          onChange={(e) => {
            onChange({ ...emptyReplaySource, recording_id: e.target.value });
            onOrdinals("");
            setAfter(null);
          }}
        >
          <option value="">Choose a recording</option>
          {recordings.data?.map((item) => (
            <option value={item.id} key={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      {chosenRecording && (
        <p className="stack-subtle">
          Source declaration: {chosenRecording.source_kind} ·{" "}
          {chosenRecording.source}. Choosing a recording does not establish its
          measurement fidelity.
        </p>
      )}
      <div className="stack-form-grid two">
        <label>
          Pinned sensor binding
          <select
            aria-label="Stack replay binding revision"
            required
            value={value.binding_revision || ""}
            onChange={(e) =>
              update({
                binding_revision: Number(e.target.value),
                lidar_channel_id: -1,
              })
            }
          >
            <option value="">Choose an immutable binding</option>
            {bindings.data?.map((item) => (
              <option key={item.revision} value={item.revision}>
                r{item.revision} · {item.rig_snapshot.name} r{item.rig_revision}{" "}
                · {item.rig_snapshot.reference_frame}
              </option>
            ))}
          </select>
        </label>
        <label>
          LiDAR input
          <select
            aria-label="Stack replay LiDAR channel"
            required
            value={value.lidar_channel_id >= 0 ? value.lidar_channel_id : ""}
            onChange={(e) =>
              update({ lidar_channel_id: Number(e.target.value) })
            }
          >
            <option value="">Choose a bound LiDAR channel</option>
            {lidars.map((item) => {
              const bound = binding?.channels.some(
                (channel) =>
                  channel.channel_id === item.channel_id &&
                  channel.decoder_id === item.decoder_id &&
                  channel.decoder_version === item.decoder_version,
              );
              return (
                <option
                  key={`${item.channel_id}/${item.decoder_id}/${item.decoder_version}`}
                  value={item.channel_id}
                  disabled={!bound}
                >
                  #{item.channel_id} · {item.topic} · {item.decoder_id}/
                  {item.decoder_version}
                  {bound ? "" : " · not in this binding"}
                </option>
              );
            })}
          </select>
        </label>
      </div>
      {binding && (
        <div className="stack-source-declaration">
          <span>
            Rig r{binding.rig_revision} · {binding.rig_snapshot.reference_frame}{" "}
            · {binding.rig_snapshot.coordinate_convention}
          </span>
          <span>
            Source: {binding.rig_snapshot.source_kind} ·{" "}
            {binding.rig_snapshot.source}
          </span>
          {mapping && (
            <span>
              LiDAR age limit:{" "}
              {mapping.max_age_ns === null
                ? "unknown"
                : `${mapping.max_age_ns} ns`}{" "}
              · log offset {mapping.log_time_offset_ns} ns · mount{" "}
              {sensor?.transform ? "source-declared transform" : "unknown"}
            </span>
          )}
          <small>
            The reference frame is user-declared ego coordinates: x forward, y
            left, z up. Matching frame names, right-handed z-up convention, and
            a declared transform do not independently certify axis directions or
            calibration. Missing or stale input remains explicit.
          </small>
        </div>
      )}
      {value.recording_id &&
        !bindings.loading &&
        bindings.data?.length === 0 && (
          <p className="stack-notice">
            This recording has no sensor binding. Create a rig and binding in
            Data catalog → Recordings → Sensor replay, then refresh this lookup.
            <button type="button" onClick={bindings.reload}>
              Refresh bindings
            </button>
          </p>
        )}
      <div className="stack-form-grid two">
        <label>
          Ego telemetry input
          <select
            aria-label="Stack replay ego channel"
            required
            value={value.ego_channel_id >= 0 ? value.ego_channel_id : ""}
            onChange={(e) => {
              update({ ego_channel_id: Number(e.target.value) });
              onOrdinals("");
              setAfter(null);
            }}
          >
            <option value="">Choose an exact supported ego schema</option>
            {egos.map((item) => (
              <option key={item.channel_id} value={item.channel_id}>
                #{item.channel_id} · {item.topic} · {item.schema_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Ego log-time offset (ns)
          <input
            aria-label="Stack replay ego offset"
            required
            inputMode="text"
            pattern="0|-?[1-9][0-9]{0,19}"
            value={value.ego_log_time_offset_ns}
            onChange={(e) => update({ ego_log_time_offset_ns: e.target.value })}
          />
        </label>
      </div>
      {value.recording_id &&
        !sources.loading &&
        sources.data &&
        !egos.length && (
          <p className="stack-notice">
            No supported axiom.EgoState.v1 telemetry channel was found. Topic
            names or generic JSON are insufficient to infer ego state.
          </p>
        )}
      <label>
        Exact ego message ordinals
        <textarea
          aria-label="Stack replay ego ordinals"
          required
          rows={3}
          value={ordinalText}
          onChange={(e) => onOrdinals(e.target.value)}
          placeholder="e.g. 1, 3, 5"
        />
      </label>
      <p className="stack-subtle">
        Choose 1–128 unique original file ordinals in strictly increasing
        aligned time order. Each ego sample anchors a tick; the server pins the
        latest LiDAR sample at or before that tick. Ordinals identify file
        messages, not channel-local indexes.
      </p>
      <button
        type="button"
        className="button secondary small"
        disabled={value.ego_channel_id < 0}
        onClick={() => setBrowse((current) => !current)}
      >
        <ListOrdered size={14} />
        {browse ? "Hide ego message browser" : "Browse ego messages"}
      </button>
      {browse && (
        <div className="stack-message-browser">
          {samples.error ? (
            <p role="alert">
              {samples.error}
              <button type="button" onClick={samples.reload}>
                Retry ego messages
              </button>
            </p>
          ) : samples.loading ? (
            <p role="status">Loading exact ego messages…</p>
          ) : (
            <>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Select</th>
                      <th>File ordinal</th>
                      <th>Log time (ns)</th>
                      <th>Payload</th>
                    </tr>
                  </thead>
                  <tbody>
                    {samples.data?.samples.map((sample) => (
                      <tr key={sample.ordinal}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Select ego ordinal ${sample.ordinal}`}
                            checked={selected.includes(sample.ordinal)}
                            disabled={
                              !selected.includes(sample.ordinal) &&
                              selected.length >= 128
                            }
                            onChange={(e) =>
                              toggle(sample.ordinal, e.target.checked)
                            }
                          />
                        </td>
                        <td>#{sample.ordinal}</td>
                        <td>
                          <code>{sample.log_time_ns}</code>
                        </td>
                        <td>{sample.decode_status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="stack-actions">
                <button
                  type="button"
                  className="button secondary small"
                  disabled={after === null}
                  onClick={() => setAfter(null)}
                >
                  <ChevronLeft size={12} />
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
                  <ChevronRight size={12} />
                </button>
              </div>
            </>
          )}
        </div>
      )}
      {(recordings.error || sources.error || bindings.error) && (
        <p className="inline-error" role="alert">
          {recordings.error || sources.error || bindings.error}
          <button
            type="button"
            onClick={() => {
              recordings.reload();
              sources.reload();
              bindings.reload();
            }}
          >
            Retry replay source lookup
          </button>
        </p>
      )}
      {sources.data?.warnings.length ? (
        <details className="stack-disclosure">
          <summary>Source support & interpretation</summary>
          {sources.data.warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
        </details>
      ) : null}
    </div>
  );
}
