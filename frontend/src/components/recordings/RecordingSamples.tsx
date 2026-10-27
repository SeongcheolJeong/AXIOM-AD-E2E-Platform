import RecordingSignalChart from "./RecordingSignalChart";
import { useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Braces,
  ChevronRight,
  Clock3,
  FileCode2,
  Hash,
  LoaderCircle,
  ListStart,
  LocateFixed,
} from "lucide-react";
import { api, formatBytes } from "../../api";
import { CopyButton } from "../ui";
import { relativeSampleTime, secondsFromNanoseconds } from "./RecordingTime";
import type {
  RecordingChannel,
  RecordingSample,
  RecordingSamples as SamplesResponse,
} from "./RecordingTypes";

type SampleAnchor =
  { kind: "cursor" } | { kind: "first" } | { kind: "after"; ordinal: number } | { kind: "ordinal"; ordinal: number };

function useSamples(
  recordingId: string,
  channelId: number | null,
  cursor: string,
  limit: number,
  anchor: SampleAnchor,
) {
  const query = new URLSearchParams({
    channel_id: String(channelId),
    limit: String(limit),
  });
  if (anchor.kind === "cursor") query.set("at_ns", cursor);
  else if (anchor.kind === "first") query.set("from_start", "true");
  else if (anchor.kind === "ordinal") query.set("from_ordinal", String(anchor.ordinal));
  else query.set("after_ordinal", String(anchor.ordinal));
  const path =
    channelId === null
      ? null
      : `/recordings/${encodeURIComponent(recordingId)}/samples?${query}`;
  const [state, setState] = useState<{
    path: string | null;
    data: SamplesResponse | null;
    loading: boolean;
    error: string;
  }>({ path: null, data: null, loading: false, error: "" });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    setState({ path, data: null, loading: path !== null, error: "" });
    if (path === null) return;
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<SamplesResponse>(path, { signal: controller.signal })
        .then((result) => {
          if (active)
            setState({ path, data: result, loading: false, error: "" });
        })
        .catch((reason) => {
          if (active && !controller.signal.aborted)
            setState({
              path,
              data: null,
              loading: false,
              error:
                reason instanceof Error
                  ? reason.message
                  : "Sample query failed.",
            });
        });
    }, 160);
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [path, revision]);
  const current = state.path === path;
  return {
    data: current ? state.data : null,
    loading: path !== null && (!current || state.loading),
    error: current ? state.error : "",
    requestKey: path,
    retry: () => setRevision((value) => value + 1),
  };
}

