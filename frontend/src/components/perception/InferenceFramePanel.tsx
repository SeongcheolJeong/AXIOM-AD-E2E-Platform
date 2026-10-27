import {
  ArrowDownToLine,
  AlertCircle,
  Clock3,
  LoaderCircle,
} from "lucide-react";
import { DownloadLink } from "../ArtifactAccess";
import DetectionImage from "./DetectionImage";
import type { InferenceFrame } from "./PerceptionTypes";

export default function InferenceFramePanel({
  frame,
  label = "Saved image with model predictions",
}: {
  frame: InferenceFrame;
  label?: string;
}) {
  return (
    <section
      className="perception-frame-result"
      data-testid="perception-frame-result"
    >
      <div className="perception-frame-heading">
        <strong>Original ordinal #{frame.input.ordinal}</strong>
        <span
          className={`perception-pill ${frame.status === "completed" ? "ready" : frame.status === "failed" ? "failure" : ""}`}
        >
          {frame.status.replaceAll("_", " ")}
        </span>
      </div>
      {frame.status === "completed" && frame.image ? (
        <DetectionImage
          key={`${frame.input.recording_sha256}/${frame.input.channel_id}/${frame.input.ordinal}/${frame.image.sha256}`}
          image={frame.image}
          detections={frame.detections}
          label={label}
        />
      ) : (
        <div
          className={`perception-frame-empty ${frame.status === "failed" ? "failure" : ""}`}
        >
          {frame.status === "failed" ? (
            <AlertCircle size={25} />
          ) : frame.status === "running" ? (
            <LoaderCircle size={25} className="spin" />
          ) : (
            <Clock3 size={25} />
          )}
          <strong>
            {frame.status === "failed"
              ? "This input failed"
              : frame.status === "not_attempted"
                ? "This input has not been attempted"
                : frame.status === "running"
                  ? "Inference is running for this input"
                  : "Saved input image unavailable"}
          </strong>
          <p>
            {frame.error ||
              "No completed prediction is recorded for this input. This is not a zero-detection result."}
          </p>
        </div>
      )}
      <div className="perception-image-provenance">
        <span>
          Channel #{frame.input.channel_id} · {frame.input.decoder_id} /{" "}
          {frame.input.decoder_version}
        </span>
        <code>Log {frame.input.log_time_ns} ns</code>
        {frame.started_at && <span>Attempt started {frame.started_at}</span>}
        {frame.image && (
          <span>
            Capture {frame.image.capture_time_ns ?? "unknown"} ns · Frame{" "}
            {frame.image.frame_id || "unknown"}
          </span>
        )}
      </div>
      {frame.image && (
        <div className="perception-hash">
          <span>Saved input PNG SHA-256</span>
          <code>{frame.image.sha256}</code>
          <DownloadLink
            href={frame.image.download_url}
            className="button secondary small"
            download
          >
            <ArrowDownToLine size={13} />
            Input PNG
          </DownloadLink>
        </div>
      )}
      <details className="perception-details">
        <summary>Source identity & actual runtime evidence</summary>
        <dl className="perception-evidence-list">
          <div>
            <dt>Recording ID</dt>
            <dd>{frame.input.recording_id}</dd>
          </div>
          <div>
            <dt>Recording SHA-256</dt>
            <dd>
              <code>{frame.input.recording_sha256}</code>
            </dd>
          </div>
          <div>
            <dt>Original message SHA-256</dt>
            <dd>
              <code>{frame.input.message_sha256}</code>
            </dd>
          </div>
          <div>
            <dt>Publish time</dt>
            <dd>
              <code>{frame.input.publish_time_ns} ns</code>
            </dd>
          </div>
          <div>
            <dt>Prediction output hash</dt>
            <dd>
              <code>{frame.output_hash ?? "No output stored"}</code>
            </dd>
          </div>
        </dl>
        {frame.runtime ? (
          <pre aria-label="Inference runtime evidence">
            {JSON.stringify(frame.runtime, null, 2)}
          </pre>
        ) : (
          <p>No completed runtime evidence is available.</p>
        )}
      </details>
    </section>
  );
}
