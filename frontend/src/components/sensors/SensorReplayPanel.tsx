import { useState } from "react";
import {
  Camera,
  Crosshair,
  FileSearch,
  LoaderCircle,
  ScanLine,
} from "lucide-react";
import SensorPreview from "./SensorPreview";
import type { InferenceSource } from "../perception/PerceptionTypes";
import {
  SensorDecodedEvidence,
  SensorMessageEvidence,
  SensorTimeEvidence,
} from "./SensorEvidence";
import type {
  RigSnapshot,
  SensorBundleEntry,
  SensorChannelSupport,
  SensorKind,
} from "./SensorTypes";

export default function SensorReplayPanel({
  kind,
  channels,
  channelId,
  onChannel,
  entry,
  rig,
  loading,
  error,
  onInspect,
  onInfer,
}: {
  kind: SensorKind;
  channels: SensorChannelSupport[];
  channelId: string;
  onChannel: (id: string) => void;
  entry: SensorBundleEntry | undefined;
  rig: RigSnapshot | null;
  loading: boolean;
  error: string;
  onInspect: (channelId: number, ordinal: number) => void;
  onInfer?: (source: InferenceSource) => void;
}) {
  const [coordinateView, setCoordinateView] = useState<"native" | "rig">(
    "native",
  );
  const decoded =
    entry?.frame?.status === "decoded" ? entry.frame.decoded : null;
  const temporalAvailable =
    entry?.selection_status === "selected" &&
    entry.freshness !== "stale" &&
    entry.aligned_time_ns !== null &&
    entry.age_ns !== null &&
    BigInt(entry.age_ns) >= 0n;
  const rigAvailable =
    entry?.transform_status === "available" &&
    entry.transform !== null &&
    rig !== null;
  const showRig = coordinateView === "rig" && rigAvailable;
  const requestedRigUnavailable = coordinateView === "rig" && !rigAvailable;
  return (
    <section
      className={`sensor-replay-panel ${kind}`}
      aria-label={
        kind === "camera" ? "Camera sensor replay" : "LiDAR sensor replay"
      }
      data-testid={`sensor-${kind}-panel`}
    >
      <header className="sensor-panel-heading">
        <span>
          {kind === "camera" ? <Camera size={17} /> : <ScanLine size={17} />}
          <strong>{kind === "camera" ? "Camera" : "LiDAR"}</strong>
        </span>
        <label>
          <span className="sr-only">
            {kind === "camera" ? "Camera channel" : "LiDAR channel"}
          </span>
          <select
            aria-label={kind === "camera" ? "Camera channel" : "LiDAR channel"}
            value={channelId}
            onChange={(event) => {
              onChannel(event.target.value);
              setCoordinateView("native");
            }}
          >
            <option value="">
              No {kind === "camera" ? "camera" : "LiDAR"} selected
            </option>
            {channels
              .filter(
                (channel) =>
                  channel.kind === kind && channel.status === "supported",
              )
              .map((channel) => (
                <option key={channel.channel_id} value={channel.channel_id}>
                  #{channel.channel_id} · {channel.topic || "Unnamed topic"}
                </option>
              ))}
          </select>
        </label>
      </header>
      {kind === "lidar" && (
        <div className="sensor-coordinate-switch">
          <span>
            <Crosshair size={12} /> Coordinates
          </span>
          <div>
            <button
              type="button"
              aria-pressed={coordinateView === "native"}
              onClick={() => setCoordinateView("native")}
            >
              Native frame
            </button>
            <button
              type="button"
              aria-pressed={coordinateView === "rig"}
              disabled={!rigAvailable}
              onClick={() => setCoordinateView("rig")}
            >
              Declared rig frame
            </button>
          </div>
        </div>
      )}
      {loading ? (
        <div className="sensor-preview-empty" role="status">
          <LoaderCircle size={22} className="spin" />
          <strong>Seeking actual sensor sample…</strong>
        </div>
      ) : error ? (
        <div className="sensor-preview-empty error" role="alert">
          {error}
        </div>
      ) : !channelId ? (
        <div className="sensor-preview-empty">
          <strong>
            Select a supported {kind === "camera" ? "camera" : "LiDAR"} channel
          </strong>
          <p>
            Capability comes from the registered decoder and exact schema,
            independently of the topic name.
          </p>
        </div>
      ) : !entry ? (
        <div className="sensor-preview-empty">
          No channel observation returned.
        </div>
      ) : !temporalAvailable ? (
        <div className="sensor-preview-empty">
          <strong>
            {entry.freshness === "stale"
              ? "Stale sample withheld"
              : "No sample at this cursor"}
          </strong>
          <p>
            {entry.reason ??
              "Only samples at or before the aligned cursor can be displayed. Future samples are not substituted."}
          </p>
        </div>
      ) : !decoded ? (
        <div className="sensor-preview-empty">
          <strong>
            {entry.frame?.status.replaceAll("_", " ") ?? "No decoded frame"}
          </strong>
          <p>
            {entry.frame?.reason ??
              entry.reason ??
              "Stored bytes remain available for inspection."}
          </p>
        </div>
      ) : requestedRigUnavailable ? (
        <div className="sensor-preview-empty">
          <strong>Rig-frame view withheld</strong>
          <p>
            {entry.transform_status.replaceAll("_", " ")}. Select Native frame
            to inspect source coordinates.
          </p>
        </div>
      ) : (
        <SensorPreview
          decoded={decoded}
          transform={showRig ? entry.transform : null}
          frame={showRig ? rig!.reference_frame : (decoded.frame_id ?? "")}
          convention={
            showRig
              ? rig!.coordinate_convention
              : decoded.coordinate_convention === "ros_right_handed_z_up"
                ? "right_handed_z_up"
                : "unknown"
          }
        />
      )}
      {entry && !loading && !error && (
        <>
          <SensorTimeEvidence entry={entry} />
          {decoded && <SensorDecodedEvidence decoded={decoded} />}
          <div className="sensor-transform-evidence">
            <span>Calibration / transform</span>
            <strong>
              {entry.transform_status === "available"
                ? "Source-declared · frame match"
                : entry.transform_status.replaceAll("_", " ")}
            </strong>
            {entry.declared_sensor && (
              <small>
                Sensor {entry.declared_sensor.key} · declared frame{" "}
                {entry.declared_sensor.frame_id}
              </small>
            )}
            {showRig && (
              <p>
                Static sensor → {rig?.reference_frame} transform applied.
                Source-declared geometry; accuracy not verified.
              </p>
            )}
          </div>
          {entry.message && (
            <div className="sensor-panel-evidence-actions">
              <span>
                Original ordinal <b>{entry.message.ordinal}</b>
              </span>
              <button
                type="button"
                className="sensor-text-button"
                aria-label={`Inspect original sample from channel ${entry.channel_id}`}
                onClick={() =>
                  onInspect(entry.channel_id, entry.message!.ordinal)
                }
              >
                <FileSearch size={14} /> Inspect original sample
              </button>
            </div>
          )}
          {onInfer &&
            temporalAvailable &&
            decoded?.kind === "camera" &&
            decoded.camera &&
            entry.frame &&
            entry.message && (
              <button
                type="button"
                className="button primary small"
                onClick={() =>
                  onInfer({
                    recording_id: entry.frame!.recording_id,
                    channel_id: entry.channel_id,
                    ordinal: entry.message!.ordinal,
                    decoder_id: decoded.decoder_id,
                    decoder_version: decoded.decoder_version,
                  })
                }
              >
                Run inference on this image
              </button>
            )}
          {entry.message && (
            <details className="sensor-payload-disclosure">
              <summary>Clocks, hashes & recorded declarations</summary>
              <SensorMessageEvidence
                message={entry.message}
                decoded={decoded}
              />
              <p className="sensor-form-help">
                Aligned time = log time + {entry.log_time_offset_ns} ns declared
                offset. No physical clock synchronization is inferred.
              </p>
              {entry.declared_sensor && (
                <pre>
                  <code>{JSON.stringify(entry.declared_sensor, null, 2)}</code>
                </pre>
              )}
              {entry.frame?.warnings.map((warning, index) => (
                <p key={index} className="sensor-warning">
                  {warning}
                </p>
              ))}
            </details>
          )}
        </>
      )}
    </section>
  );
}