export default function RecordingSamples({
  recordingId,
  channel,
  cursor,
  origin,
}: {
  recordingId: string;
  channel: RecordingChannel | null;
  cursor: string;
  origin: bigint;
}) {
  const [limit, setLimit] = useState(10);
  const [ordinal, setOrdinal] = useState<number | null>(null);
  const target = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const targetOrdinal = target.get("recording") === recordingId && Number(target.get("channel")) === channel?.id && target.has("ordinal") ? Number(target.get("ordinal")) : -1;
  const [anchor, setAnchor] = useState<SampleAnchor>(Number.isSafeInteger(targetOrdinal) && targetOrdinal >= 0 ? { kind: "ordinal", ordinal: targetOrdinal } : { kind: "cursor" });
  const [history, setHistory] = useState<SampleAnchor[]>([]);
  const { data, loading, error, retry, requestKey } = useSamples(
    recordingId,
    channel?.id ?? null,
    cursor,
    limit,
    anchor,
  );
  useEffect(() => setOrdinal(null), [requestKey]);
  const samples = data?.samples ?? [];
  const selected =
    samples.find((sample) => sample.ordinal === ordinal) ?? samples[0];
  const firstIsFuture =
    anchor.kind === "cursor" &&
    samples[0] &&
    BigInt(samples[0].log_time_ns) > BigInt(cursor);
  const nextOrdinal = data?.next_after_ordinal;
  const canContinue = nextOrdinal !== null && nextOrdinal !== undefined;
  const changeMode = (next: SampleAnchor) => {
    setAnchor(next);
    setHistory([]);
    setOrdinal(null);
  };

  return (
    <section
      className="panel recording-samples"
      aria-label="Recorded sample inspector"
      data-selected-ordinal={selected?.ordinal}
      data-selected-sha256={selected?.sha256}
    >
      <div className="recording-section-heading">
        <div>
          <h3>Sample inspector</h3>
          <p>
            {channel ? (
              <>
                <span className="mono">Channel #{channel.id}</span> ·{" "}
                {channel.topic || "Unnamed topic"}
              </>
            ) : (
              "Select a channel to query its recorded messages."
            )}
          </p>
        </div>
        <label className="recording-limit-label">
          Window size
          <select
            aria-label="Samples per request"
            value={limit}
            onChange={(event) => {
              setLimit(Number(event.target.value));
              setHistory([]);
            }}
          >
            <option value={1}>1 sample</option>
            <option value={10}>10 samples</option>
            <option value={25}>25 samples</option>
            <option value={100}>100 samples</option>
          </select>
        </label>
      </div>
      <div className="recording-browse-controls">
        <div>
          <button
            type="button"
            className="button secondary small"
            aria-pressed={anchor.kind === "cursor"}
            disabled={!channel}
            onClick={() => changeMode({ kind: "cursor" })}
          >
            <LocateFixed size={14} />
            Seek at cursor
          </button>
          <button
            type="button"
            className="button secondary small"
            aria-pressed={anchor.kind === "first"}
            disabled={!channel}
            onClick={() => changeMode({ kind: "first" })}
          >
            <ListStart size={14} />
            Browse from first sample
          </button>
        </div>
        <div>
          <button
            type="button"
            className="button secondary small"
            disabled={loading || history.length === 0}
            onClick={() => {
              const previous = history.at(-1);
              if (!previous) return;
              setAnchor(previous);
              setHistory((entries) => entries.slice(0, -1));
            }}
          >
            <ArrowLeft size={14} />
            Previous window
          </button>
          <button
            type="button"
            className="button secondary small"
            disabled={loading || !canContinue}
            onClick={() => {
              if (nextOrdinal === null || nextOrdinal === undefined) return;
              setHistory((entries) => [...entries, anchor]);
              setAnchor({ kind: "after", ordinal: nextOrdinal });
            }}
          >
            Next window
            <ArrowRight size={14} />
          </button>
        </div>
      </div>
      <p
        className="recording-query-note"
        data-testid="recording-sample-query-mode"
      >
        {anchor.kind === "cursor"
          ? "Nearest sample at or before the log-time cursor, then following samples. Equal timestamps anchor on the last file ordinal; browse from the first sample to inspect every duplicate."
          : `Chronological browsing ${anchor.kind === "first" ? "from the first sample" : `${anchor.kind === "ordinal" ? "from exact" : "after"} ordinal ${anchor.ordinal}`}. Ordered by log time and file ordinal. The timeline cursor is retained only for age comparisons.`}
      </p>
      {error ? (
        <div className="recording-query-state" role="alert">
          <AlertCircle size={22} />
          <p>{error}</p>
          <button className="button secondary" onClick={retry}>
            Retry sample query
          </button>
        </div>
      ) : loading ? (
        <div className="recording-query-state" role="status">
          <LoaderCircle className="spin" size={22} />
          <p>Seeking recorded samples…</p>
        </div>
      ) : !selected ? (
        <div className="recording-query-state">
          <FileCode2 size={25} />
          <h4>
            {channel ? "No messages in this channel" : "No channel selected"}
          </h4>
          <p>
            {channel
              ? "The channel definition is preserved, but it contains no samples to inspect."
              : "Channel metadata is required before a sample can be queried."}
          </p>
        </div>
      ) : (
        <>
          {firstIsFuture && (
            <div className="recording-future-note" role="status">
              <Clock3 size={16} />
              <p>
                No sample exists at or before this cursor in the selected
                channel. The first available message is{" "}
                <strong>in the future</strong>.
              </p>
            </div>
          )}
          {data?.warnings.length ? (
            <div className="recording-warning-list">
              {data.warnings.map((warning, index) => (
                <p key={index}>
                  <AlertCircle size={14} />
                  {warning}
                </p>
              ))}
            </div>
          ) : null}
          <RecordingSignalChart samples={samples} origin={origin} />
          <div className="recording-sample-grid">
            <div className="recording-sample-window">
              <div className="recording-window-label">
                <span>RETURNED WINDOW</span>
                <span>{samples.length} messages</span>
              </div>
              <div
                className="recording-sample-list"
                aria-label="Returned samples"
              >
                {samples.map((sample) => {
                  const temporal = relativeSampleTime(
                    sample.log_time_ns,
                    cursor,
                  );
                  return (
                    <button
                      type="button"
                      key={sample.ordinal}
                      className={
                        selected.ordinal === sample.ordinal ? "active" : ""
                      }
                      onClick={() => setOrdinal(sample.ordinal)}
                      aria-label={`Inspect sample ordinal ${sample.ordinal}`}
                      aria-current={
                        selected.ordinal === sample.ordinal ? "true" : undefined
                      }
                    >
                      <span>
                        <strong className="mono">
                          {secondsFromNanoseconds(
                            BigInt(sample.log_time_ns) - origin,
                          )}{" "}
                          s
                        </strong>
                        <small>
                          Ordinal {sample.ordinal} · seq {sample.sequence}
                        </small>
                      </span>
                      <i
                        className={`recording-sample-position ${temporal.kind}`}
                        title={temporal.label}
                      />
                      <ChevronRight size={14} />
                    </button>
                  );
                })}
              </div>
              <p className="recording-window-footnote">
                Times are relative to recording start. Equal timestamps remain
                distinct by file ordinal.
              </p>
            </div>
            <SamplePayload sample={selected} cursor={cursor} />
          </div>
        </>
      )}
    </section>
  );
}

