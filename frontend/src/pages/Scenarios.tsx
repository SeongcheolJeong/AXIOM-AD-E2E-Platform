import { useState } from "react";
import {
  Cloud,
  CloudFog,
  CloudRain,
  Copy,
  Edit3,
  Grid2X2,
  List,
  Map,
  Play,
  Plus,
  Sun,
} from "lucide-react";
import { api } from "../api";
import { categories, categoryLabels, type Scenario } from "../types";
import { Empty, PageHeading, SearchField } from "../components/ui";
function MiniMap({ category }: { category: string }) {
  return (
    <svg className="scenario-map" viewBox="0 0 340 110" aria-hidden="true">
      <defs>
        <pattern
          id={`grid-${category}`}
          width="20"
          height="20"
          patternUnits="userSpaceOnUse"
        >
          <path d="M20 0H0V20" fill="none" stroke="#d4ddd5" strokeWidth=".5" />
        </pattern>
      </defs>
      <rect width="340" height="110" fill={`url(#grid-${category})`} />
      <path d="M0 35H340M0 80H340" stroke="#b9c5bb" strokeWidth="1" />
      <path d="M0 58H340" stroke="#b5c2b7" strokeDasharray="10 9" />
      <path
        d={
          category === "cut_in"
            ? "M60 68H150C188 68 179 45 212 45H288"
            : category === "pedestrian_crossing"
              ? "M175 12V95"
              : "M55 68H286"
        }
        fill="none"
        stroke={category === "pedestrian_crossing" ? "#cf9875" : "#7c9e88"}
        strokeWidth="2.5"
        strokeDasharray={category === "pedestrian_crossing" ? "4 4" : undefined}
      />
      <rect x="110" y="59" width="29" height="15" rx="3" fill="#719781" />
      <rect x="118" y="61" width="12" height="11" rx="1" fill="#c3d7c6" />
      {category === "pedestrian_crossing" ? (
        <circle cx="175" cy="40" r="5" fill="#d1a17a" />
      ) : (
        <>
          <rect
            x="215"
            y={category === "cut_in" ? 36 : 59}
            width="29"
            height="15"
            rx="3"
            fill="#afa1c5"
          />
          <rect
            x="221"
            y={category === "cut_in" ? 38 : 61}
            width="12"
            height="11"
            rx="1"
            fill="#dad3e6"
          />
        </>
      )}
      {category === "emergency_brake" && (
        <path
          d="m255 58 7 7-7 7m7-14 7 7-7 7"
          fill="none"
          stroke="#c59372"
          strokeWidth="2"
        />
      )}
    </svg>
  );
}
export default function Scenarios({
  scenarios,
  onCreate,
  onEdit,
  onRun,
  onRefresh,
  notify,
}: {
  scenarios: Scenario[];
  onCreate: () => void;
  onEdit: (scenario: Scenario) => void;
  onRun: (id: string) => void;
  onRefresh: () => void;
  notify: (message: string) => void;
}) {
  const [query, setQuery] = useState(""),
    [category, setCategory] = useState("all"),
    [weather, setWeather] = useState("all"),
    [layout, setLayout] = useState("grid"),
    [cloning, setCloning] = useState(""),
    [error, setError] = useState("");
  const filtered = scenarios.filter(
    (s) =>
      (category === "all" || s.category === category) &&
      (weather === "all" || s.weather === weather) &&
      `${s.name} ${s.description} ${s.tags.join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  async function clone(scenario: Scenario) {
    setCloning(scenario.id);
    setError("");
    try {
      await api<Scenario>(`/scenarios/${scenario.id}/clone`, {
        method: "POST",
      });
      onRefresh();
      notify("Scenario cloned as an independent definition");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCloning("");
    }
  }
  return (
    <>
      <PageHeading
        eyebrow="DESIGN THE CONDITIONS"
        title="Scenario library"
        description="Small, reproducible tests for the behaviors that matter."
        actions={
          <button className="button primary" onClick={onCreate}>
            <Plus size={16} />
            Create scenario
          </button>
        }
      />
      <div className="collection-toolbar">
        <div className="tabs">
          <button
            className={category === "all" ? "active" : ""}
            onClick={() => setCategory("all")}
          >
            All scenarios <span>{scenarios.length}</span>
          </button>
          {categories.map((c) => (
            <button
              key={c}
              className={category === c ? "active" : ""}
              onClick={() => setCategory(c)}
            >
              {categoryLabels[c]}
            </button>
          ))}
        </div>
      </div>
      <div className="filter-bar">
        <SearchField
          value={query}
          onChange={setQuery}
          placeholder="Search scenarios or tags…"
        />
        <div className="filter-right">
          <select
            aria-label="Filter weather"
            value={weather}
            onChange={(e) => setWeather(e.target.value)}
          >
            <option value="all">All weather</option>
            <option value="clear">Clear</option>
            <option value="rain">Rain</option>
            <option value="fog">Fog</option>
          </select>
          <div className="segmented">
            <button
              aria-label="Grid view"
              className={layout === "grid" ? "active" : ""}
              onClick={() => setLayout("grid")}
            >
              <Grid2X2 size={16} />
            </button>
            <button
              aria-label="List view"
              className={layout === "list" ? "active" : ""}
              onClick={() => setLayout("list")}
            >
              <List size={16} />
            </button>
          </div>
        </div>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="results-caption">
        {filtered.length} scenario{filtered.length !== 1 ? "s" : ""}
        <span>Application schema · SI units · versioned inputs</span>
      </div>
      {!filtered.length ? (
        <section className="panel">
          <Empty
            title="No matching scenarios"
            description="Adjust your filters or create a new test definition."
            action={
              <button className="button secondary" onClick={onCreate}>
                <Plus size={15} />
                Create scenario
              </button>
            }
          />
        </section>
      ) : (
        <div
          className={`scenario-grid ${layout === "list" ? "scenario-list" : ""}`}
        >
          {filtered.map((s) => {
            const Weather =
              s.weather === "clear"
                ? Sun
                : s.weather === "rain"
                  ? CloudRain
                  : CloudFog;
            return (
              <article className="scenario-card" key={s.id}>
                <div className="scenario-card-visual">
                  <MiniMap category={s.category} />
                  <span className="scenario-category">
                    {categoryLabels[s.category]}
                  </span>
                  <span className="scenario-revision">r{s.revision}</span>
                </div>
                <div className="scenario-card-body">
                  <button className="scenario-title" onClick={() => onEdit(s)}>
                    {s.name}
                  </button>
                  <p>
                    {s.description || "A reproducible object-level scenario."}
                  </p>
                  <div className="scenario-facts">
                    <span>
                      <Map size={13} />
                      {s.map_name}
                    </span>
                    <span>
                      <Weather size={13} />
                      {s.weather}
                    </span>
                    <span>{s.duration_s}s</span>
                  </div>
                  <div className="tags">
                    {s.tags.slice(0, 3).map((tag) => (
                      <span key={tag}>{tag}</span>
                    ))}
                    <span className="seed-tag">seed {s.seed}</span>
                  </div>
                </div>
                <footer>
                  <span className="scenario-speeds">
                    <Cloud size={13} />
                    {s.parameters.ego_speed_mps} m/s ego
                  </span>
                  <div>
                    <button
                      className="icon-button"
                      aria-label={`Edit ${s.name}`}
                      title="Edit scenario"
                      onClick={() => onEdit(s)}
                    >
                      <Edit3 size={15} />
                    </button>
                    <button
                      className="icon-button"
                      aria-label={`Clone ${s.name}`}
                      title="Clone scenario"
                      disabled={cloning === s.id}
                      onClick={() => clone(s)}
                    >
                      <Copy size={15} />
                    </button>
                    <button
                      className="button run-small"
                      onClick={() => onRun(s.id)}
                    >
                      <Play size={12} />
                      Run
                    </button>
                  </div>
                </footer>
              </article>
            );
          })}
        </div>
      )}
    </>
  );
}
