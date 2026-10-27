import { useState } from "react";
import { Activity } from "lucide-react";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { secondsFromNanoseconds } from "./RecordingTime";
import type { RecordingSample } from "./RecordingTypes";

function numericFields(sample: RecordingSample): Record<string, number> {
  if (
    sample.decode_status !== "decoded" ||
    sample.decoded === null ||
    typeof sample.decoded !== "object" ||
    Array.isArray(sample.decoded)
  )
    return {};
  return Object.fromEntries(
    Object.entries(sample.decoded).filter(
      (entry): entry is [string, number] =>
        typeof entry[1] === "number" &&
        Number.isFinite(entry[1]) &&
        (!Number.isInteger(entry[1]) || Number.isSafeInteger(entry[1])),
    ),
  );
}

export default function RecordingSignalChart({
  samples,
  origin,
}: {
  samples: RecordingSample[];
  origin: bigint;
}) {
  const [selectedField, setSelectedField] = useState("");
  const fields = [
    ...new Set(samples.flatMap((sample) => Object.keys(numericFields(sample)))),
  ];
  const field = fields.includes(selectedField) ? selectedField : fields[0];
  if (!field) return null;
  const points = samples.flatMap((sample) => {
    const value = numericFields(sample)[field];
    if (value === undefined) return [];
    const offset = BigInt(sample.log_time_ns) - origin;
    return [
      {
        relative_s: Number(offset) / 1_000_000_000,
        exact_offset_s: secondsFromNanoseconds(offset),
        value,
        ordinal: sample.ordinal,
        time_ns: sample.log_time_ns,
      },
    ];
  });
  return (
    <section
      className="recording-signal-chart"
      aria-label="Actual sample window signal chart"
    >
      <div>
        <h4>
          <Activity size={15} />
          Sample window signal
        </h4>
        <label>
          Numeric signal
          <select
            aria-label="Numeric signal"
            value={field}
            onChange={(event) => setSelectedField(event.target.value)}
          >
            {fields.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p>
        {points.length} recorded points in this returned window. No line
        interpolates missing samples; units are not inferred from field names.
      </p>
      <ResponsiveContainer width="100%" height={185}>
        <ScatterChart margin={{ top: 12, right: 20, bottom: 10, left: -10 }}>
          <CartesianGrid stroke="#e2e9db" strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="relative_s"
            domain={["dataMin", "dataMax"]}
            name="Relative log time"
            tick={{ fontSize: 11, fill: "#617155" }}
            tickFormatter={(value) =>
              `${Number(value)
                .toFixed(3)
                .replace(/\.?0+$/, "")}s`
            }
            tickLine={false}
            axisLine={{ stroke: "#d6e0ce" }}
            minTickGap={30}
          />
          <YAxis
            type="number"
            dataKey="value"
            name={field}
            domain={["auto", "auto"]}
            tick={{ fontSize: 11, fill: "#617155" }}
            tickLine={false}
            axisLine={false}
            width={60}
          />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            content={({ active, payload }) => {
              const point = payload?.[0]?.payload as
                (typeof points)[number] | undefined;
              return active && point ? (
                <div className="recording-chart-tooltip">
                  <strong>
                    {field}: {String(point.value)}
                  </strong>
                  <span>
                    {point.exact_offset_s} s · ordinal {point.ordinal}
                  </span>
                  <code>{point.time_ns} ns</code>
                </div>
              ) : null;
            }}
          />
          <Scatter
            data={points}
            name={field}
            fill="#779367"
            isAnimationActive={false}
          />
        </ScatterChart>
      </ResponsiveContainer>
      <span className="recording-chart-axis-note">
        X: seconds from recording start · finite, JavaScript-safe numeric fields
      </span>
    </section>
  );
}
