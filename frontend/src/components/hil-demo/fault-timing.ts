import type { HilFaultTiming } from "./demo-contract.ts";
export const hilTimingTargetLabel = (appliesToBoth: boolean) =>
  `입력 시각 · 18초 중 ${appliesToBoth ? "A/B에 동일 적용" : "B에만 적용"}`;

export function hilTimingWindow(
  timing: HilFaultTiming,
  parameters: Record<string, number>,
) {
  const start = parameters.start_s ?? timing.default_start_s;
  const duration = parameters.duration_s ?? timing.default_duration_s;
  return { start, duration, end: start + duration };
}

export function hilTimingError(
  timing: HilFaultTiming,
  parameters: Record<string, number>,
) {
  const { start, duration, end } = hilTimingWindow(timing, parameters);
  if (![start, duration, end].every(Number.isFinite))
    return "고장 시작과 종료 시각을 입력하세요.";
  if (start < timing.start_min_s || start > timing.start_max_s)
    return `시작은 ${timing.start_min_s}–${timing.start_max_s}초 범위여야 합니다.`;
  if (duration < timing.duration_min_s || duration > timing.duration_max_s)
    return `종료는 시작보다 ${timing.duration_min_s}–${timing.duration_max_s}초 뒤여야 합니다.`;
  if (end > timing.end_max_s)
    return `고장은 ${timing.end_max_s}초 이내에 종료해야 합니다.`;
  if (
    [start, duration].some((value) => !Number.isInteger(value / timing.step_s))
  )
    return `시각은 ${timing.step_s}초 단위로 입력하세요.`;
  return "";
}

export function hilEditTiming(
  timing: HilFaultTiming,
  parameters: Record<string, number>,
  field: "start" | "end",
  value: number,
): Record<string, number> {
  return field === "start"
    ? { ...parameters, start_s: value }
    : {
        ...parameters,
        duration_s: value - hilTimingWindow(timing, parameters).start,
      };
}
