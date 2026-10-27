import { CopyButton } from "../ui";
import { secondsFromNanoseconds } from "../recordings/RecordingTime";
import type {
  SensorBundleEntry,
  SensorDecodeResult,
  SensorMessage,
} from "./SensorTypes";

export function SensorTimeEvidence({ entry }: { entry: SensorBundleEntry }) {
  const age =
    entry.age_ns !== null
      ? `${secondsFromNanoseconds(BigInt(entry.age_ns))} s`
      : "Unavailable";
  return (
    <div
      className={`sensor-time-evidence ${entry.freshness}`}
      data-testid={`sensor-time-${entry.channel_id}`}
    >
      <div>
        <span>
          {entry.selection_status === "missing"
            ? "No sample at cursor"
            : entry.freshness === "stale"
              ? "Stale · frame withheld"
              : entry.time_relation === "at_cursor"
                ? "At cursor"
                : "Earlier sample"}
        </span>
        <strong>Age {age}</strong>
      </div>
      <p>
        {entry.max_age_ns === null
          ? "No age threshold declared · freshness unknown"
          : `Age limit ${secondsFromNanoseconds(BigInt(entry.max_age_ns))} s · ${entry.age_policy_source} policy`}
      </p>
      {entry.aligned_time_ns !== null && (
        <small>
          Aligned log time <code>{entry.aligned_time_ns}</code>
        </small>
      )}
      {entry.next_aligned_time_ns !== null &&
        entry.selection_status === "missing" && (
          <small>
            First available aligned sample is in the future:{" "}
            <code>{entry.next_aligned_time_ns}</code>
          </small>
        )}
    </div>
  );
}

export function SensorDecodedEvidence({
  decoded,
}: {
  decoded: SensorDecodeResult;
}) {
  return (
    <div className="sensor-decoded-evidence">
      <div>
        <span>Source frame</span>
        <strong>{decoded.frame_id || "Not recorded"}</strong>
      </div>
      <div>
        <span>Encoding</span>
        <strong>{decoded.original_encoding}</strong>
      </div>
      <div>
        <span>Decoder</span>
        <strong>
          {decoded.decoder_id} / {decoded.decoder_version}
        </strong>
      </div>
      {decoded.point_cloud && (
        <p className="sensor-count-evidence" data-testid="sensor-point-counts">
          <b>{decoded.point_cloud.point_count.toLocaleString()}</b> rendered /{" "}
          {decoded.point_cloud.original_point_count.toLocaleString()} source
          points · {decoded.point_cloud.invalid_points} invalid XYZ excluded ·{" "}
          {decoded.point_cloud.skipped_points} sampled out · stride{" "}
          {decoded.point_cloud.sampling_stride}
          {decoded.point_cloud.intensity_invalid_points > 0
            ? ` · ${decoded.point_cloud.intensity_invalid_points} invalid intensity values`
            : ""}
        </p>
      )}
      <p className="sensor-coordinate-note">
        {decoded.transform_policy === "message_pose_to_frame"
          ? "Recorded message pose applied by the decoder."
          : "Decoder preserves native coordinates."}{" "}
        {decoded.coordinate_convention.replaceAll("_", " ")} · {decoded.units}
      </p>
      {decoded.warnings.map((warning, index) => (
        <p className="sensor-warning" key={index}>
          {warning}
        </p>
      ))}
    </div>
  );
}

export function SensorMessageEvidence({
  message,
  decoded,
}: {
  message: SensorMessage;
  decoded?: SensorDecodeResult | null;
}) {
  return (
    <div className="sensor-message-evidence">
      <dl className="sensor-evidence-grid">
        <div>
          <dt>Original ordinal / sequence</dt>
          <dd>
            {message.ordinal} / {message.sequence}
          </dd>
        </div>
        <div>
          <dt>Log time · ns</dt>
          <dd>
            <code>{message.log_time_ns}</code>
          </dd>
        </div>
        <div>
          <dt>Publish time · ns</dt>
          <dd>
            <code>{message.publish_time_ns}</code>
          </dd>
        </div>
        <div>
          <dt>Capture time · ns</dt>
          <dd>
            <code>{decoded?.capture_time_ns ?? "Not recorded"}</code>
          </dd>
        </div>
        <div>
          <dt>Original payload</dt>
          <dd>{message.size_bytes.toLocaleString()} bytes</dd>
        </div>
        <div>
          <dt>Capture/log clock relationship</dt>
          <dd>Not verified</dd>
        </div>
      </dl>
      <div className="sensor-hash">
        <span>Original sample SHA-256</span>
        <code>{message.sha256}</code>
        <CopyButton text={message.sha256} />
      </div>
      {decoded?.source_pose && (
        <details>
          <summary>Recorded source pose</summary>
          <pre>
            <code>{JSON.stringify(decoded.source_pose, null, 2)}</code>
          </pre>
        </details>
      )}
      {decoded?.camera && (
        <div className="sensor-hash">
          <span>Decoded PNG SHA-256</span>
          <code>{decoded.camera.sha256}</code>
          <CopyButton text={decoded.camera.sha256} />
        </div>
      )}
      {decoded?.point_cloud && (
        <div className="sensor-hash">
          <span>Decoded XYZ SHA-256</span>
          <code>{decoded.point_cloud.sha256}</code>
          <CopyButton text={decoded.point_cloud.sha256} />
        </div>
      )}
    </div>
  );
}
