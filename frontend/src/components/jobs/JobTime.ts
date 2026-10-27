import { useEffect, useState } from "react";

export function useJobClock() {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  // A fetch can resolve between clock ticks; do not label this browser's own
  // newer observation as a clock disagreement during that sub-second interval.
  return Math.max(now, Date.now());
}

export function elapsedTime(start: string | null, end: string | number | null) {
  if (!start || end === null) return "—";
  const milliseconds =
    (typeof end === "number" ? end : Date.parse(end)) - Date.parse(start);
  if (!Number.isFinite(milliseconds)) return "—";
  if (milliseconds < 0) return "Clock difference";
  const seconds = Math.floor(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  if (seconds < 86400)
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

export function preciseJobTime(value: string | null | undefined) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isFinite(date.valueOf()) ? date.toISOString() : value;
}
