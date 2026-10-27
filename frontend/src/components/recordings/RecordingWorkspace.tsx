import { useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileArchive,
  FolderOpen,
  RefreshCw,
  Upload,
} from "lucide-react";
import { formatBytes, useResource } from "../../api";
import { Empty, ErrorState, Loading, SearchField } from "../ui";
import RecordingDetail from "./RecordingDetail";
import RecordingUpload from "./RecordingUpload";
import type { InferenceSource } from "../perception/PerceptionTypes";
import { recordingDurationNs, secondsFromNanoseconds } from "./RecordingTime";
import type {
  RecordingDetail as Detail,
  RecordingSummary,
} from "./RecordingTypes";

export default function RecordingWorkspace({
  initialRecordingId,
  onOpenRun,
  onInfer,
}: {
  initialRecordingId?: string | null;
  onOpenRun?: (id: string) => void;
  onInfer?: (source: InferenceSource) => void;
}) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(
    initialRecordingId ?? null,
  );
  const [uploadOpen, setUploadOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const recordings = useResource<RecordingSummary[]>(
    `/recordings${query ? `?q=${encodeURIComponent(query)}` : ""}`,
  );
  const detail = useResource<Detail>(
    selectedId ? `/recordings/${encodeURIComponent(selectedId)}` : null,
  );
  useEffect(() => {
    if (!selectedId && recordings.data?.[0])
      setSelectedId(recordings.data[0].id);
  }, [recordings.data, selectedId]);
  function imported(recording: Detail, duplicate: boolean) {
    setUploadOpen(false);
    setQuery("");
    setSelectedId(recording.id);
    recordings.reload();
    detail.reload();
    setNotice(
      duplicate
        ? "This recording is already in the catalog with identical bytes and declared metadata."
        : "Recording imported. Original bytes, channels, schemas, and a traceable seek index are available.",
    );
  }
  function refresh() {
    recordings.reload();
    detail.reload();
  }
  return (
    <div className="recordings-workspace">
      <div className="recording-workspace-toolbar">
        <details className="recording-scope-disclosure">
          <summary>
            <AlertCircle size={15} />
            Import & decoding scope
            <ChevronDown size={14} />
          </summary>
          <p>
            Container parsing, payload decoding, and sensor validation are
            separate. Calibration and transforms remain unknown unless recorded;
            recorded declarations are not automatically verified.
          </p>
        </details>
        <div>
          <button
            type="button"
            className="button secondary"
            onClick={refresh}
            aria-label="Refresh recordings"
          >
            <RefreshCw size={15} />
            Refresh
          </button>
          <button
            type="button"
            className="button primary"
            onClick={() => setUploadOpen(true)}
          >
            <Upload size={15} />
            Import MCAP
          </button>
        </div>
      </div>
      {notice && (
        <div className="recording-success" role="status">
          <CheckCircle2 size={17} />
          <span>{notice}</span>
          <button
            className="icon-button"
            aria-label="Dismiss recording notification"
            onClick={() => setNotice("")}
          >
            ×
          </button>
        </div>
      )}
      <div className="recording-explorer-grid">
        <aside
          className="panel recording-library"
          aria-label="Recording library"
        >
          <div className="recording-library-heading">
            <h3>
              <FolderOpen size={16} />
              Recordings
            </h3>
            <span>{recordings.data?.length ?? "…"}</span>
          </div>
          <SearchField
            value={query}
            onChange={setQuery}
            placeholder="Search recordings or sources…"
          />
          {recordings.loading ? (
            <Loading label="Loading recordings…" />
          ) : recordings.error ? (
            <ErrorState message={recordings.error} retry={recordings.reload} />
          ) : !recordings.data?.length ? (
            <div className="recording-library-empty">
              <FileArchive size={26} />
              <strong>
                {query
                  ? "No matching recordings"
                  : "Your recording library is empty"}
              </strong>
              <p>
                {query
                  ? "Adjust the search to find a recording."
                  : "Import an MCAP file to explore its channels and message timing."}
              </p>
              {!query && (
                <button
                  className="button secondary"
                  onClick={() => setUploadOpen(true)}
                >
                  Import your first MCAP
                </button>
              )}
            </div>
          ) : (
            <div className="recording-library-list">
              {recordings.data.map((recording) => (
                <button
                  type="button"
                  key={recording.id}
                  className={selectedId === recording.id ? "active" : ""}
                  aria-label={`Open recording ${recording.name}`}
                  aria-pressed={selectedId === recording.id}
                  onClick={() => {
                    setSelectedId(recording.id);
                    setNotice("");
                  }}
                >
                  <div>
                    <span className="recording-file-mark">
                      <FileArchive size={17} />
                    </span>
                    <strong>{recording.name}</strong>
                    <ChevronRight size={14} />
                  </div>
                  <p>{recording.source || "No source label"}</p>
                  <span className="recording-library-facts">
                    <span>
                      {recording.message_count.toLocaleString()} messages
                    </span>
                    <span>{recording.channel_count} channels</span>
                  </span>
                  <span className="recording-library-facts">
                    <span>
                      {secondsFromNanoseconds(
                        recordingDurationNs(
                          recording.start_time_ns,
                          recording.end_time_ns,
                        ),
                        6,
                      )}{" "}
                      s
                    </span>
                    <span>{formatBytes(recording.size_bytes)}</span>
                  </span>
                  <span className="recording-library-footer">
                    <span>{recording.source_kind} · declared</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </aside>
        <div className="recording-explorer-main">
          {detail.error ? (
            <section className="panel">
              <ErrorState message={detail.error} retry={detail.reload} />
            </section>
          ) : selectedId &&
            (detail.loading || detail.data?.id !== selectedId) ? (
            <section className="panel">
              <Loading label="Loading original recording metadata…" />
            </section>
          ) : detail.data ? (
            <RecordingDetail
              key={detail.data.id}
              recording={detail.data}
              onOpenRun={onOpenRun}
              onInfer={onInfer}
            />
          ) : (
            <section className="panel recording-initial-state">
              <Empty
                title="Choose a recording to begin"
                description="Inspect channel definitions, exact log times, and the payloads that were actually stored."
                action={
                  <button
                    className="button primary"
                    onClick={() => setUploadOpen(true)}
                  >
                    <Upload size={15} />
                    Import MCAP
                  </button>
                }
              />
              <div className="recording-initial-capabilities">
                <span>Byte-preserving import</span>
                <span>Channel-ID-aware seeking</span>
                <span>Exact nanosecond cursors</span>
                <span>JSON & opaque payloads</span>
              </div>
            </section>
          )}
        </div>
      </div>
      {uploadOpen && (
        <RecordingUpload
          onClose={() => setUploadOpen(false)}
          onImported={imported}
          onRefresh={recordings.reload}
        />
      )}
    </div>
  );
}
