import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Boxes,
  Play,
  Plus,
  RefreshCw,
} from "lucide-react";
import { formatDate } from "../api";
import { Empty, ErrorState, Loading, PageHeading } from "../components/ui";
import JobStatusBadge from "../components/jobs/JobStatusBadge";
import { useJobResource } from "../components/jobs/useJobResource";
import { useSensorResource } from "../components/sensors/SensorData";
import StackEditor from "../components/stacks/StackEditor";
import {
  kernelAvailable,
  stackKernels,
} from "../components/stacks/StackKernels";
import StackDefinitionView from "../components/stacks/StackDefinitionView";
import StackReplayLauncher from "../components/stacks/StackReplayLauncher";
import StackReplayInspector from "../components/stacks/StackReplayInspector";
import type {
  StackComponentMetadata,
  StackDetail,
} from "../components/stacks/StackTypes";
import type {
  StackReplayPage,
  StackReplayDetail,
} from "../components/stacks/StackReplayTypes";
import "../stacks.css";
import "../verification.css";
import DrivingPolicyWorkbench from "../components/stacks/DrivingPolicyWorkbench";

export default function Stacks() {
  const targetReplay = new URLSearchParams(location.hash.split("?")[1] ?? "").get("replay");
  const [tab, setTab] = useState<"definitions" | "replays" | "driving">(targetReplay ? "replays" : "definitions");
  const [id, setId] = useState(""),
    [revision, setRevision] = useState(1),
    [jobId, setJobId] = useState(targetReplay ?? "");
  const [editor, setEditor] = useState<StackDetail | "new" | null>(null),
    [launch, setLaunch] = useState<StackDetail | "new" | null>(null);
  const [status, setStatus] = useState(""),
    [before, setBefore] = useState<number | null>(null);
  const stacks = useSensorResource<StackDetail[]>("/stacks");
  const components =
    useSensorResource<StackComponentMetadata[]>("/stack-components");
  const history = useSensorResource<StackDetail[]>(
    id ? `/stacks/${encodeURIComponent(id)}/revisions` : null,
  );
  const query = new URLSearchParams({ limit: "100" });
  if (status) query.set("status", status);
  if (before !== null) query.set("before_seq", String(before));
  const jobs = useJobResource<StackReplayPage>(`/stack-replays?${query}`, 3000);
  const detail = useJobResource<StackReplayDetail>(
    jobId ? `/stack-replays/${encodeURIComponent(jobId)}` : null,
    2500,
  );
  useEffect(() => {
    if (!id && stacks.data?.[0]) {
      setId(stacks.data[0].id);
      setRevision(stacks.data[0].revision);
    }
  }, [id, stacks.data]);
  useEffect(() => {
    if (!jobId && jobs.data?.items[0]) setJobId(jobs.data.items[0].id);
  }, [jobId, jobs.data]);
  const stack =
    history.data?.find(
      (item) => item.id === id && item.revision === revision,
    ) ?? null;
  const job = detail.data?.id === jobId ? detail.data : null;
  const canCreate = stackKernels.some((kernel) =>
    kernelAvailable(components.data ?? [], kernel),
  );
  function refresh() {
    stacks.reload();
    components.reload();
    history.reload();
    jobs.reload();
    detail.reload();
  }
  function saved(value: StackDetail) {
    setEditor(null);
    setId(value.id);
    setRevision(value.revision);
    stacks.reload();
    history.reload();
  }
  function submitted(value: StackReplayDetail) {
    setLaunch(null);
    setTab("replays");
    setJobId(value.id);
    setStatus("");
    setBefore(null);
    jobs.reload();
    detail.reload();
  }
  return (
    <div className="stacks-page">
      <PageHeading
        eyebrow="REPLACEABLE MODULES · INSPECTABLE DECISIONS"
        title="Component stacks"
        description="Connect sensor observations to policy and control requests, then inspect every recorded input and decision boundary."
      />
      <div className="stack-workspace-toolbar">
        <div
          className="stack-tabs"
          role="tablist"
          aria-label="Component stack workspace"
        >
          <button
            role="tab"
            aria-selected={tab === "definitions"}
            onClick={() => setTab("definitions")}
          >
            <Boxes size={15} />
            Definitions
          </button>
          <button
            role="tab"
            aria-selected={tab === "replays"}
            onClick={() => setTab("replays")}
          >
            <Play size={15} />
            Recorded replays
          </button>
          <button role="tab" aria-selected={tab === "driving"} onClick={() => setTab("driving")}>Driving policies</button>
        </div>
        <div className="stack-actions">
          <button
            type="button"
            className="button secondary"
            aria-label="Refresh stacks workspace"
            onClick={refresh}
          >
            <RefreshCw size={14} />
            Refresh
          </button>
          {tab === "driving" ? null : tab === "definitions" ? (
            <button
              type="button"
              className="button primary"
              disabled={!canCreate}
              onClick={() => setEditor("new")}
            >
              <Plus size={14} />
              New stack
            </button>
          ) : (
            <button
              type="button"
              className="button primary"
              disabled={!stacks.data?.length}
              onClick={() => setLaunch("new")}
            >
              <Play size={14} />
              New recorded replay
            </button>
          )}
        </div>
      </div>
      {components.error && (
        <p className="stack-stale" role="alert">
          Component catalog unavailable. {components.error}
          <button onClick={components.reload}>Retry stack components</button>
        </p>
      )}
      {tab === "driving" ? <DrivingPolicyWorkbench /> : tab === "definitions" ? (
        stacks.error ? (
          <ErrorState message={stacks.error} retry={stacks.reload} />
        ) : stacks.loading && !stacks.data ? (
          <Loading label="Loading immutable component stacks…" />
        ) : !stacks.data?.length ? (
          <section className="panel">
            <Empty
              title="Define your first sensor-driven stack"
              description="Choose versioned observation, policy, and control components. Replay them over exact source messages without modifying recorded observations."
              action={
                <button
                  type="button"
                  className="button primary"
                  disabled={!canCreate}
                  onClick={() => setEditor("new")}
                >
                  Create first stack
                </button>
              }
            />
          </section>
        ) : (
          <>
            <div className="stack-library-controls">
              <label>
                Component stack
                <select
                  aria-label="Select component stack"
                  value={id}
                  onChange={(e) => {
                    const selected = stacks.data!.find(
                      (item) => item.id === e.target.value,
                    )!;
                    setId(selected.id);
                    setRevision(selected.revision);
                  }}
                >
                  {stacks.data.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Immutable revision
                <select
                  aria-label="Component stack revision"
                  value={revision}
                  onChange={(e) => setRevision(Number(e.target.value))}
                >
                  {history.data?.map((item) => (
                    <option key={item.revision} value={item.revision}>
                      r{item.revision} · {formatDate(item.created_at)}
                    </option>
                  )) ?? <option value={revision}>r{revision}</option>}
                </select>
              </label>
            </div>
            {history.error ? (
              <ErrorState message={history.error} retry={history.reload} />
            ) : stack ? (
              <StackDefinitionView
                stack={stack}
                onEdit={() => setEditor(stack)}
                onReplay={() => setLaunch(stack)}
              />
            ) : (
              <Loading label="Loading the selected stack revision…" />
            )}
          </>
        )
      ) : (
        <>
          <div className="stack-list-toolbar">
            <label>
              Replay status
              <select
                aria-label="Filter stack replays"
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  setBefore(null);
                  setJobId("");
                }}
              >
                <option value="">All states</option>
                {[
                  "queued",
                  "running",
                  "cancel_requested",
                  "completed",
                  "failed",
                  "cancelled",
                  "interrupted",
                ].map((value) => (
                  <option key={value} value={value}>
                    {value.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <span>
              {jobs.data?.items.length ?? 0} replays in this page · shared
              durable queue
            </span>
          </div>
          {jobs.error && (
            <p className="stack-stale" role="alert">
              Replay list refresh failed.{" "}
              {jobs.data
                ? "Showing its last successful observation."
                : "No list observation is available."}{" "}
              {jobs.error}
              <button onClick={jobs.reload}>Retry replay list</button>
            </p>
          )}
          <div className="stack-replay-workspace">
            <aside
              className="panel stack-replay-library"
              aria-label="Recorded stack replay library"
            >
              {jobs.loading && !jobs.data ? (
                <Loading label="Loading recorded stack replays…" />
              ) : jobs.data?.items.length ? (
                jobs.data.items.map((item) => (
                  <button
                    type="button"
                    className="stack-replay-card"
                    key={item.id}
                    aria-label={`Open stack replay ${item.label}`}
                    aria-pressed={jobId === item.id}
                    onClick={() => setJobId(item.id)}
                  >
                    <span>
                      #{item.submission_seq}
                      <JobStatusBadge status={item.status} />
                    </span>
                    <strong>{item.label}</strong>
                    <small>
                      {item.stack_name} r{item.stack_revision} ·{" "}
                      {item.counts.completed}/{item.counts.total} ticks
                    </small>
                    <time>{formatDate(item.created_at)}</time>
                  </button>
                ))
              ) : (
                <div className="stack-library-empty">
                  <Boxes size={24} />
                  <strong>No recorded replays</strong>
                  <p>Pin a stack and supported source channels to begin.</p>
                </div>
              )}
              <div className="stack-actions">
                <button
                  type="button"
                  className="button secondary small"
                  disabled={before === null}
                  onClick={() => {
                    setBefore(null);
                    setJobId("");
                  }}
                >
                  <ArrowLeft size={12} />
                  Newest
                </button>
                <button
                  type="button"
                  className="button secondary small"
                  disabled={jobs.data?.next_before_seq == null}
                  onClick={() => {
                    setBefore(jobs.data?.next_before_seq ?? null);
                    setJobId("");
                  }}
                >
                  Older
                  <ArrowRight size={12} />
                </button>
              </div>
            </aside>
            <div className="stack-replay-main">
              {detail.error && (
                <p className="stack-stale" role="alert">
                  {job
                    ? "Replay refresh failed. Showing the last observation of this same replay."
                    : "The selected stack replay could not be loaded."}{" "}
                  {detail.error}
                  <button onClick={detail.reload}>
                    Retry selected stack replay
                  </button>
                </p>
              )}
              {job ? (
                <StackReplayInspector
                  key={job.id}
                  job={job}
                  onChanged={() => {
                    detail.reload();
                    jobs.reload();
                  }}
                />
              ) : jobId && detail.loading ? (
                <Loading label="Loading pinned inputs and stack trace…" />
              ) : !jobId ? (
                <section className="panel">
                  <Empty
                    title="Select a recorded replay"
                    description="Inspect input age, observed geometry, policy assumptions, and requested controls."
                  />
                </section>
              ) : null}
            </div>
          </div>
        </>
      )}
      {editor && (
        <StackEditor
          stack={editor === "new" ? undefined : editor}
          expectedRevision={
            stacks.data?.find(
              (item) => item.id === (editor === "new" ? "" : editor.id),
            )?.revision
          }
          components={components.data ?? []}
          onSaved={saved}
          onClose={() => setEditor(null)}
        />
      )}
      {launch && stacks.data && (
        <StackReplayLauncher
          stacks={stacks.data}
          components={components.data ?? []}
          initialStack={launch === "new" ? null : launch}
          onClose={() => setLaunch(null)}
          onSubmitted={submitted}
        />
      )}
    </div>
  );
}
