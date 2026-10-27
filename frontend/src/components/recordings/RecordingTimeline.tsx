import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Clock3, SkipBack, SkipForward } from "lucide-react";
import { CopyButton } from "../ui";
import {
  MAX_TIMESTAMP_NS,
  TIMELINE_STEPS,
  nanosecondsFromSeconds,
  secondsFromNanoseconds,
  timeAtPosition,
  timelinePosition,
} from "./RecordingTime";

export default function RecordingTimeline({
  start,
  end,
  cursor,
  onCursor,
}: {
  start: bigint;
  end: bigint;
  cursor: string;
  onCursor: (time: string) => void;
}) {
  const [offsetInput, setOffsetInput] = useState(
    secondsFromNanoseconds(BigInt(cursor) - start),
  );
  const [error, setError] = useState("");
  useEffect(() => {
    setOffsetInput(secondsFromNanoseconds(BigInt(cursor) - start));
  }, [cursor, start]);
  const current = BigInt(cursor);
  const afterEnd = current > end;
  const beforeStart = current < start;
  function seek(next: bigint) {
    onCursor(next.toString());
    setOffsetInput(secondsFromNanoseconds(next - start));
    setError("");
  }
  function preciseSeek(event: FormEvent) {
    event.preventDefault();
    try {
      const next = start + nanosecondsFromSeconds(offsetInput);
      if (next < 0n || next > MAX_TIMESTAMP_NS)
        throw new Error(
          "The resulting timestamp must remain within the unsigned 64-bit nanosecond range.",
        );
      seek(next);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  return (
    <section
      className="panel recording-timeline"
      aria-label="Recording log-time cursor"
    >
      <div className="recording-section-heading">
        <div>
          <h3>
            <Clock3 size={17} />
            Log-time cursor
          </h3>
          <p>Seek stored messages without altering their original clocks.</p>
        </div>
        <span className="recording-span-label">
          Span {secondsFromNanoseconds(end - start)} s
        </span>
      </div>
      <div className="recording-timeline-track">
        <input
          type="range"
          aria-label="Recording time cursor"
          aria-valuetext={`${secondsFromNanoseconds(current - start)} seconds from recording start; ${cursor} nanoseconds`}
          min={0}
          max={TIMELINE_STEPS}
          step={1}
          value={timelinePosition(current, start, end)}
          disabled={end <= start}
          onChange={(event) =>
            seek(timeAtPosition(Number(event.target.value), start, end))
          }
        />
        <div className="recording-timeline-ticks">
          {[0, 1, 2, 3, 4].map((tick) => (
            <span key={tick}>
              {secondsFromNanoseconds(((end - start) * BigInt(tick)) / 4n, 6)} s
            </span>
          ))}
        </div>
      </div>
      <div className="recording-cursor-controls">
        <button
          type="button"
          className="button secondary small"
          aria-label="Go to recording start"
          onClick={() => seek(start)}
        >
          <SkipBack size={14} />
          Start
        </button>
        <form onSubmit={preciseSeek}>
          <label>
            Cursor offset (seconds)
            <input
              value={offsetInput}
              inputMode="decimal"
              onChange={(event) => setOffsetInput(event.target.value)}
              aria-describedby="recording-seek-help"
            />
          </label>
          <button className="button secondary small" type="submit">
            Seek
            <ArrowRight size={14} />
          </button>
        </form>
        <button
          type="button"
          className="button secondary small"
          aria-label="Go to recording end"
          onClick={() => seek(end)}
        >
          End
          <SkipForward size={14} />
        </button>
      </div>
      <div className="recording-exact-time">
        <div>
          <span>EXACT LOG TIME · NANOSECONDS</span>
          <code data-testid="recording-cursor-ns">{cursor}</code>
        </div>
        <CopyButton text={cursor} label="Copy timestamp" />
      </div>
      <p className="recording-timeline-help" id="recording-seek-help">
        Offsets accept up to 9 decimal places. The slider samples the span;
        precise seeks preserve every nanosecond.
        {afterEnd
          ? " This cursor is after the recording end."
          : beforeStart
            ? " This cursor is before the recording start."
            : ""}
      </p>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
