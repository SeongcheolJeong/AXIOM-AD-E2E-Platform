/** Deadlines govern when one fixed source step may run; they never choose source time. */
export type PlaybackDeadline = { key: string; dueMs: number };
export const PLAYBACK_STEP_MS = 50;
export const PLAYBACK_REBASE_MS = 100;

export function playbackDeadline(
  previous: PlaybackDeadline | null,
  nowMs: number,
  key: string,
): PlaybackDeadline {
  if (
    !previous ||
    previous.key !== key ||
    nowMs - previous.dueMs > PLAYBACK_REBASE_MS
  )
    return { key, dueMs: nowMs + PLAYBACK_STEP_MS };
  return previous;
}

/** A late callback still grants only one step, then discards excessive backlog. */
export function followingPlaybackDeadline(
  current: PlaybackDeadline,
  nowMs: number,
): PlaybackDeadline {
  return {
    key: current.key,
    dueMs:
      nowMs - current.dueMs > PLAYBACK_REBASE_MS
        ? nowMs + PLAYBACK_STEP_MS
        : current.dueMs + PLAYBACK_STEP_MS,
  };
}
