/** A bookmarked moment is an initial cursor, never a change to the run's inputs. */
export function demoMoment(hash: string): number | null {
  const raw = new URLSearchParams(hash.split("?")[1] ?? "").get("time_s");
  if (raw === null || !raw.trim()) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function boundedDemoMoment(requested: number | null, first: number, last: number): number {
  return Math.max(first, Math.min(last, requested ?? first));
}

export function demoMomentIndex(times: readonly number[], requested: number | null): number {
  if (!times.length || requested === null) return 0;
  const target = boundedDemoMoment(requested, times[0], times[times.length - 1]);
  let best = 0;
  for (let i = 1; i < times.length; i++) {
    if (Math.abs(times[i] - target) < Math.abs(times[best] - target)) best = i;
  }
  return best;
}

export function demoGalleryReturn(hash: string): string | null {
  const query = new URLSearchParams(hash.split("?")[1] ?? "");
  const project = query.get("project"), caseId = query.get("demo_case");
  if (!project || !caseId) return null;
  return `#workspace?${new URLSearchParams({ project, demo_case: caseId })}`;
}
