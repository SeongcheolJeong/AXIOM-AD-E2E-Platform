import type { RecordingChannel } from "./RecordingTypes";

export const NANOSECONDS_PER_SECOND = 1_000_000_000n;
export const MAX_TIMESTAMP_NS = 18_446_744_073_709_551_615n;
export const TIMELINE_STEPS = 1_000_000;

/** Convert only integer differences, never uint64 absolute times, to display text. */
export function secondsFromNanoseconds(value: bigint, digits = 9): string {
  const precision = Math.max(0, Math.min(9, digits));
  const negative = value < 0n;
  const absolute = negative ? -value : value;
  const whole = absolute / NANOSECONDS_PER_SECOND;
  const fraction = (absolute % NANOSECONDS_PER_SECOND)
    .toString()
    .padStart(9, "0")
    .slice(0, precision)
    .replace(/0+$/, "");
  return `${negative ? "−" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

/** Parse decimal seconds without a Number/float round trip. */
export function nanosecondsFromSeconds(value: string): bigint {
  const normalized = value.trim().replace(/^−/, "-");
  const match = /^([+-]?)(\d+)(?:\.(\d{0,9}))?$/.exec(normalized);
  if (!match)
    throw new Error("Enter decimal seconds with up to 9 fractional digits.");
  const magnitude =
    BigInt(match[2]) * NANOSECONDS_PER_SECOND +
    BigInt((match[3] ?? "").padEnd(9, "0"));
  return match[1] === "-" ? -magnitude : magnitude;
}

export function recordingDurationNs(
  start: string | null,
  end: string | null,
): bigint {
  if (start === null || end === null) return 0n;
  const duration = BigInt(end) - BigInt(start);
  return duration > 0n ? duration : 0n;
}

/** Only this dimensionless, bounded ratio passes through Number for CSS/range controls. */
export function timelinePosition(
  time: bigint,
  start: bigint,
  end: bigint,
): number {
  if (end <= start) return 0;
  const bounded = time < start ? start : time > end ? end : time;
  return Number(((bounded - start) * BigInt(TIMELINE_STEPS)) / (end - start));
}

export function timeAtPosition(
  position: number,
  start: bigint,
  end: bigint,
): bigint {
  if (end <= start) return start;
  const step = BigInt(
    Math.max(0, Math.min(TIMELINE_STEPS, Math.round(position))),
  );
  return start + ((end - start) * step) / BigInt(TIMELINE_STEPS);
}

export function channelMeanRate(channel: RecordingChannel): string | null {
  const span = recordingDurationNs(channel.start_time_ns, channel.end_time_ns);
  if (channel.message_count < 2 || span === 0n) return null;
  const scaled =
    ((BigInt(channel.message_count) - 1n) * NANOSECONDS_PER_SECOND * 1000n) /
    span;
  if (scaled === 0n) return "<0.001";
  const whole = scaled / 1000n;
  const fraction = (scaled % 1000n)
    .toString()
    .padStart(3, "0")
    .replace(/0+$/, "");
  return `${whole}${fraction ? `.${fraction}` : ""}`;
}

export function relativeSampleTime(
  logTime: string,
  cursor: string,
): { kind: "at_cursor" | "earlier" | "future"; delta: bigint; label: string } {
  const delta = BigInt(logTime) - BigInt(cursor);
  if (delta === 0n) return { kind: "at_cursor", delta, label: "At cursor" };
  if (delta > 0n)
    return {
      kind: "future",
      delta,
      label: `Future sample · +${secondsFromNanoseconds(delta)} s`,
    };
  return {
    kind: "earlier",
    delta,
    label: `Earlier sample · age ${secondsFromNanoseconds(-delta)} s`,
  };
}