function SamplePayload({
  sample,
  cursor,
}: {
  sample: RecordingSample;
  cursor: string;
}) {
  const temporal = relativeSampleTime(sample.log_time_ns, cursor);
  const decoded = sample.decode_status === "decoded";
  const title = decoded
    ? "Decoded JSON"
    : sample.decode_status === "invalid_json"
      ? "Invalid JSON · raw preview"
      : sample.decode_status === "too_large"
        ? "Payload exceeds decoder limit"
        : "Opaque payload";
  const display = decoded
    ? (sample.decoded_text ?? JSON.stringify(sample.decoded, null, 2))
    : sample.preview_hex.match(/.{1,2}/g)?.join(" ") ||
      "No preview bytes available.";
  const publishDelta =
    BigInt(sample.publish_time_ns) - BigInt(sample.log_time_ns);
  return (
    <div className="recording-payload" data-testid="recording-sample-payload">
      <div className="recording-payload-heading">
        <span>
          <span
            className={`recording-decoder-tag ${decoded ? "json" : "opaque"}`}
          >
            {decoded ? <Braces size={14} /> : <FileCode2 size={14} />} {title}
          </span>
          {sample.truncated && (
            <span className="recording-prefix-tag">Bounded prefix</span>
          )}
        </span>
        <CopyButton
          text={decoded ? display : sample.preview_hex}
          label={decoded ? "Copy JSON" : "Copy hex"}
        />
      </div>
      <span
        className={`recording-time-status ${temporal.kind}`}
        data-testid="recording-sample-time-status"
      >
        <Clock3 size={14} />
        {temporal.label}
      </span>
      <dl className="recording-sample-clocks">
        <div>
          <dt>Log time · ns</dt>
          <dd className="mono" data-testid="recording-sample-log-time">
            {sample.log_time_ns}
          </dd>
        </div>
        <div>
          <dt>Publish time · ns</dt>
          <dd className="mono" data-testid="recording-sample-publish-time">
            {sample.publish_time_ns}
          </dd>
        </div>
        <div>
          <dt>Publish − log</dt>
          <dd className="mono">
            {publishDelta > 0n ? "+" : ""}
            {secondsFromNanoseconds(publishDelta)} s
          </dd>
        </div>
        <div>
          <dt>Payload / sequence</dt>
          <dd>
            {formatBytes(sample.size_bytes)} · sequence {sample.sequence} ·
            ordinal {sample.ordinal}
          </dd>
        </div>
      </dl>
      <p className="recording-clock-note">
        Clocks are shown as stored. Their difference does not establish latency
        or clock synchronization.
      </p>
      <pre
        className={`recording-payload-code ${decoded ? "json" : "opaque"}`}
        aria-label={
          decoded ? "Decoded sample JSON" : "Opaque sample hex preview"
        }
      >
        <code>{display}</code>
      </pre>
      {!decoded && (
        <p className="recording-opaque-note">
          {sample.decode_status === "invalid_json"
            ? "This channel declares JSON, but the sample did not decode as valid JSON."
            : sample.decode_status === "too_large"
              ? "This message is preserved in the original file; only a bounded byte prefix is shown here."
              : "The raw-message inspector preserves this encoding as bytes. Open Sensor replay to check exact schema support in the registered sensor decoders."}
        </p>
      )}
      <details className="recording-hash-disclosure">
        <summary>
          <Hash size={13} />
          Sample SHA-256
        </summary>
        <code>{sample.sha256}</code>
        <CopyButton text={sample.sha256} />
      </details>
    </div>
  );
}
