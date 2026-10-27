import { useState } from "react";
import {
  AlertCircle,
  ArrowDownToLine,
  Check,
  Database,
  FileArchive,
  Fingerprint,
  GitBranch,
  Layers3,
  ShieldQuestion,
} from "lucide-react";
import { formatBytes, formatDate } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { CopyButton } from "../ui";
import RecordingChannels from "./RecordingChannels";
import RecordingTimeline from "./RecordingTimeline";
import RecordingSamples from "./RecordingSamples";
import { recordingDurationNs, secondsFromNanoseconds } from "./RecordingTime";
import type { RecordingDetail as Detail } from "./RecordingTypes";
import RecordingSensorReplay from "../sensors/RecordingSensorReplay";
import RecordingOrigin from "./RecordingOrigin";
import CaseCapture from "../cases/CaseCapture";
import type { InferenceSource } from "../perception/PerceptionTypes";

export default function RecordingDetail({
  recording,
  onOpenRun,
  onInfer,
}: {
  recording: Detail;
  onOpenRun?: (id: string) => void;
  onInfer?: (source: InferenceSource) => void;
}) {
  const target = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const targetChannel = target.get("recording") === recording.id && target.has("channel") ? Number(target.get("channel")) : null;
  const targetTime = target.get("recording") === recording.id ? target.get("at") : null;
  const validTime = targetTime && /^(0|[1-9][0-9]{0,19})$/.test(targetTime) && BigInt(targetTime) < 2n**64n ? targetTime : null;
  const [view, setView] = useState<"raw" | "sensors">("raw");
  const [channelId, setChannelId] = useState<number | null>(
    targetChannel !== null && recording.channels.some((c) => c.id === targetChannel) ? targetChannel : recording.channels[0]?.id ?? null,
  );
  const [cursor, setCursor] = useState(validTime ?? recording.start_time_ns ?? "0");
  const start = BigInt(recording.start_time_ns ?? "0");
  const end = BigInt(recording.end_time_ns ?? recording.start_time_ns ?? "0");
  const channel =
    recording.channels.find((candidate) => candidate.id === channelId) ?? null;
  const schema = recording.schemas.find(
    (candidate) => candidate.id === channel?.schema_id,
  );
  return (
    <div
      className={`recording-detail ${view === "sensors" ? "sensor-detail-active" : ""}`}
      data-testid="recording-detail"
    >
      <section className="panel recording-detail-header">
        <div className="recording-heading-line">
          <span className="recording-container-icon">
            <FileArchive size={24} />
          </span>
          <div>
            <div className="recording-kicker">
              MCAP CONTAINER{" "}
              <span>{recording.profile || "No declared profile"}</span>
            </div>
            <h2>{recording.name}</h2>
            <p>{recording.source || "No source label supplied"}</p>
          </div>
          <DownloadLink
            className="button secondary small"
            href={recording.download_url}
            download
            aria-label="Download original recording"
          >
            <ArrowDownToLine size={14} />
            Original file
          </DownloadLink>
        </div>
        <div className="recording-detail-stats">
          <div>
            <span>Messages scanned</span>
            <strong>{recording.message_count.toLocaleString()}</strong>
          </div>
          <div>
            <span>Channels / schemas</span>
            <strong>
              {recording.channel_count}{" "}
              <small>/ {recording.schemas.length}</small>
            </strong>
          </div>
          <div>
            <span>Log-time span</span>
            <strong>
              {secondsFromNanoseconds(
                recordingDurationNs(
                  recording.start_time_ns,
                  recording.end_time_ns,
                ),
                6,
              )}{" "}
              <small>s</small>
            </strong>
          </div>
          <div>
            <span>Original size</span>
            <strong>{formatBytes(recording.size_bytes)}</strong>
          </div>
        </div>
        <div className="recording-declared-source">
          <ShieldQuestion size={16} />
          <span>
            <strong>{recording.source_kind}</strong> source · user-declared, not
            verified
          </span>
          <span>Imported {formatDate(recording.created_at)}</span>
        </div>
      </section>
      <div
        className="recording-view-tabs"
        role="tablist"
        aria-label="Recording exploration views"
      >
        <button
          type="button"
          role="tab"
          aria-selected={view === "sensors"}
          onClick={() => setView("sensors")}
        >
          Sensor replay
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "raw"}
          onClick={() => setView("raw")}
        >
          Raw messages
        </button>
      </div>
      {recording.warnings.length > 0 && (
        <details className="recording-warnings">
          <summary>
            <AlertCircle size={16} />
            {recording.warnings.length} container notice
            {recording.warnings.length !== 1 ? "s" : ""}
          </summary>
          {recording.warnings.map((warning, index) => (
            <p key={index}>{warning}</p>
          ))}
        </details>
      )}
      {view === "raw" && (
        <RecordingChannels
          channels={recording.channels}
          selectedId={channelId}
          start={start}
          end={end}
          onSelect={setChannelId}
        />
      )}
      {recording.start_time_ns !== null ? (
        <RecordingTimeline
          start={start}
          end={end}
          cursor={cursor}
          onCursor={setCursor}
        />
      ) : (
        <div className="recording-notice">
          <AlertCircle size={17} />
          <p>
            This container has no message timestamps. Channel and schema
            definitions remain inspectable.
          </p>
        </div>
      )}
      {view === "sensors" && (
        <RecordingSensorReplay
          onInfer={onInfer}
          recording={recording}
          cursor={cursor}
          onCursor={setCursor}
          onOpenRaw={(channel, time) => {
            setChannelId(channel);
            setCursor(time);
            setView("raw");
          }}
        />
      )}
      <RecordingOrigin recordingId={recording.id} onOpenRun={onOpenRun} />
      {channel && <CaseCapture key={`${recording.id}/${channel.id}`} label="Create case from log interval" source={{ kind: "recording", recording_id: recording.id, channel_id: channel.id, start_time_ns: cursor, end_time_ns: cursor }} />}
      {view === "raw" && (
        <>
          {channel && (
            <div className="recording-selected-channel">
              <div>
                <span>SELECTED CHANNEL</span>
                <strong>
                  <b>#{channel.id}</b>
                  {channel.topic || "Unnamed topic"}
                </strong>
              </div>
              <div>
                <span>DECLARED ENCODING / DECODER</span>
                <strong>
                  {channel.message_encoding || "Unknown encoding"} /{" "}
                  {channel.decoder === "json" ? "JSON" : "Opaque bytes"}
                </strong>
              </div>
              <div>
                <span>CALIBRATION / TRANSFORMS</span>
                <strong>Not validated · inspect declarations</strong>
              </div>
            </div>
          )}
          <RecordingSamples
            key={channelId ?? "no-channel"}
            recordingId={recording.id}
            channel={channel}
            cursor={cursor}
            origin={start}
          />
          <section className="panel recording-metadata">
            <div className="recording-section-heading">
              <div>
                <h3>
                  <Layers3 size={17} />
                  Schemas, metadata & provenance
                </h3>
                <p>
                  Definitions and declarations are preserved without assuming
                  sensor semantics.
                </p>
              </div>
            </div>
            {channel && (
              <details open className="recording-metadata-block">
                <summary>
                  <Database size={15} />
                  Channel #{channel.id} metadata{" "}
                  <span>{Object.keys(channel.metadata).length} entries</span>
                </summary>
                <StringMap
                  data={channel.metadata}
                  empty="No channel metadata was recorded."
                />
                {schema && (
                  <p className="recording-schema-reference">
                    This channel references schema #{schema.id}:{" "}
                    {schema.name || "Unnamed schema"} (
                    {schema.encoding || "unknown encoding"}).
                  </p>
                )}
              </details>
            )}
            {recording.schemas.map((entry) => (
              <details key={entry.id} className="recording-metadata-block">
                <summary>
                  <Layers3 size={15} />
                  Schema #{entry.id} · {entry.name || "Unnamed schema"}
                  <span>{entry.encoding || "Unknown encoding"}</span>
                </summary>
                <p className="recording-schema-size">
                  {formatBytes(entry.size_bytes)} original definition
                  {entry.definition_truncated
                    ? " · displayed text is truncated"
                    : ""}
                </p>
                {entry.definition !== null ? (
                  <pre aria-label={`Schema ${entry.id} definition`}>
                    <code>{entry.definition}</code>
                  </pre>
                ) : (
                  <p className="recording-empty-inline">
                    No bounded UTF-8 definition is available. The original
                    schema bytes remain in the file.
                  </p>
                )}
                <HashLine label="Schema SHA-256" value={entry.sha256} />
              </details>
            ))}
            <details className="recording-metadata-block">
              <summary>
                <Database size={15} />
                Container metadata
                <span>{recording.metadata.length} records</span>
              </summary>
              {recording.metadata.length ? (
                recording.metadata.map((entry, index) => (
                  <div className="recording-metadata-record" key={index}>
                    <h4>{entry.name || "Unnamed metadata record"}</h4>
                    <StringMap
                      data={entry.metadata}
                      empty="No key/value entries."
                    />
                  </div>
                ))
              ) : (
                <p className="recording-empty-inline">
                  No MCAP Metadata records were stored.
                </p>
              )}
            </details>
            <details className="recording-metadata-block">
              <summary>
                <Fingerprint size={15} />
                Original bytes & content hashes
              </summary>
              <dl className="recording-provenance">
                <div>
                  <dt>Recording ID</dt>
                  <dd className="mono">{recording.id}</dd>
                </div>
                <div>
                  <dt>Declared source kind</dt>
                  <dd>{recording.source_kind} · user-declared</dd>
                </div>
                <div>
                  <dt>Source label</dt>
                  <dd>{recording.source || "Not supplied"}</dd>
                </div>
                <div>
                  <dt>First log time · ns</dt>
                  <dd className="mono">
                    {recording.start_time_ns ?? "No messages"}
                  </dd>
                </div>
                <div>
                  <dt>Last log time · ns</dt>
                  <dd className="mono">
                    {recording.end_time_ns ?? "No messages"}
                  </dd>
                </div>
              </dl>
              <HashLine
                label="Original MCAP SHA-256"
                value={recording.sha256}
              />
              {recording.index && (
                <div className="recording-index-provenance">
                  <h4>
                    <GitBranch size={14} />
                    Seek index · {recording.index.version}
                  </h4>
                  <p>
                    {formatBytes(recording.index.size_bytes)} ·{" "}
                    {recording.index.source_sha256 === recording.sha256 ? (
                      <>
                        <Check size={13} />
                        Source hash matches original MCAP
                      </>
                    ) : (
                      <strong className="recording-provenance-mismatch">
                        Index source hash differs from this recording
                      </strong>
                    )}
                  </p>
                  <HashLine
                    label="Index SHA-256"
                    value={recording.index.sha256}
                  />
                  <HashLine
                    label="Index source SHA-256"
                    value={recording.index.source_sha256}
                  />
                </div>
              )}
            </details>
          </section>
        </>
      )}
    </div>
  );
}

function StringMap({
  data,
  empty,
}: {
  data: Record<string, string>;
  empty: string;
}) {
  const entries = Object.entries(data);
  return entries.length ? (
    <dl className="recording-string-map">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  ) : (
    <p className="recording-empty-inline">{empty}</p>
  );
}
function HashLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="recording-hash-line">
      <span>{label}</span>
      <code>{value}</code>
      <CopyButton text={value} />
    </div>
  );
}
