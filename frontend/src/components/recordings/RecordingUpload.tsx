import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  AlertCircle,
  Check,
  FileArchive,
  LoaderCircle,
  Upload,
} from "lucide-react";
import { authHeaders, formatBytes } from "../../api";
import { Modal } from "../ui";
import {
  MAX_RECORDING_UPLOAD_BYTES,
  type RecordingDetail,
  type RecordingSourceKind,
} from "./RecordingTypes";

function uploadError(status: number, response: unknown): string {
  const body = response as {
    detail?: string | { loc?: (string | number)[]; msg?: string }[];
  } | null;
  let message =
    typeof body?.detail === "string"
      ? body.detail
      : Array.isArray(body?.detail)
        ? body.detail
            .map(
              (issue) =>
                `${issue.loc?.filter((part) => part !== "query").join(" · ") || "Input"}: ${issue.msg || "Invalid value"}`,
            )
            .join(". ")
        : `Import failed (${status}). The file was not accepted.`;
  if (status === 401)
    message += " Open Workspace settings to enter the API bearer token.";
  return message;
}

export default function RecordingUpload({
  onClose,
  onImported,
  onRefresh,
}: {
  onClose: () => void;
  onImported: (recording: RecordingDetail, duplicate: boolean) => void;
  onRefresh: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [sourceKind, setSourceKind] = useState<RecordingSourceKind>("unknown");
  const [source, setSource] = useState("");
  const [pending, setPending] = useState(false);
  const [uploaded, setUploaded] = useState(0);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<XMLHttpRequest | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  function chooseFile(candidate: File | null) {
    setError("");
    if (candidate && candidate.size > MAX_RECORDING_UPLOAD_BYTES) {
      setFile(null);
      setError(
        "This file exceeds the 128 MiB import limit. Select a smaller MCAP recording.",
      );
      return;
    }
    if (candidate && candidate.size === 0) {
      setFile(null);
      setError(
        "The selected file is empty. Select an MCAP file containing a valid container.",
      );
      return;
    }
    setFile(candidate);
    if (candidate && !name)
      setName(candidate.name.replace(/\.mcap$/i, "").slice(0, 180));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("Choose an MCAP file before importing.");
      return;
    }
    if (!name.trim()) {
      setError("Enter a recording name.");
      return;
    }
    setPending(true);
    setProcessing(false);
    setUploaded(0);
    setError("");
    const query = new URLSearchParams({
      name: name.trim(),
      source_kind: sourceKind,
      source: source.trim(),
    });
    const xhr = new XMLHttpRequest();
    request.current = xhr;
    xhr.open("POST", `/api/v1/recordings?${query.toString()}`);
    xhr.responseType = "json";
    xhr.timeout = 300_000;
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    Object.entries(authHeaders()).forEach(([key, value]) =>
      xhr.setRequestHeader(key, value),
    );
    xhr.upload.onprogress = (progress) => {
      setUploaded(progress.loaded);
      if (progress.lengthComputable && progress.loaded >= progress.total)
        setProcessing(true);
    };
    xhr.upload.onload = () => {
      setUploaded(file.size);
      setProcessing(true);
    };
    xhr.onload = () => {
      request.current = null;
      setPending(false);
      if (xhr.status === 200 || xhr.status === 201) {
        onImported(xhr.response as RecordingDetail, xhr.status === 200);
      } else {
        setError(uploadError(xhr.status, xhr.response));
      }
    };
    xhr.onerror = () => {
      request.current = null;
      setPending(false);
      setError(
        "The upload connection failed. Refresh the catalog before retrying to check whether import completed.",
      );
      onRefresh();
    };
    xhr.ontimeout = () => {
      request.current = null;
      setPending(false);
      setError(
        "Import did not respond within five minutes. Refresh the catalog to check its state before retrying.",
      );
      onRefresh();
    };
    xhr.onabort = () => {
      request.current = null;
      setPending(false);
      setProcessing(false);
      setError(
        "Transfer canceled. The catalog has been refreshed; if processing had already started, check again before retrying.",
      );
      onRefresh();
    };
    xhr.send(file);
  }

  const progress = file
    ? Math.min(100, Math.round((uploaded / file.size) * 100))
    : 0;
  return (
    <Modal
      title="Import an MCAP recording"
      subtitle="Preserve the original bytes and record their declared source."
      onClose={() => {
        if (!pending) onClose();
      }}
    >
      <form onSubmit={submit} className="recording-import-form">
        <div className="modal-body">
          <label>
            Recording name
            <input
              autoFocus
              required
              maxLength={180}
              value={name}
              disabled={pending}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Synthetic braking telemetry"
            />
          </label>
          <div className="form-grid">
            <label>
              Source kind
              <select
                value={sourceKind}
                disabled={pending}
                onChange={(event) =>
                  setSourceKind(event.target.value as RecordingSourceKind)
                }
              >
                <option value="unknown">Unknown / not declared</option>
                <option value="synthetic">Synthetic (declared)</option>
                <option value="simulation">Simulation (declared)</option>
                <option value="measured">Measured (declared)</option>
              </select>
            </label>
            <label>
              Source label <span className="optional">optional</span>
              <input
                maxLength={1000}
                disabled={pending}
                value={source}
                onChange={(event) => setSource(event.target.value)}
                placeholder="Producer, dataset, or collection context"
              />
            </label>
          </div>
          <div className="recording-upload-zone">
            <FileArchive size={27} />
            <strong>
              {file ? file.name : "Choose a recording to inspect"}
            </strong>
            <p>MCAP container · up to 128 MiB · original bytes preserved</p>
            <input
              type="file"
              aria-label="MCAP file"
              accept=".mcap,application/octet-stream"
              disabled={pending}
              onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
            />
            {file && (
              <span className="recording-file-ready">
                <Check size={14} />
                {formatBytes(file.size)} selected
              </span>
            )}
          </div>
          <div className="recording-notice">
            <AlertCircle size={17} />
            <p>
              Source kind is user-declared. Import acceptance does not verify
              measured origin, calibration, payload encoding, or
              sensor/perception performance.
            </p>
          </div>
          {pending && (
            <div className="recording-upload-progress" role="status">
              <div>
                <span>
                  {processing
                    ? "Upload complete · validating MCAP content…"
                    : "Uploading original file…"}
                </span>
                <strong>{progress}% transferred</strong>
              </div>
              <progress
                aria-label="MCAP upload progress"
                value={progress}
                max={100}
              />
              <p>
                {formatBytes(uploaded)} of {formatBytes(file?.size ?? 0)}{" "}
                transferred. The recording appears after server validation
                completes.
              </p>
            </div>
          )}
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
        </div>
        <footer>
          {pending ? (
            <button
              className="button secondary"
              type="button"
              onClick={() => request.current?.abort()}
            >
              Cancel transfer
            </button>
          ) : (
            <button
              className="button secondary"
              type="button"
              onClick={onClose}
            >
              Cancel
            </button>
          )}
          <button
            type="submit"
            className="button primary"
            disabled={pending || !file}
          >
            {pending ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Upload size={15} />
            )}{" "}
            {pending ? "Importing…" : "Import recording"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
