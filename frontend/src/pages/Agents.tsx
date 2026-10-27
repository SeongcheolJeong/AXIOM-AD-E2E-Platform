import { useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Braces,
  ChevronDown,
  Code2,
  Command,
  FileCode2,
  Terminal,
} from "lucide-react";
import { useResource } from "../api";
import type { AgentTool, Scenario } from "../types";
import {
  CopyButton,
  ErrorState,
  Loading,
  PageHeading,
  SearchField,
} from "../components/ui";
export default function Agents({ scenarios }: { scenarios: Scenario[] }) {
  const { data, loading, error, reload } = useResource<{
    tools: AgentTool[];
    usage_notes: string[];
  }>("/agent/tools");
  const [query, setQuery] = useState(""),
    [expanded, setExpanded] = useState("");
  const example = `curl -X POST http://127.0.0.1:8000/api/v1/jobs \\\n  -H 'Content-Type: application/json' \\\n  -H 'Idempotency-Key: my-unique-execution-001' \\\n  -d '{"scenario_id":"${scenarios[0]?.id ?? "<scenario-id>"}","adapter_id":"reference"}'`;
  function curl(tool: AgentTool) {
    const path = tool.path.startsWith("/api/")
      ? tool.path
      : `/api/v1${tool.path}`;
    const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
    const parts = [
      `curl -X ${tool.method}`,
      quote(`http://127.0.0.1:8000${path}`),
    ];
    if (!tool.read_only) parts.push("-H 'Content-Type: application/json'");
    for (const header of tool.required_headers ?? []) {
      if (!/^[A-Za-z0-9-]+$/.test(header)) continue;
      const value =
        header.toLowerCase() === "idempotency-key"
          ? "<stable-logical-request-key>"
          : "<required-header-value>";
      parts.push(`-H ${quote(`${header}: ${value}`)}`);
    }
    if (!tool.read_only) parts.push("-d '<JSON matching the input schema>'");
    return parts.join(" ");
  }
  return (
    <>
      <PageHeading
        eyebrow="AUTOMATE THE DEVELOPMENT LOOP"
        title="Agent studio & API"
        description="The same tools for your team, your scripts, and your AI agents."
      />
      <div className="agent-hero">
        <div>
          <span className="code-orb">
            <Command size={27} />
          </span>
          <h2>
            Less glue code.
            <br />
            More useful experiments.
          </h2>
          <p>
            Discover structured tools, submit reproducible runs, and retrieve
            evidence through a documented API.
          </p>
          <div className="agent-hero-tags">
            <span>
              <Braces size={13} />
              JSON schemas
            </span>
            <span>
              <Code2 size={13} />
              OpenAPI
            </span>
            <span>
              <Terminal size={13} />
              Agent guide
            </span>
          </div>
        </div>
        <div className="code-window">
          <header>
            <span>
              <i />
              <i />
              <i />
            </span>
            <span>RUN AN EXPERIMENT</span>
            <CopyButton text={example} />
          </header>
          <pre>
            <code>{example}</code>
          </pre>
          <footer>
            <span className="api-dot" />
            POST /api/v1/jobs<span>202 Accepted</span>
          </footer>
        </div>
      </div>
      <div className="doc-link-grid">
        {[
          {
            icon: BookOpen,
            title: "Interactive API reference",
            description: "Explore endpoints and try requests.",
            url: "/docs",
          },
          {
            icon: FileCode2,
            title: "OpenAPI specification",
            description: "Generate clients from the live schema.",
            url: "/openapi.json",
          },
          {
            icon: Terminal,
            title: "Agent integration guide",
            description: "Execution patterns, units, and limitations.",
            url: "/llms.txt",
          },
        ].map((doc) => (
          <a
            className="doc-link"
            key={doc.url}
            href={doc.url}
            target="_blank"
            rel="noreferrer"
          >
            <doc.icon size={21} />
            <div>
              <strong>{doc.title}</strong>
              <span>{doc.description}</span>
            </div>
            <ArrowUpRight size={17} />
          </a>
        ))}
      </div>
      {loading ? (
        <Loading />
      ) : error ? (
        <ErrorState message={error} retry={reload} />
      ) : (
        <>
          <div className="tool-section-header">
            <div>
              <h2>
                Discoverable tools <span>{data?.tools.length ?? 0}</span>
              </h2>
              <p>Structured operations published by the running platform.</p>
            </div>
            <SearchField
              value={query}
              onChange={setQuery}
              placeholder="Search agent tools…"
            />
          </div>
          <section className="panel tool-list">
            {data?.tools
              .filter((tool) =>
                `${tool.name} ${tool.description}`
                  .toLowerCase()
                  .includes(query.toLowerCase()),
              )
              .map((tool) => (
                <article key={tool.name} className="tool-item">
                  <button
                    className="tool-title"
                    onClick={() =>
                      setExpanded((v) => (v === tool.name ? "" : tool.name))
                    }
                  >
                    <span className={`method ${tool.method.toLowerCase()}`}>
                      {tool.method}
                    </span>
                    <div>
                      <strong>{tool.name}</strong>
                      <p>{tool.description}</p>
                    </div>
                    <span className="tool-access">
                      {tool.read_only ? "Read only" : "Mutates state"}
                    </span>
                    <ChevronDown
                      size={16}
                      className={expanded === tool.name ? "rotate" : ""}
                    />
                  </button>
                  {expanded === tool.name && (
                    <div className="tool-detail">
                      <div>
                        <span className="eyebrow">ENDPOINT</span>
                        <code>{tool.path}</code>
                        <CopyButton
                          text={curl(tool)}
                          label="Copy cURL template"
                        />
                      </div>
                      {!!tool.required_headers?.length && (
                        <p>
                          Required headers ·{" "}
                          <code>{tool.required_headers.join(", ")}</code>
                        </p>
                      )}
                      {tool.idempotency_scope && (
                        <p>
                          Retry scope · <code>{tool.idempotency_scope}</code>.
                          Preserve the same key and body after a lost response.
                        </p>
                      )}
                      <h4>Input schema</h4>
                      <pre>{JSON.stringify(tool.input_schema, null, 2)}</pre>
                    </div>
                  )}
                </article>
              ))}
          </section>
          {data?.usage_notes.length ? (
            <div className="agent-notes">
              <span className="eyebrow">INTEGRATION NOTES</span>
              {data.usage_notes.map((note, i) => (
                <p key={i}>
                  <ArrowRight size={13} />
                  {note}
                </p>
              ))}
            </div>
          ) : null}
        </>
      )}
    </>
  );
}
