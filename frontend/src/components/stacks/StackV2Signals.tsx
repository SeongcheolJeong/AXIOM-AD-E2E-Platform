import type { StackOutput } from "./StackTypes";
import { validateStackOutputV2 } from "./StackV2Data";
import "./stack-v2.css";
type Sample = { index: number; output: StackOutput | null };
const fmt = (value: number) => String(Number(value.toFixed(4)));
export default function StackV2Signals({
  samples,
  selected,
  onSelect,
}: {
  samples: Sample[];
  selected: number;
  onSelect: (index: number) => void;
}) {
  if (!samples.some((item) => item.output?.kernel_version === "stack-kernel/2"))
    return null;
  const values = samples.map((sample) => {
    try {
      return {
        index: sample.index,
        output: validateStackOutputV2(sample.output),
      };
    } catch {
      return { index: sample.index, output: null };
    }
  });
  const charts = [
    {
      name: "Stopping envelope across stored ticks",
      unit: "m",
      series: [
        {
          name: "Available after reserve",
          color: "#527c70",
          values: values.map(
            (item) =>
              item.output?.decision.available_stopping_distance_m ?? null,
          ),
        },
        {
          name: "Current stopping need",
          color: "#bd8f4a",
          values: values.map(
            (item) => item.output?.decision.current_stopping_distance_m ?? null,
          ),
        },
        {
          name: "Planned stopping need",
          color: "#9a79aa",
          values: values.map(
            (item) => item.output?.decision.planned_stopping_distance_m ?? null,
          ),
        },
      ],
    },
    {
      name: "Requested and observed acceleration",
      unit: "m/s²",
      series: [
        {
          name: "Requested acceleration",
          color: "#577f98",
          values: values.map(
            (item) => item.output?.decision.target_acceleration_mps2 ?? null,
          ),
        },
        {
          name: "Observed speed difference / dt",
          color: "#b4745f",
          values: values.map(
            (item) =>
              item.output?.decision.observed_speed_acceleration_mps2 ?? null,
          ),
        },
      ],
    },
  ];
  return (
    <section className="stack-v2-signals" data-testid="stack-v2-signals">
      <h3>Stored plan and state trace</h3>
      <p>
        Every position belongs to the original sequence. Missing or rejected
        outputs stay gaps; selecting a position does not recalculate a stateful
        plan.
      </p>
      <div className="stack-v2-signal-grid">
        {charts.map((chart) => {
          const numbers = chart.series.flatMap((series) =>
            series.values.flatMap((value) => (value === null ? [] : [value])),
          );
          const min = Math.min(0, ...numbers),
            max = Math.max(1, ...numbers),
            range = max - min;
          const x = (index: number) =>
            58 + (index / Math.max(1, values.length - 1)) * 495;
          const y = (value: number) => 143 - ((value - min) / range) * 115;
          return (
            <figure key={chart.name}>
              <figcaption>
                {chart.name} · {chart.unit}
              </figcaption>
              <svg viewBox="0 0 580 175" role="img" aria-label={chart.name}>
                <line x1="58" x2="553" y1={y(0)} y2={y(0)} stroke="#d1dacf" />
                <text x="4" y="33">
                  {fmt(max)}
                </text>
                <text x="4" y="147">
                  {fmt(min)}
                </text>
                <text x="58" y="167">
                  Sequence {values[0]?.index}
                </text>
                <text x="553" y="167" textAnchor="end">
                  {values[values.length - 1]?.index}
                </text>
                <line
                  x1={x(values.findIndex((item) => item.index === selected))}
                  x2={x(values.findIndex((item) => item.index === selected))}
                  y1="20"
                  y2="148"
                  stroke="#aeb7b1"
                  strokeDasharray="3 3"
                />
                {chart.series.map((series) => {
                  let connected = false,
                    path = "";
                  series.values.forEach((value, index) => {
                    if (value === null) {
                      connected = false;
                      return;
                    }
                    path += `${connected ? "L" : "M"}${x(index)},${y(value)} `;
                    connected = true;
                  });
                  return (
                    <g
                      key={series.name}
                      data-series={series.name}
                      data-valid-points={
                        series.values.filter((value) => value !== null).length
                      }
                    >
                      <path
                        d={path}
                        stroke={series.color}
                        strokeWidth="2"
                        fill="none"
                      />
                      {series.values.map((value, index) =>
                        value === null ? null : (
                          <circle
                            key={index}
                            cx={x(index)}
                            cy={y(value)}
                            r={values[index].index === selected ? 4 : 2}
                            fill={series.color}
                          />
                        ),
                      )}
                    </g>
                  );
                })}
              </svg>
              <div className="stack-plan-phases">
                {chart.series.map((series) => (
                  <span key={series.name} style={{ color: series.color }}>
                    {series.name}
                  </span>
                ))}
              </div>
            </figure>
          );
        })}
      </div>
      <div
        className="stack-v2-state-strip"
        aria-label="Stored kernel 2 state sequence"
      >
        {values.map((item) => (
          <button
            type="button"
            key={item.index}
            aria-label={`Inspect plan state at sequence ${item.index}`}
            aria-pressed={selected === item.index}
            className={item.output?.state_after.mode ?? "unavailable"}
            onClick={() => onSelect(item.index)}
            title={
              item.output
                ? `${item.output.state_before.mode} → ${item.output.state_after.mode}; ${item.output.state_after.latch_reason ?? "no latch"}`
                : "No valid kernel 2 output"
            }
          >
            <span>{item.index}</span>
            <small>
              {item.output?.state_after.mode.replaceAll("_", " ") ?? "gap"}
            </small>
          </button>
        ))}
      </div>
    </section>
  );
}
