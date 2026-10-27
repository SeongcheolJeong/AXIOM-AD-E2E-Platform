/** Retained simulation timestamps only: never interpolate or extrapolate a pose. */
const timestampNoiseToleranceS = 1e-9;
const atOrBefore = (sampleTime: number, requestedTime: number) =>
  sampleTime <= requestedTime ||
  Math.abs(sampleTime - requestedTime) <= timestampNoiseToleranceS;
export function retainedFrameIndex(
  frames: { time_s: number }[],
  timeS: number,
): number {
  if (
    !Number.isFinite(timeS) ||
    !frames.length ||
    !atOrBefore(frames[0].time_s, timeS)
  )
    return -1;
  let lo = 0,
    hi = frames.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (atOrBefore(frames[mid].time_s, timeS)) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

export function playbackBoundary(
  frames: { time_s: number }[],
  timeS: number,
): "before" | "active" | "ended" | "empty" {
  if (!frames.length) return "empty";
  if (!atOrBefore(frames[0].time_s, timeS)) return "before";
  if (!atOrBefore(timeS, frames[frames.length - 1].time_s)) return "ended";
  return "active";
}
