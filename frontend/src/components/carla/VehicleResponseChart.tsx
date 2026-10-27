export type ResponseSeries = {
  name: string;
  color: string;
  values: (number | null)[];
  dashed?: boolean;
};

export const responseNumber = (value: number | null | undefined) =>
  value == null
    ? "—"
    : Math.abs(value) >= 10000
      ? value.toExponential(3)
      : value.toFixed(3);

/** Every dot is a recorded frame. Null observations break the path. */
export default function VehicleResponseChart({
  title,
  unit,
  times,
  frames,
  series,
  selected,
  onSelect,
}: {
  title: string;
  unit: string;
  times: number[];
  frames: number[];
  series: ResponseSeries[];
  selected: number;
  onSelect: (index: number) => void;
}) {
  const values = series.flatMap((item) =>
    item.values.filter((value): value is number => value !== null),
  );
  const extent = Math.max(0.001, ...values.map(Math.abs));
  const signed = values.some((value) => value < 0);
  const minimum = times[0] ?? 0;
  const span = Math.max(0.001, (times.at(-1) ?? minimum) - minimum);
  // Leave room for the minus sign and the longest formatted scientific value.
  // All plot/axis coordinates share these bounds so widening labels cannot
  // misalign samples, the cursor, zero line or the simulation-time labels.
  const plotLeft = 126,
    plotRight = 655,
    labelRight = plotLeft - 10;
  const x = (index: number) =>
    plotLeft + ((times[index] - minimum) / span) * (plotRight - plotLeft);
  const y = (value: number) =>
    signed ? 98 - (value / extent) * 70 : 168 - (value / extent) * 140;
  return (
    <figure className="vehicle-response-chart">
      <figcaption>
        <strong>{title}</strong> <span>{unit}</span>
      </figcaption>
      <div
        className="vehicle-response-chart-scroll"
        tabIndex={0}
        aria-label={`Scroll ${title} chart`}
      >
        <svg
          viewBox="0 0 680 212"
          role="img"
          aria-label={`${title} by simulation time`}
        >
          <title>
            {title}: original frame samples; null values remain gaps
          </title>
          <line
            x1={plotLeft}
            x2={plotRight}
            y1="168"
            y2="168"
            className="response-axis"
          />
          <line
            x1={plotLeft}
            x2={plotLeft}
            y1="22"
            y2="168"
            className="response-axis"
          />
          {signed && (
            <line
              x1={plotLeft}
              x2={plotRight}
              y1={y(0)}
              y2={y(0)}
              className="response-zero"
            />
          )}
          <text x={labelRight} y="29" textAnchor="end" data-axis="y-max">
            {responseNumber(extent)}
          </text>
          <text x={labelRight} y="171" textAnchor="end" data-axis="y-min">
            {signed ? responseNumber(-extent) : "0"}
          </text>
          <text x={plotLeft} y="193">
            {responseNumber(minimum)} s
          </text>
          <text x={plotRight} y="193" textAnchor="end">
            {responseNumber(times.at(-1))} s
          </text>
          {times[selected] !== undefined && (
            <line
              x1={x(selected)}
              x2={x(selected)}
              y1="22"
              y2="168"
              className="response-cursor"
            />
          )}
          {series.map((item) => {
            let drawing = false;
            const path = item.values
              .map((value, index) => {
                if (value === null) {
                  drawing = false;
                  return "";
                }
                const instruction = `${drawing ? "L" : "M"}${x(index)},${y(value)}`;
                drawing = true;
                return instruction;
              })
              .join(" ");
            return (
              <g
                key={item.name}
                data-series={item.name}
                data-valid-points={
                  item.values.filter((value) => value !== null).length
                }
              >
                <path
                  d={path}
                  fill="none"
                  stroke={item.color}
                  strokeWidth="1.7"
                  strokeDasharray={item.dashed ? "5 4" : undefined}
                />
                {item.values.map((value, index) =>
                  value === null ? null : (
                    <circle
                      key={index}
                      cx={x(index)}
                      cy={y(value)}
                      r={index === selected ? 4 : 2.4}
                      fill={item.color}
                      onClick={() => onSelect(index)}
                    >
                      <title>
                        {item.name} · frame {frames[index]} ·{" "}
                        {responseNumber(times[index])} s:{" "}
                        {responseNumber(value)} {unit}
                      </title>
                    </circle>
                  ),
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <div className="vehicle-response-legend">
        {series.map((item) => (
          <span key={item.name}>
            <i style={{ background: item.color }} />
            {item.name}
          </span>
        ))}
      </div>
      <p className="vehicle-response-scroll-hint">
        Scroll the chart horizontally to inspect its full time axis.
      </p>
    </figure>
  );
}
