import { useState, type FormEvent } from "react";
import { Camera, LoaderCircle } from "lucide-react";
import { api } from "../../api";
import { useSensorResource } from "../sensors/SensorData";
import type { SensorChannels } from "../sensors/SensorTypes";
import type { RecordingSummary } from "../recordings/RecordingTypes";
import type { InferenceSource } from "../perception/PerceptionTypes";
import type { DatasetImage } from "./DatasetTypes";

export default function DatasetImageCapture({
  initialSource,
  onCaptured,
  onClose,
}: {
  initialSource?: InferenceSource | null;
  onCaptured: (image: DatasetImage) => void;
  onClose: () => void;
}) {
  const [recording, setRecording] = useState(initialSource?.recording_id ?? "");
  const [channel, setChannel] = useState(
    initialSource ? String(initialSource.channel_id) : "",
  );
  const [ordinal, setOrdinal] = useState(
    initialSource ? String(initialSource.ordinal) : "",
  );
  const [decoder, setDecoder] = useState(
    initialSource
      ? `${initialSource.decoder_id}\u0000${initialSource.decoder_version}`
      : "",
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const recordings = useSensorResource<RecordingSummary[]>("/recordings");
  const support = useSensorResource<SensorChannels>(
    recording
      ? `/recordings/${encodeURIComponent(recording)}/sensor-channels`
      : null,
  );
  const selectedChannel = support.data?.channels.find(
    (item) => String(item.channel_id) === channel,
  );
  const exactDecoder =
    decoder ||
    (selectedChannel?.decoders[0]
      ? `${selectedChannel.decoders[0].id}\u0000${selectedChannel.decoders[0].version}`
      : "");
  async function capture(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (
      !/^\d+$/.test(ordinal) ||
      !Number.isSafeInteger(Number(ordinal)) ||
      Number(ordinal) > 100000 ||
      !selectedChannel ||
      !exactDecoder
    ) {
      setError(
        "Select a supported camera channel and exact file ordinal from 0 to 100000.",
      );
      return;
    }
    const [decoderId, decoderVersion] = exactDecoder.split("\u0000");
    setBusy(true);
    try {
      onCaptured(
        await api<DatasetImage>("/dataset-images", {
          method: "POST",
          body: JSON.stringify({
            recording_id: recording,
            channel_id: Number(channel),
            ordinal: Number(ordinal),
            decoder_id: decoderId,
            decoder_version: decoderVersion,
          }),
        }),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Image capture failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="dataset-image-capture"
      aria-label="Capture source image"
    >
      <div className="dataset-section-toolbar">
        <div>
          <h3>
            <Camera size={17} />
            Capture an original image
          </h3>
          <p>
            This preserves one exact camera message as a dataset asset,
            independently of inference.
          </p>
        </div>
      </div>
      <form onSubmit={capture}>
        <fieldset disabled={busy}>
          <div className="dataset-form-grid two">
            <label>
              Recording
              <select
                aria-label="Dataset image recording"
                required
                value={recording}
                onChange={(e) => {
                  setRecording(e.target.value);
                  setChannel("");
                  setDecoder("");
                  setOrdinal("");
                }}
              >
                <option value="">Select a recording</option>
                {recordings.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Camera channel
              <select
                aria-label="Dataset image channel"
                required
                value={channel}
                onChange={(e) => {
                  setChannel(e.target.value);
                  setDecoder("");
                  setOrdinal("");
                }}
              >
                <option value="">Select a supported camera channel</option>
                {support.data?.channels
                  .filter(
                    (item) =>
                      item.kind === "camera" && item.status === "supported",
                  )
                  .map((item) => (
                    <option value={item.channel_id} key={item.channel_id}>
                      #{item.channel_id} · {item.topic}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Original file ordinal
              <input
                aria-label="Dataset image ordinal"
                inputMode="numeric"
                required
                value={ordinal}
                onChange={(e) => setOrdinal(e.target.value)}
              />
            </label>
            <label>
              Decoder revision
              <select
                aria-label="Dataset image decoder"
                required
                value={exactDecoder}
                onChange={(e) => setDecoder(e.target.value)}
              >
                <option value="">Select a decoder version</option>
                {selectedChannel?.decoders.map((item) => (
                  <option
                    key={`${item.id}/${item.version}`}
                    value={`${item.id}\u0000${item.version}`}
                  >
                    {item.name} · {item.id} / {item.version}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </fieldset>
        {recordings.error || support.error ? (
          <p role="alert" className="inline-error">
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
        {error && (
          <p role="alert" className="inline-error">
            {error} Retrying the same original input resolves its existing image
            asset.
          </p>
        )}
        <div className="dataset-actions">
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            Close image capture
          </button>
          <button
            className="button primary"
            disabled={busy || !selectedChannel || !exactDecoder || !ordinal}
          >
            {busy && <LoaderCircle size={14} className="spin" />}
            {busy ? "Capturing original image…" : "Capture dataset image"}
          </button>
        </div>
      </form>
    </section>
  );
}
