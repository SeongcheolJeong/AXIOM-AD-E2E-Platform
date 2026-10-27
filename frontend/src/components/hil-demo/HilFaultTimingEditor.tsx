import type { HilFaultTiming } from "./demo-contract";
import {
  hilEditTiming,
  hilTimingError,
  hilTimingTargetLabel,
  hilTimingWindow,
} from "./fault-timing";

export default function HilFaultTimingEditor({
  timing,
  parameters,
  disabled,
  appliesToBoth = false,
  onChange,
}: {
  timing: HilFaultTiming;
  parameters: Record<string, number>;
  disabled: boolean;
  appliesToBoth?: boolean;
  onChange: (parameters: Record<string, number>) => void;
}) {
  const { start, duration, end } = hilTimingWindow(timing, parameters),
    error = hilTimingError(timing, parameters);
  return (
    <section className="hil-fault-timing" aria-label="고장 입력 시각 편집">
      <div>
        <strong>고장 주입 구간</strong>
        <small>{hilTimingTargetLabel(appliesToBoth)}</small>
      </div>
      <div className="hil-timing-inputs">
        <label>
          시작 · 초
          <input
            aria-label="HIL 고장 시작 시각"
            type="number"
            min={timing.start_min_s}
            max={timing.start_max_s}
            step={timing.step_s}
            value={Number.isFinite(start) ? start : ""}
            disabled={disabled}
            onChange={(event) =>
              onChange(
                hilEditTiming(
                  timing,
                  parameters,
                  "start",
                  event.target.value === "" ? NaN : Number(event.target.value),
                ),
              )
            }
          />
        </label>
        <label>
          종료 · 초
          <input
            aria-label="HIL 고장 종료 시각"
            type="number"
            min={
              Number.isFinite(start)
                ? start + timing.duration_min_s
                : timing.start_min_s + timing.duration_min_s
            }
            max={timing.end_max_s}
            step={timing.step_s}
            value={Number.isFinite(end) ? end : ""}
            disabled={disabled}
            onChange={(event) =>
              onChange(
                hilEditTiming(
                  timing,
                  parameters,
                  "end",
                  event.target.value === "" ? NaN : Number(event.target.value),
                ),
              )
            }
          />
        </label>
      </div>
      <div
        className="hil-timing-track"
        role="img"
        aria-label={
          error
            ? "작성 고장 구간 오류"
            : `작성 고장 입력 구간 ${start}초부터 ${end}초까지`
        }
      >
        <span className="hil-timing-zero">0 s</span>
        {!error && (
          <i
            style={{
              left: `${(start / 18) * 100}%`,
              width: `${(duration / 18) * 100}%`,
            }}
          />
        )}
        <span className="hil-timing-last">18 s</span>
      </div>
      {error ? (
        <p className="hil-error" role="alert">
          {error}
        </p>
      ) : (
        <small>
          {start.toFixed(1)}–{end.toFixed(1)} s · 지속 {duration.toFixed(1)} s
        </small>
      )}
      <small>
        시작을 바꾸면 지속 시간이 유지됩니다. {timing.step_s}초 단위 · 종료 ≤{" "}
        {timing.end_max_s}초
      </small>
    </section>
  );
}
