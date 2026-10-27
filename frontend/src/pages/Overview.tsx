import {
  ArrowRight,
  ArrowUpRight,
  Box,
  CheckCheck,
  Database,
  FlaskConical,
  Plus,
  Workflow,
} from "lucide-react";
import {
  categoryLabels,
  type Overview as OverviewData,
  type RunDetail,
} from "../types";
import Replay from "../components/Replay";
import RunTable from "../components/RunTable";
import { PageHeading, PanelHeading, TextLink } from "../components/ui";
export default function Overview({
  data,
  run,
  onRun,
  onCreate,
  onSelect,
  onNavigate,
}: {
  data: OverviewData;
  run: RunDetail | null;
  onRun: () => void;
  onCreate: () => void;
  onSelect: (id: string) => void;
  onNavigate: (page: string) => void;
}) {
  const covered = data.covered_requirement_count,
    totalReq = data.requirement_count;
  const distribution = data.category_distribution;
  return (
    <>
      <PageHeading
        eyebrow="YOUR DEVELOPMENT LOOP, CONNECTED"
        title="Workspace overview"
        description="From the first scenario to evidence you can trace."
        actions={
          <>
            <button className="button secondary" onClick={onCreate}>
              <Plus size={16} />
              New scenario
            </button>
            <button className="button primary" onClick={onRun}>
              <PlayIcon />
              Run simulation
            </button>
          </>
        }
      />
      <div className="stats-grid">
        {[
          {
            label: "Scenarios",
            value: data.scenario_count,
            detail: "Versioned test definitions",
            icon: Box,
            accent: "green",
          },
          {
            label: "Simulation runs",
            value: data.run_count,
            detail: "Persisted execution results",
            icon: Workflow,
            accent: "blue",
          },
          {
            label: "Pass rate",
            value: `${Number(data.pass_rate).toFixed(1)}%`,
            detail: "Across persisted executions",
            icon: CheckCheck,
            accent: "purple",
          },
          {
            label: "Data artifacts",
            value: data.artifact_count,
            detail: "Tracked with SHA-256 lineage",
            icon: Database,
            accent: "orange",
          },
        ].map((stat) => (
          <section className="stat-card" key={stat.label}>
            <div className="stat-label">
              {stat.label}
              <stat.icon size={17} />
            </div>
            <div className="stat-value">
              {stat.value}
              <span className={`stat-glyph ${stat.accent}`}>
                <stat.icon size={31} strokeWidth={1} />
              </span>
            </div>
            <p>{stat.detail}</p>
          </section>
        ))}
      </div>
      <div className="overview-grid">
        <Replay run={run} onExplore={() => run && onSelect(run.id)} />
        <div className="overview-right">
          <section className="panel coverage-panel">
            <PanelHeading
              title="Validation coverage"
              action={
                <button
                  className="icon-button"
                  aria-label="Open validation"
                  onClick={() => onNavigate("validation")}
                >
                  <ArrowUpRight size={17} />
                </button>
              }
            />
            <div className="coverage-head">
              <div
                className="coverage-ring"
                style={{
                  background: `conic-gradient(#638775 0% ${totalReq ? (covered / totalReq) * 100 : 0}%, #edf0e8 ${totalReq ? (covered / totalReq) * 100 : 0}% 100%)`,
                }}
              >
                <div>
                  <strong>
                    {totalReq ? Math.round((covered / totalReq) * 100) : 0}
                    <small>%</small>
                  </strong>
                </div>
              </div>
              <div>
                <strong>
                  {covered} of {totalReq}
                </strong>
                <p>
                  requirements have
                  <br />
                  execution evidence
                </p>
              </div>
            </div>
            <div className="matrix-label">
              <span>SCENARIO × WEATHER</span>
              <span>Passed / defined</span>
            </div>
            <div className="coverage-matrix">
              <span />
              <span>Clear</span>
              <span>Rain</span>
              <span>Fog</span>
              {Object.keys(categoryLabels).map((category) => (
                <div className="matrix-row" key={category}>
                  <span title={categoryLabels[category]}>
                    {categoryLabels[category]}
                  </span>
                  {["clear", "rain", "fog"].map((weather) => {
                    const cell = data.coverage.find(
                      (c) => c.category === category && c.weather === weather,
                    );
                    return (
                      <div
                        key={weather}
                        title={`${categoryLabels[category]}, ${weather}: ${cell?.total ?? 0} defined; ${cell?.passed ?? 0} passed, ${cell?.failed ?? 0} failed, ${cell?.untested ?? 0} without current baseline evidence`}
                        className={`matrix-cell ${!cell?.tested ? "none" : cell.failed ? "fail" : cell.untested ? "partial" : "pass"}`}
                      >
                        {cell?.total ? `${cell.passed}/${cell.total}` : "—"}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
            <div className="matrix-legend">
              <span>
                <i className="pass" />
                All passed
              </span>
              <span>
                <i className="fail" />
                Failed
              </span>
              <span>
                <i className="none" />
                Pending evidence
              </span>
            </div>
          </section>
          <section className="panel distribution-panel">
            <PanelHeading
              title="Scenario composition"
              subtitle={`${data.scenario_count} scenarios in your library`}
            />
            <div className="distribution-bar">
              {distribution.map((d, i) => (
                <span
                  key={d.category}
                  style={{
                    width: `${data.scenario_count ? (d.count / data.scenario_count) * 100 : 0}%`,
                    background: ["#789a85", "#b7c19c", "#b4a8ce", "#e6bd9b"][
                      i % 4
                    ],
                  }}
                  title={`${categoryLabels[d.category] ?? d.category}: ${d.count}`}
                />
              ))}
            </div>
            <div className="distribution-legend">
              {distribution.map((d, i) => (
                <div key={d.category}>
                  <i
                    style={{
                      background: ["#789a85", "#b7c19c", "#b4a8ce", "#e6bd9b"][
                        i % 4
                      ],
                    }}
                  />
                  <span>{categoryLabels[d.category] ?? d.category}</span>
                  <strong>{d.count}</strong>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
      <section className="panel recent-panel">
        <PanelHeading
          title="Recent simulation runs"
          subtitle="A traceable record of every execution."
          action={
            <TextLink onClick={() => onNavigate("runs")}>
              View all runs
            </TextLink>
          }
        />
        <RunTable runs={data.recent_runs.slice(0, 5)} onSelect={onSelect} />
      </section>
      <div className="workspace-next">
        <div className="next-icon">
          <FlaskConical size={22} />
        </div>
        <div>
          <strong>Build the next test. Close the next gap.</strong>
          <p>
            Every scenario, run, and artifact stays connected through one open
            API.
          </p>
        </div>
        <button className="text-link" onClick={() => onNavigate("agents")}>
          Explore agent tools
          <ArrowRight size={16} />
        </button>
      </div>
    </>
  );
}
function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
      <path d="m4 2 8 5-8 5Z" />
    </svg>
  );
}
