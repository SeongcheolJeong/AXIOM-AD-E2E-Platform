import { useEffect, useState, type FormEvent } from "react";
import { Search } from "lucide-react";
import { Modal } from "../ui";
import { DownloadLink } from "../ArtifactAccess";
import { useSensorResource } from "./SensorData";
import { SensorDecodedEvidence, SensorMessageEvidence } from "./SensorEvidence";
import SensorPreview from "./SensorPreview";
import type { SensorChannelSupport, SensorFrame } from "./SensorTypes";
import type { RecordingSamples } from "../recordings/RecordingTypes";
import type { InferenceSource } from "../perception/PerceptionTypes";

export default function SensorFrameInspector({
  recordingId,
  channels,
  channelId,
  ordinal,
  onClose,
  onOpenRaw,
  onInfer,
}: {
  recordingId: string;
  channels: SensorChannelSupport[];
  channelId: number;
  ordinal: number | null;
  onClose: () => void;
  onOpenRaw: (channelId: number, time: string) => void;
  onInfer?: (source: InferenceSource) => void;
}) {
  const [channel, setChannel] = useState(String(channelId));
  const [ordinalInput, setOrdinalInput] = useState(
    ordinal === null ? "" : String(ordinal),
  );
  const [selection, setSelection] = useState({ channelId, ordinal });
  const [inputError, setInputError] = useState("");
  const first = useSensorResource<RecordingSamples>(
    selection.ordinal === null
      ? `/recordings/${encodeURIComponent(recordingId)}/samples?channel_id=${selection.channelId}&from_start=true&limit=1`
      : null,
  );
  useEffect(() => {
    if (
      selection.ordinal === null &&
      first.data?.channel_id === selection.channelId &&
      first.data.samples[0]
    ) {
      const ordinal = first.data.samples[0].ordinal;
      setSelection({ channelId: selection.channelId, ordinal });
      setOrdinalInput(String(ordinal));
    }
  }, [first.data, selection.channelId, selection.ordinal]);
  const result = useSensorResource<SensorFrame>(
    selection.ordinal !== null
      ? `/recordings/${encodeURIComponent(recordingId)}/sensor-frame?channel_id=${selection.channelId}&ordinal=${selection.ordinal}&max_points=20000`
      : null,
  );
  function inspect(event: FormEvent) {
    event.preventDefault();
    if (
      !/^\d+$/.test(ordinalInput) ||
      !Number.isSafeInteger(Number(ordinalInput))
    ) {
      setInputError("Enter an exact nonnegative file ordinal.");
      return;
    }
    setSelection({ channelId: Number(channel), ordinal: Number(ordinalInput) });
    setInputError("");
  }
  const data =
    result.data?.recording_id === recordingId &&
    result.data.channel_id === selection.channelId &&
    result.data.message.ordinal === selection.ordinal
      ? result.data
      : null;
  return (
    <Modal
      title="Inspect original sensor sample"
      subtitle="Exact file ordinal inspection is independent of the shared cursor and its freshness policy."
      onClose={onClose}
      className="sensor-modal sensor-inspector-modal"
    >
      <div className="sensor-form">
        <form className="sensor-inspect-query" onSubmit={inspect}>
          <label>
            Original sample channel
            <select
              value={channel}
              onChange={(event) => setChannel(event.target.value)}
            >
              {channels.map((entry) => (
                <option key={entry.channel_id} value={entry.channel_id}>
                  #{entry.channel_id} · {entry.topic}
                </option>
              ))}
            </select>
          </label>
          <label>
            Original sample ordinal
            <input
              inputMode="numeric"
              required
              value={ordinalInput}
              onChange={(event) => setOrdinalInput(event.target.value)}
            />
          </label>
          <button type="submit" className="button secondary">
            <Search size={14} />
            Inspect ordinal
          </button>
        </form>
        {inputError && (
          <p className="inline-error" role="alert">
            {inputError}
          </p>
        )}
        {first.loading && (
          <p role="status">Finding the channel's first actual ordinal…</p>
        )}
        {first.error && (
          <p role="alert" className="inline-error">
            {first.error}
            <button
              type="button"
              className="sensor-text-button"
              onClick={first.reload}
            >
              Retry first sample lookup
            </button>
          </p>
        )}
        {first.data && !first.data.samples.length && (
          <p>No original messages exist in this channel.</p>
        )}
        {result.loading ? (
          <p role="status">Loading exact original sample…</p>
        ) : result.error ? (
          <div role="alert">
            <p className="inline-error">{result.error}</p>
            <button
              type="button"
              className="button secondary"
              onClick={result.reload}
            >
              Retry original sample
            </button>
          </div>
        ) : (
          data && (
            <>
              <div className="sensor-inspector-status">
                <strong>
                  Ordinal {data.message.ordinal} ·{" "}
                  {data.status.replaceAll("_", " ")}
                </strong>
                <span>Explicit inspection · not a current-frame assertion</span>
              </div>
              {data.status === "decoded" && data.decoded ? (
                <>
                  <SensorPreview
                    decoded={data.decoded}
                    transform={null}
                    frame={data.decoded.frame_id ?? ""}
                    convention={
                      data.decoded.coordinate_convention ===
                      "ros_right_handed_z_up"
                        ? "right_handed_z_up"
                        : "unknown"
                    }
                  />
                  <SensorDecodedEvidence decoded={data.decoded} />
                  {onInfer &&
                    data.decoded.kind === "camera" &&
                    data.decoded.camera && (
                      <button
                        type="button"
                        className="button primary"
                        onClick={() => {
                          onClose();
                          onInfer({
                            recording_id: data.recording_id,
                            channel_id: data.channel_id,
                            ordinal: data.message.ordinal,
                            decoder_id: data.decoded!.decoder_id,
                            decoder_version: data.decoded!.decoder_version,
                          });
                        }}
                      >
                        Run inference on this image
                      </button>
                    )}
                </>
              ) : (
                <p className="sensor-warning">
                  {data.reason ??
                    "This sample has no supported decoded sensor preview."}
                </p>
              )}
              <SensorMessageEvidence
                message={data.message}
                decoded={data.decoded}
              />
              <DownloadLink
                href={`/api/v1/recordings/${encodeURIComponent(recordingId)}/messages/${data.message.ordinal}/payload`}
                download
                className="sensor-text-button"
              >
                Download original sample payload
              </DownloadLink>
              {data.message.preview_hex !== null && (
                <details open>
                  <summary>Original payload · bounded hex prefix</summary>
                  <pre aria-label="Original sensor payload hex">
                    <code>
                      {data.message.preview_hex.match(/.{1,2}/g)?.join(" ") ||
                        "Empty payload"}
                    </code>
                  </pre>
                </details>
              )}
              {data.warnings.map((warning, index) => (
                <p key={index} className="sensor-warning">
                  {warning}
                </p>
              ))}
              <button
                type="button"
                className="button secondary"
                onClick={() => {
                  onOpenRaw(data.channel_id, data.message.log_time_ns);
                  onClose();
                }}
              >
                Open raw channel at sample time
              </button>
              <p className="sensor-form-help">
                The raw time query selects the final ordinal at an equal
                timestamp. This inspector preserves the exact ordinal shown
                above.
              </p>
            </>
          )
        )}
      </div>
    </Modal>
  );
}
