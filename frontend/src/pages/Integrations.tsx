import {
  ArrowRight,
  ArrowUpRight,
  Box,
  Braces,
  Cable,
  Check,
  ChevronRight,
  Layers3,
  LoaderCircle,
  Plug,
  RefreshCw,
  Route,
  Workflow,
} from "lucide-react";
import { useState } from "react";
import { api, formatDate } from "../api";
import type { Adapter } from "../types";
import { Badge, PageHeading } from "../components/ui";

interface AdapterHealth {
  adapter_id: string;
  checked_at: string;
  status: "ready" | "not_configured" | "unreachable" | "unknown";
  detail: string;
  metadata?: { version?: string; map?: string; scope?: string };
}

export default function Integrations({
  adapters,
  onRun,
}: {
  adapters: Adapter[];
  onRun: () => void;
}) {
  const [health, setHealth] = useState<Record<string, AdapterHealth>>({});
  const [checking, setChecking] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  async function checkConnection(adapterId: string) {
    setChecking((current) => ({ ...current, [adapterId]: true }));
    setErrors((current) => ({ ...current, [adapterId]: "" }));
    try {
      const result = await api<AdapterHealth>(
        `/adapters/${encodeURIComponent(adapterId)}/health`,
        { cache: "no-store" },
      );
      setHealth((current) => ({ ...current, [adapterId]: result }));
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [adapterId]:
          error instanceof Error
            ? error.message
            : "Connection check could not be completed.",
      }));
    } finally {
      setChecking((current) => ({ ...current, [adapterId]: false }));
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="MODULAR BY DESIGN"
        title="Engine integrations"
        description="Replace the engine. Keep your scenarios, data, and development workflow."
      />
      <section className="integration-hero">
        <div>
          <span className="eyebrow">ONE CONTRACT. MANY ENGINES.</span>
          <h2>
            Your simulation stack,
            <br />
            connected on your terms.
          </h2>
          <p>
            Execution adapters connect engines to a shared schema for inputs,
            trajectories, metrics, and artifacts.
          </p>
          <a
            className="button light"
            href="/docs"
            target="_blank"
            rel="noreferrer"
          >
            Explore the API
            <ArrowUpRight size={15} />
          </a>
        </div>
        <div className="integration-diagram">
          <div className="diagram-node">
            <Box size={22} />
            <span>Scenario</span>
          </div>
          <span className="diagram-connector" />
          <div className="diagram-node center">
            <Plug size={26} />
            <span>Adapter</span>
          </div>
          <span className="diagram-connector" />
          <div className="diagram-node">
            <Layers3 size={22} />
            <span>Evidence</span>
          </div>
          <div className="diagram-caption">
            Versioned input <ArrowRight size={12} /> Swap-in execution{" "}
            <ArrowRight size={12} /> Shared results
          </div>
        </div>
      </section>
      <div className="section-label">
        EXECUTION ADAPTERS <span>{adapters.length} registered</span>
      </div>
      <div className="adapter-grid">
        {adapters.map((adapter, i) => {
          const checked = health[adapter.id];
          const isChecking = checking[adapter.id] ?? false;
          const Icon =
            adapter.id === "carla"
              ? Route
              : i === 0
                ? Box
                : i % 2
                  ? Cable
                  : Workflow;
          return (
            <article
              key={adapter.id}
              className={`adapter-card ${adapter.id === "carla" ? "carla-card" : ""}`}
            >
              <div className="adapter-card-top">
                <span className={`integration-icon color-${i % 4}`}>
                  <Icon size={26} />
                </span>
                <Badge
                  status={adapter.status}
                  label={
                    adapter.status === "available"
                      ? adapter.id === "reference"
                        ? "Local adapter"
                        : "Configured"
                      : adapter.status === "not_configured"
                        ? "Not configured"
                        : "Planned"
                  }
                />
              </div>
              <div className="adapter-title">
                <h2>{adapter.name}</h2>
                <span className="mono">{adapter.version}</span>
              </div>
              <p>{adapter.description}</p>
              <div className="adapter-capabilities">
                {adapter.capabilities.map((capability) => (
                  <span key={capability}>
                    <Check size={12} />
                    {capability.replaceAll("_", " ")}
                  </span>
                ))}
              </div>
              {adapter.limitations.length > 0 && (
                <div className="adapter-limitations">
                  <strong>Current scope</strong>
                  {adapter.limitations.map((limitation, i) => (
                    <p key={i}>{limitation}</p>
                  ))}
                </div>
              )}
              <section
                className="adapter-connection"
                aria-label={`${adapter.name} connection`}
                aria-busy={isChecking}
              >
                <div className="adapter-connection-heading">
                  <strong>Connection check</strong>
                  <button
                    className="button secondary small"
                    onClick={() => checkConnection(adapter.id)}
                    disabled={isChecking}
                    aria-label={`Check connection for ${adapter.name}`}
                  >
                    {isChecking ? (
                      <LoaderCircle className="spin" size={14} />
                    ) : (
                      <RefreshCw size={14} />
                    )}
                    {isChecking ? "Checking…" : "Check connection"}
                  </button>
                </div>
                {errors[adapter.id] && (
                  <p className="inline-error" role="alert">
                    {errors[adapter.id]}
                  </p>
                )}
                <div aria-live="polite">
                  {checked ? (
                    <>
                      <div className="adapter-health-meta">
                        <Badge
                          status={
                            checked.status === "ready"
                              ? "passed"
                              : checked.status === "unreachable"
                                ? "failed"
                                : "not_configured"
                          }
                          label={
                            checked.status === "ready"
                              ? "Ready"
                              : checked.status === "not_configured"
                                ? "Not configured"
                                : checked.status === "unreachable"
                                  ? "Not reachable / not ready"
                                  : "Unknown"
                          }
                        />
                        <span className="subtle">
                          Last check · {formatDate(checked.checked_at)}
                        </span>
                      </div>
                      <p>{checked.detail}</p>
                      {checked.metadata?.version && (
                        <p className="mono">
                          Version · {checked.metadata.version}
                        </p>
                      )}
                      {checked.metadata?.map && (
                        <p className="mono">Map · {checked.metadata.map}</p>
                      )}
                      {checked.metadata?.scope && (
                        <p>{checked.metadata.scope}</p>
                      )}
                    </>
                  ) : (
                    <p className="subtle">
                      No connection check has been made in this view.
                    </p>
                  )}
                </div>
                <p className="adapter-health-note">
                  Checks connectivity and version compatibility. No simulation
                  runs or fidelity validation.
                </p>
                <p className="adapter-health-note">
                  Connection health does not resolve an earlier execution or
                  confirm engine cleanup. Inspect its original submission in the{" "}
                  <a href="#jobs">execution queue</a> for remote outcome and
                  evidence recovery availability.
                </p>
              </section>
              <footer>
                <span>{adapter.license}</span>
                {adapter.status === "available" ? (
                  <button className="text-link" onClick={onRun}>
                    Use adapter
                    <ChevronRight size={14} />
                  </button>
                ) : adapter.source_url ? (
                  <a href={adapter.source_url} target="_blank" rel="noreferrer">
                    Project documentation
                    <ArrowUpRight size={14} />
                  </a>
                ) : (
                  <span className="muted">Implementation planned</span>
                )}
              </footer>
            </article>
          );
        })}
      </div>
      <div className="developer-callout">
        <Braces size={26} />
        <div>
          <h3>Build your own adapter</h3>
          <p>
            The adapter registry is the extension boundary. Engine configuration
            and execution stay separate from the workspace UI.
          </p>
        </div>
        <a
          className="button secondary"
          href="/openapi.json"
          target="_blank"
          rel="noreferrer"
        >
          OpenAPI schema
          <ArrowUpRight size={15} />
        </a>
      </div>
    </>
  );
}
