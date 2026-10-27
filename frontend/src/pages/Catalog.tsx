import RecordingWorkspace from "../components/recordings/RecordingWorkspace";
import "../recordings.css";
import { ArtifactImage, DownloadLink } from "../components/ArtifactAccess";
import { useState } from "react";
import {
  ArrowDownToLine,
  Database,
  FileJson,
  Image,
  ShieldCheck,
  FileArchive,
  LoaderCircle,
} from "lucide-react";
import { api, formatBytes, formatDate, shortId, useResource } from "../api";
import type { Artifact } from "../types";
import type { RecordingDetail } from "../components/recordings/RecordingTypes";
import type { InferenceSource } from "../components/perception/PerceptionTypes";
import {
  CopyButton,
  Empty,
  ErrorState,
  Loading,
  Modal,
  PageHeading,
  SearchField,
} from "../components/ui";
export default function Catalog({
  onOpenRun,
  onInfer,
}: {
  onOpenRun?: (id: string) => void;
  onInfer?: (source: InferenceSource) => void;
}) {
  const targetRecording = new URLSearchParams(location.hash.split("?")[1] ?? "").get("recording");
  const [tab, setTab] = useState<"artifacts" | "recordings">(targetRecording ? "recordings" : "artifacts");
  const [recordingId, setRecordingId] = useState<string | null>(targetRecording);
  return (
    <>
      <PageHeading
        eyebrow="EVIDENCE WITH CONTEXT"
        title="Data catalog"
        description="Explore execution evidence and original message recordings."
      />
      <div
        className="catalog-data-tabs"
        role="tablist"
        aria-label="Data catalog views"
      >
        <button
          type="button"
          role="tab"
          id="catalog-artifacts-tab"
          aria-controls="catalog-artifacts-panel"
          aria-selected={tab === "artifacts"}
          onClick={() => setTab("artifacts")}
        >
          Execution artifacts
        </button>
        <button
          type="button"
          role="tab"
          id="catalog-recordings-tab"
          aria-controls="catalog-recordings-panel"
          aria-selected={tab === "recordings"}
          onClick={() => setTab("recordings")}
        >
          Recordings
        </button>
      </div>
      {tab === "artifacts" ? (
        <div
          role="tabpanel"
          id="catalog-artifacts-panel"
          aria-labelledby="catalog-artifacts-tab"
        >
          <ExecutionArtifacts
            onOpenRecording={(id) => {
              setRecordingId(id);
              setTab("recordings");
            }}
          />
        </div>
      ) : (
        <div
          role="tabpanel"
          id="catalog-recordings-panel"
          aria-labelledby="catalog-recordings-tab"
        >
          <RecordingWorkspace
            initialRecordingId={recordingId}
            onOpenRun={onOpenRun}
            onInfer={onInfer}
          />
        </div>
      )}
    </>
  );
}

