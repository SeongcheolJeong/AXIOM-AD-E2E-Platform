import { Braces, FileCode2, Radio } from "lucide-react";
import {
  channelMeanRate,
  secondsFromNanoseconds,
  timelinePosition,
  TIMELINE_STEPS,
} from "./RecordingTime";
import type { RecordingChannel } from "./RecordingTypes";

export default function RecordingChannels({
  channels,
  selectedId,
  start,
  end,
  onSelect,
}: {
  channels: RecordingChannel[];
  selectedId: number | null;
  start: bigint;
  end: bigint;
  onSelect: (id: number) => void;
}) {
  return (
    <section
      className="panel recording-channels"
      aria-label="Recording channels"
    >
      <div className="recording-section-heading">
        <div>
          <h3>
            <Radio size={17} />
            Channels & recorded spans
          </h3>
          <p>Select by channel ID; topics may repeat.</p>
        </div>
        <span className="recording-count-tag">{channels.length} channels</span>
      </div>
      <div className="recording-channel-table-scroll">
        <div className="recording-channel-table">
          <div className="recording-channel-column-labels">
            <span>CHANNEL / TOPIC</span>
            <span>MESSAGES</span>
            <span>MEAN RATE</span>
            <span>FIRST → LAST SAMPLE</span>
          </div>
          {channels.map((channel) => {
            const hasBounds =
              channel.start_time_ns !== null && channel.end_time_ns !== null;
            const left = hasBounds
              ? (timelinePosition(BigInt(channel.start_time_ns!), start, end) /
                  TIMELINE_STEPS) *
                100
              : 0;
            const right = hasBounds
              ? (timelinePosition(BigInt(channel.end_time_ns!), start, end) /
                  TIMELINE_STEPS) *
                100
              : 0;
            const rate = channelMeanRate(channel);
            return (
              <button
                type="button"
                key={channel.id}
                aria-label={`Select channel ${channel.id} · ${channel.topic || "Unnamed topic"}`}
                aria-pressed={selectedId === channel.id}
                className={`recording-channel-row ${selectedId === channel.id ? "active" : ""}`}
                onClick={() => onSelect(channel.id)}
              >
                <span className="recording-channel-name">
                  <span className="recording-channel-id">#{channel.id}</span>
                  <span>
                    <strong>{channel.topic || "Unnamed topic"}</strong>
                    <small>
                      {channel.decoder === "json" ? (
                        <Braces size={12} />
                      ) : (
                        <FileCode2 size={12} />
                      )}{" "}
                      {channel.message_encoding || "Unknown encoding"} · schema
                      #{channel.schema_id}
                    </small>
                  </span>
                </span>
                <span className="mono">
                  {channel.message_count.toLocaleString()}
                </span>
                <span className="mono">
                  {rate ?? "—"}
                  {rate ? <small> Hz</small> : null}
                </span>
                <span className="recording-channel-span">
                  {hasBounds ? (
                    <>
                      <span className="recording-channel-span-track">
                        <i
                          className={channel.decoder}
                          style={{
                            left: `${left}%`,
                            width: `${Math.max(0.7, right - left)}%`,
                            maxWidth: `${100 - left}%`,
                          }}
                        />
                        {channel.start_time_ns === channel.end_time_ns && (
                          <b style={{ left: `${left}%` }} />
                        )}
                      </span>
                      <small>
                        {secondsFromNanoseconds(
                          BigInt(channel.start_time_ns!) - start,
                          6,
                        )}{" "}
                        →{" "}
                        {secondsFromNanoseconds(
                          BigInt(channel.end_time_ns!) - start,
                          6,
                        )}{" "}
                        s
                      </small>
                    </>
                  ) : (
                    <span className="recording-channel-empty">No messages</span>
                  )}
                </span>
              </button>
            );
          })}
          {!channels.length && (
            <p className="recording-empty-inline">
              No channels were defined in this container.
            </p>
          )}
        </div>
      </div>
      <details className="recording-span-explanation">
        <summary>Message bounds & mean rate · interpretation</summary>
        <p>
          Bars show first/last message bounds, not continuous coverage. Mean
          rate = (message count − 1) / log-time span; it is not a configured
          sensor rate.
        </p>
      </details>
    </section>
  );
}
