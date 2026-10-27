import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { StackReplayTick } from "./StackReplayTypes";
import { secondsFromNanoseconds } from "../recordings/RecordingTime";
export default function StackTraceSignals({
  ticks,
  selected,
  onSelect,
}: {
  ticks: StackReplayTick[];
  selected: number;
  onSelect: (index: number) => void;
}) {
  const origin = ticks[0]?.input.at_ns;
  if (!origin) return null;
  const data = ticks.map((tick) => ({
    index: tick.index,
    time: Number(BigInt(tick.input.at_ns) - BigInt(origin)) / 1e9,
    exact: tick.input.at_ns,
    speed: tick.packet?.ego?.speed_mps ?? null,
    target: tick.output?.decision.target_speed_mps ?? null,
    distance:
      tick.output?.kernel_version === "stack-kernel/2"
        ? (tick.output.observation.positive_surface?.distance_from_bumper_m ??
          null)
        : (tick.output?.observation.distance_m ?? null),
    throttle: tick.output?.control.throttle ?? null,
    brake: tick.output?.control.brake ?? null,
    steer: tick.output?.control.steer ?? null,
  }));
  const cursor = data.find((item) => item.index === selected)?.time;
  return (
    <section className="panel stack-signals" data-testid="stack-trace-signals">
      <div className="stack-section-heading">
        <div>
          <h3>Stored tick signals</h3>
          <p>
            No interpolation across absent stage output. Click a sampled tick to
            inspect its evidence.
          </p>
        </div>
        <span>{ticks.length} tick positions</span>
      </div>
      <div className="stack-signal-grid">
        {(["motion", "commands"] as const).map((kind) => (
          <div className="stack-signal-chart" key={kind}>
            <h4>
              {kind === "motion"
                ? "Ego telemetry & policy"
                : "Requested actuator commands · not applied"}
            </h4>
            <ResponsiveContainer width="100%" height={190}>
              <LineChart
                data={data}
                margin={{ top: 10, right: 12, left: 0, bottom: 12 }}
                onClick={(state) => {
                  const index =
                    state.activeTooltipIndex === null
                      ? NaN
                      : Number(state.activeTooltipIndex);
                  if (Number.isInteger(index) && data[index])
                    onSelect(data[index].index);
                }}
              >
                <CartesianGrid stroke="#dce7d7" strokeDasharray="3 4" />
                <XAxis
                  dataKey="time"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tick={{ fontSize: 11, fill: "#667f65" }}
                  tickFormatter={(value) => `${Number(value).toFixed(2)}s`}
                />
                <YAxis
                  yAxisId="left"
                  domain={kind === "motion" ? [0, "auto"] : [-1, 1]}
                  width={42}
                  tick={{ fontSize: 11, fill: "#667f65" }}
                />
                {kind === "motion" && (
                  <YAxis
                    yAxisId="distance"
                    orientation="right"
                    width={42}
                    tick={{ fontSize: 11, fill: "#967351" }}
                  />
                )}
                <Tooltip
                  labelFormatter={(_, payload) => {
                    const sample = payload?.[0]?.payload as
                      (typeof data)[number] | undefined;
                    return sample
                      ? `+${secondsFromNanoseconds(BigInt(sample.exact) - BigInt(origin))} s · ${sample.exact} ns`
                      : "";
                  }}
                  formatter={(value) =>
                    typeof value === "number"
                      ? Number(value.toFixed(4))
                      : "unavailable"
                  }
                />
                {cursor !== undefined && (
                  <ReferenceLine
                    yAxisId="left"
                    x={cursor}
                    stroke="#688a70"
                    strokeDasharray="3 3"
                  />
                )}
                {kind === "motion" ? (
                  <>
                    <Line
                      yAxisId="left"
                      dataKey="speed"
                      name="Recorded ego speed (m/s)"
                      stroke="#6792a1"
                      strokeWidth={2}
                      type="stepAfter"
                      dot={{ r: 2 }}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                    <Line
                      yAxisId="left"
                      dataKey="target"
                      name="Target speed (m/s)"
                      stroke="#4d8d64"
                      strokeWidth={2}
                      type="stepAfter"
                      dot={{ r: 2 }}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                    <Line
                      yAxisId="distance"
                      dataKey="distance"
                      name="Observed surface distance (m)"
                      stroke="#bd9059"
                      strokeWidth={1.5}
                      dot={{ r: 2 }}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                  </>
                ) : (
                  <>
                    <Line
                      yAxisId="left"
                      dataKey="throttle"
                      name="Requested throttle"
                      stroke="#59906b"
                      strokeWidth={2}
                      type="stepAfter"
                      dot={{ r: 2 }}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                    <Line
                      yAxisId="left"
                      dataKey="brake"
                      name="Requested brake"
                      stroke="#c88159"
                      strokeWidth={2}
                      type="stepAfter"
                      dot={{ r: 2 }}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                    <Line
                      yAxisId="left"
                      dataKey="steer"
                      name="Requested steer (+left)"
                      stroke="#8c80b1"
                      strokeWidth={1.5}
                      type="stepAfter"
                      dot={{ r: 2 }}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                  </>
                )}
              </LineChart>
            </ResponsiveContainer>
            <div className="stack-chart-legend">
              {kind === "motion" ? (
                <>
                  <span className="ego">Recorded speed · m/s</span>
                  <span className="target">Target speed · m/s</span>
                  <span className="distance">Surface distance · m</span>
                </>
              ) : (
                <>
                  <span className="target">Throttle [0,1]</span>
                  <span className="distance">Brake [0,1]</span>
                  <span className="steer">Steer [−1,1]</span>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