function ExecutionArtifacts({
  onOpenRecording,
}: {
  onOpenRecording: (id: string) => void;
}) {
  const { data, error, loading, reload } =
    useResource<Artifact[]>("/artifacts");
  const [query, setQuery] = useState(""),
    [kind, setKind] = useState("all"),
    [selected, setSelected] = useState<Artifact | null>(null);
  const [registering, setRegistering] = useState(false);
  const [registrationError, setRegistrationError] = useState("");
  async function openRecording(artifact: Artifact) {
    setRegistering(true);
    setRegistrationError("");
    try {
      const recording = await api<RecordingDetail>(
        `/runs/${encodeURIComponent(artifact.run_id)}/recordings`,
        { method: "POST", body: JSON.stringify({ artifact_id: artifact.id }) },
      );
      onOpenRecording(recording.id);
    } catch (reason) {
      setRegistrationError(
        reason instanceof Error
          ? reason.message
          : "Artifact registration failed.",
      );
    } finally {
      setRegistering(false);
    }
  }
  const rows = (data ?? []).filter(
    (a) =>
      (kind === "all" || a.kind === kind) &&
      `${a.name} ${a.run_id} ${a.source}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <>
      {loading ? (
        <Loading />
      ) : error ? (
        <ErrorState message={error} retry={reload} />
      ) : (
        <>
          <div className="catalog-summary">
            <div>
              <span className="catalog-icon">
                <Database size={24} />
              </span>
              <div>
                <strong>{data?.length ?? 0} artifacts</strong>
                <p>
                  {formatBytes(
                    (data ?? []).reduce((n, a) => n + a.size_bytes, 0),
                  )}{" "}
                  of managed execution data
                </p>
              </div>
            </div>
            <span>
              <ShieldCheck size={16} />
              Content hashed · run linked · exportable
            </span>
          </div>
          <div className="filter-bar">
            <SearchField
              value={query}
              onChange={setQuery}
              placeholder="Search artifacts, runs, or sources…"
            />
            <select
              aria-label="Filter artifact kind"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="all">All artifact types</option>
              {[...new Set(data?.map((a) => a.kind))].map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>
          <section className="panel">
            {!rows.length ? (
              <Empty
                title="No matching artifacts"
                description="Run a simulation or adjust your search to find recorded evidence."
              />
            ) : (
              <div className="table-scroll">
                <table className="data-table artifact-table">
                  <thead>
                    <tr>
                      <th>Artifact</th>
                      <th>Kind / source</th>
                      <th>Run</th>
                      <th>Size</th>
                      <th>Created</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((a) => (
                      <tr key={a.id}>
                        <td>
                          <button
                            className="file-title"
                            onClick={() => {
                              setSelected(a);
                              setRegistrationError("");
                            }}
                          >
                            <span className="file-icon">
                              {a.media_type.startsWith("image/") ? (
                                <Image size={19} />
                              ) : (
                                <FileJson size={19} />
                              )}
                            </span>
                            <span>
                              <strong>{a.name}</strong>
                              <small>{a.media_type}</small>
                            </span>
                          </button>
                        </td>
                        <td>
                          <span className="tag">{a.kind}</span>
                          <span className="cell-subtext">{a.source}</span>
                        </td>
                        <td className="mono muted">{shortId(a.run_id)}</td>
                        <td className="mono">{formatBytes(a.size_bytes)}</td>
                        <td className="table-date">
                          {formatDate(a.created_at)}
                        </td>
                        <td>
                          <DownloadLink
                            className="icon-button"
                            href={a.download_url}
                            download
                            aria-label={`Download ${a.name}`}
                          >
                            <ArrowDownToLine size={16} />
                          </DownloadLink>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
      {selected && (
        <Modal
          title={selected.name}
          subtitle={`${selected.kind} · ${selected.media_type}`}
          onClose={() => {
            if (!registering) setSelected(null);
          }}
        >
          <div className="modal-body">
            {selected.media_type.startsWith("image/") && (
              <ArtifactImage
                url={selected.download_url}
                alt={`Recorded sensor artifact ${selected.name}`}
              />
            )}
            <dl className="artifact-metadata">
              <div>
                <dt>Source</dt>
                <dd>{selected.source}</dd>
              </div>
              <div>
                <dt>Run</dt>
                <dd className="mono">{selected.run_id}</dd>
              </div>
              <div>
                <dt>Size</dt>
                <dd>{formatBytes(selected.size_bytes)}</dd>
              </div>
              <div>
                <dt>SHA-256</dt>
                <dd>
                  <code>{selected.sha256}</code>
                  <CopyButton text={selected.sha256} />
                </dd>
              </div>
            </dl>
            {registrationError && (
              <p className="inline-error" role="alert">
                {registrationError}
              </p>
            )}
          </div>
          <footer>
            {selected.media_type === "application/mcap" && (
              <button
                type="button"
                className="button secondary"
                disabled={registering}
                onClick={() => openRecording(selected)}
              >
                {registering ? (
                  <LoaderCircle size={15} className="spin" />
                ) : (
                  <FileArchive size={15} />
                )}
                Open as recording
              </button>
            )}
            <DownloadLink
              className="button primary"
              href={selected.download_url}
              download
            >
              <ArrowDownToLine size={15} />
              Download artifact
            </DownloadLink>
          </footer>
        </Modal>
      )}
    </>
  );
}
