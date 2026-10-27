import { useId, useState } from "react";
import type { CorridorObservationV2 } from "./StackV2Types";
const number = (value: number | null | undefined) =>
  value == null ? "Unavailable" : String(Number(value.toFixed(5)));
const vector = (value: number[] | null) =>
  value ? value.map(number).join(", ") + " m" : "No retained hit extents";

export default function StackCoverageGrid({
  observation,
}: {
  observation: CorridorObservationV2;
}) {
  const pattern = useId().replaceAll(":", "");
  const [selection, setSelection] = useState<string>("");
  const coverage = observation.coverage,
    surface = observation.positive_surface;
  const cell =
    coverage.cells.find(
      (item) =>
        `${item.longitudinal_index}/${item.lateral_index}` === selection,
    ) ?? coverage.cells[0];
  const span = coverage.lookahead_x_m;
  const halfWidth = Math.max(
    coverage.lateral_cell_m * 1.5,
    ...coverage.cells.flatMap((item) => item.y_bounds_m.map(Math.abs)),
  );
  const x = (value: number) => 58 + (value / span) * 760;
  const y = (value: number) => 136 - (value / halfWidth) * 72;
  return (
    <section className="stack-grid-evidence" data-testid="stack-coverage-grid">
      <div className="stack-v2-heading">
        <h4>Retained ground-hit grid</h4>
        <strong>
          {coverage.qualified_cells} / {coverage.expected_cells} cells qualified
        </strong>
      </div>
      <p className="stack-stage-semantics">
        Counts are actual retained ground hits. Hatched cells remain unknown,
        including gaps before farther returns. Sample qualification does not
        certify free space.
      </p>
      <div className="stack-grid-legend">
        <span className="sample">Sample qualified</span>
        <span className="unknown">Unknown · no gap filling</span>
        <span className="positive">Positive height-band surface</span>
      </div>
      <div className="stack-v2-scroll">
        <svg
          viewBox="0 0 860 255"
          role="img"
          aria-label="Ground sample coverage in ego coordinates"
          className="stack-coverage-svg"
        >
          <defs>
            <pattern
              id={pattern}
              width="8"
              height="8"
              patternUnits="userSpaceOnUse"
            >
              <rect width="8" height="8" fill="#ede9df" />
              <path
                d="M-2 2L2-2M0 8L8 0M6 10L10 6"
                stroke="#aaa493"
                strokeWidth="1"
              />
            </pattern>
          </defs>
          <rect
            x={x(0)}
            y="57"
            width={x(span) - x(0)}
            height="158"
            fill="#f2f4ee"
          />
          {coverage.unknown_regions.map((region, index) => (
            <rect
              key={index}
              data-testid="stack-unknown-region"
              x={x(region.x_bounds_m[0])}
              y={y(region.y_bounds_m[1])}
              width={x(region.x_bounds_m[1]) - x(region.x_bounds_m[0])}
              height={y(region.y_bounds_m[0]) - y(region.y_bounds_m[1])}
              fill={`url(#${pattern})`}
            />
          ))}
          {coverage.cells.map((item) => {
            const id = `${item.longitudinal_index}/${item.lateral_index}`,
              chosen = item === cell;
            const label = `Grid cell ${id}, ${item.state}, ${item.ground_hits} ground hits`;
            return (
              <g key={id} data-cell-id={id} data-cell-state={item.state}>
                <rect
                  role="button"
                  tabIndex={0}
                  aria-label={label}
                  aria-pressed={chosen}
                  onClick={() => setSelection(id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelection(id);
                    }
                  }}
                  x={x(item.x_bounds_m[0])}
                  y={y(item.y_bounds_m[1])}
                  width={x(item.x_bounds_m[1]) - x(item.x_bounds_m[0])}
                  height={y(item.y_bounds_m[0]) - y(item.y_bounds_m[1])}
                  fill={
                    item.state === "qualified" ? "#c4d9ca" : `url(#${pattern})`
                  }
                  stroke={chosen ? "#223d45" : "#fff"}
                  strokeWidth={chosen ? 2.5 : 1.5}
                />
                <text
                  x={x((item.x_bounds_m[0] + item.x_bounds_m[1]) / 2)}
                  y={y((item.y_bounds_m[0] + item.y_bounds_m[1]) / 2) + 4}
                  textAnchor="middle"
                  pointerEvents="none"
                >
                  {item.ground_hits}
                </text>
              </g>
            );
          })}
          <line
            x1={x(coverage.ego_front_x_m)}
            x2={x(coverage.ego_front_x_m)}
            y1="40"
            y2="220"
            stroke="#52616a"
            strokeDasharray="3 3"
          />
          <text x={x(coverage.ego_front_x_m)} y="30" textAnchor="middle">
            Ego front {number(coverage.ego_front_x_m)} m
          </text>
          {coverage.qualified_prefix_m !== null && (
            <g data-testid="stack-qualified-prefix-marker">
              <line
                x1={x(coverage.ego_front_x_m + coverage.qualified_prefix_m)}
                x2={x(coverage.ego_front_x_m + coverage.qualified_prefix_m)}
                y1="52"
                y2="218"
                stroke="#2e7565"
                strokeWidth="3"
              />
            </g>
          )}
          {surface && (
            <g data-testid="stack-positive-surface-marker">
              <circle
                cx={x(surface.nearest_point_xyz_m[0])}
                cy={y(surface.nearest_point_xyz_m[1])}
                r="6"
                fill="#b74b3c"
                stroke="white"
                strokeWidth="2"
              />
              <title>{`Retained nearest point ${surface.nearest_point_xyz_m.join(", ")} m`}</title>
            </g>
          )}
          <text x="8" y="68">
            +y left
          </text>
          <text x="10" y="141">
            0
          </text>
          <text x="8" y="209">
            −y right
          </text>
          <text x="58" y="242">
            0 m
          </text>
          <text x="818" y="242" textAnchor="end">
            +x forward · {number(span)} m
          </text>
          {coverage.status === "invalid" && (
            <text
              x="440"
              y="142"
              textAnchor="middle"
              className="stack-grid-invalid"
            >
              Invalid source · no sample coverage is trusted
            </text>
          )}
        </svg>
      </div>
      <dl className="stack-v2-facts">
        <div>
          <dt>Qualified contiguous prefix from ego front</dt>
          <dd data-testid="stack-v2-qualified-prefix">
            {coverage.qualified_prefix_m === null
              ? "Unavailable"
              : `${number(coverage.qualified_prefix_m)} m`}
          </dd>
        </div>
        <div>
          <dt>Declared cell size / minimum ground hits</dt>
          <dd>
            {number(coverage.longitudinal_cell_m)} ×{" "}
            {number(coverage.lateral_cell_m)} m /{" "}
            {coverage.min_ground_hits_per_cell}
          </dd>
        </div>
      </dl>
      {cell && (
        <div
          data-testid="stack-selected-grid-cell"
          className="stack-grid-cell-detail"
        >
          <strong>
            Cell {cell.longitudinal_index}/{cell.lateral_index} · {cell.state} ·{" "}
            {cell.ground_hits} retained ground hits
          </strong>
          <dl className="stack-v2-facts">
            <div>
              <dt>Declared x bounds</dt>
              <dd>{cell.x_bounds_m.map(number).join(" → ")} m</dd>
            </div>
            <div>
              <dt>Declared y bounds</dt>
              <dd>{cell.y_bounds_m.map(number).join(" → ")} m</dd>
            </div>
            <div>
              <dt>Actual point minimum XYZ</dt>
              <dd>{vector(cell.point_min_xyz_m)}</dd>
            </div>
            <div>
              <dt>Actual point maximum XYZ</dt>
              <dd>{vector(cell.point_max_xyz_m)}</dd>
            </div>
          </dl>
          {cell.reasons.map((reason) => (
            <p key={reason}>{reason.replaceAll("_", " ")}</p>
          ))}
        </div>
      )}
    </section>
  );
}
