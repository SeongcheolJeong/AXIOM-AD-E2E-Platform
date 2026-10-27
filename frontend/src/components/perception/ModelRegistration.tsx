import { useRef, useState, type FormEvent } from "react";
import { FileUp, LoaderCircle } from "lucide-react";
import { api, authHeaders, formatBytes } from "../../api";
import { Modal } from "../ui";
import type {
  ModelArtifact,
  ModelCreate,
  ModelDetail,
} from "./PerceptionTypes";

const empty: ModelCreate = {
  name: "",
  description: "",
  artifact_id: "",
  profile: "opencv_yolox_rgb_v1",
  input_width: 640,
  input_height: 640,
  labels: [],
  source: "unknown",
  source_revision: "unknown",
  weights_license: "unknown",
  weights_license_url: "",
  code_license: "unknown",
  training_data_note: "unknown",
  note: "",
};
function authoring(model?: ModelDetail): ModelCreate {
  if (!model) return empty;
  return Object.fromEntries(
    Object.keys(empty).map((key) => [key, model[key as keyof ModelCreate]]),
  ) as unknown as ModelCreate;
}
export default function ModelRegistration({
  model,
  expectedRevision,
  onClose,
  onSaved,
}: {
  model?: ModelDetail;
  expectedRevision?: number;
  onClose: () => void;
  onSaved: (model: ModelDetail) => void;
}) {
  const [form, setForm] = useState(() => authoring(model));
  const [guard] = useState(expectedRevision ?? model?.revision ?? 1);
  const [labels, setLabels] = useState(model?.labels.join("\n") ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [artifact, setArtifact] = useState<ModelArtifact | null>(
    model?.artifact ?? null,
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [upload, setUpload] = useState<number | null>(null);
  const xhr = useRef<XMLHttpRequest | null>(null);
  function update<K extends keyof ModelCreate>(key: K, value: ModelCreate[K]) {
    setForm((v) => ({ ...v, [key]: value }));
  }
  async function uploadFile(selected: File): Promise<ModelArtifact> {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      xhr.current = request;
      request.open(
        "POST",
        `/api/v1/perception/model-artifacts?name=${encodeURIComponent(selected.name)}`,
      );
      request.setRequestHeader("Content-Type", "application/octet-stream");
      Object.entries(authHeaders()).forEach(([key, value]) =>
        request.setRequestHeader(key, value),
      );
      request.upload.onprogress = (event) => {
        if (event.lengthComputable)
          setUpload(Math.round((event.loaded / event.total) * 100));
      };
      request.onload = () => {
        xhr.current = null;
        try {
          const data = JSON.parse(request.responseText);
          if (request.status < 200 || request.status >= 300)
            throw new Error(
              typeof data.detail === "string"
                ? data.detail
                : `Upload failed (${request.status}).`,
            );
          resolve(data as ModelArtifact);
        } catch (reason) {
          reject(reason);
        }
      };
      request.onerror = () => {
        xhr.current = null;
        reject(
          new Error(
            "Model upload response was lost. Retry the same file to recover its registration.",
          ),
        );
      };
      request.onabort = () => {
        xhr.current = null;
        reject(
          new Error(
            "Upload stopped in this browser. If storage already completed, the same file can recover its artifact.",
          ),
        );
      };
      request.send(selected);
    });
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    const ordered = labels
      .trim()
      .split("\n")
      .map((label) => label.trim());
    if (
      !ordered.length ||
      ordered.length > 256 ||
      new Set(ordered).size !== ordered.length ||
      ordered.some((label) => !label.length || label.length > 128)
    ) {
      setError(
        "Provide 1–256 unique class labels, one per line without empty lines, each at most 128 characters. Line order defines class IDs from zero.",
      );
      return;
    }
    if (!file && !artifact) {
      setError("Choose an original ONNX file before registering a model.");
      return;
    }
    if (file && (file.size < 1 || file.size > 128 * 1024 * 1024)) {
      setError("The ONNX file must contain 1 byte to 128 MiB.");
      return;
    }
    setBusy(true);
    try {
      let savedArtifact = artifact;
      if (file && !artifact) {
        setUpload(0);
        savedArtifact = await uploadFile(file);
        setArtifact(savedArtifact);
        setUpload(null);
      }
      const body = {
        ...form,
        artifact_id: savedArtifact!.id,
        labels: ordered,
        ...(model ? { expected_revision: guard } : {}),
      };
      const saved = await api<ModelDetail>(
        model
          ? `/perception/models/${encodeURIComponent(model.id)}/revisions`
          : "/perception/models",
        { method: "POST", body: JSON.stringify(body) },
      );
      onSaved(saved);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Model registration failed.",
      );
    } finally {
      setBusy(false);
      setUpload(null);
    }
  }
  const provenance: {
    key:
      | "source"
      | "source_revision"
      | "weights_license"
      | "weights_license_url"
      | "code_license";
    label: string;
    max: number;
  }[] = [
    { key: "source", label: "Model source", max: 2000 },
    { key: "source_revision", label: "Source revision", max: 300 },
    { key: "weights_license", label: "Weights license", max: 300 },
    { key: "weights_license_url", label: "Weights license URL", max: 2000 },
    { key: "code_license", label: "Code license", max: 300 },
  ];
  return (
    <Modal
      title={model ? "Create model revision" : "Register perception model"}
      subtitle="Store original weights and an immutable input/output declaration. Registration does not execute the model."
      onClose={() => {
        if (!busy) onClose();
      }}
      className="perception-modal"
    >
      <form className="perception-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <div className="perception-form-grid two">
            <label>
              Model name
              <input
                aria-label="Model name"
                required
                maxLength={180}
                value={form.name}
                onChange={(e) => update("name", e.target.value)}
              />
            </label>
            <label>
              Inference profile
              <input
                aria-label="Inference profile"
                required
                maxLength={80}
                pattern="[a-zA-Z0-9_.\-]+"
                list="perception-profile-options"
                value={form.profile}
                onChange={(e) => update("profile", e.target.value)}
              />
              <datalist id="perception-profile-options">
                <option value="opencv_yolox_rgb_v1" />
                <option value="boxes_xyxy_v1" />
              </datalist>
            </label>
          </div>
          <label>
            Description
            <textarea
              aria-label="Model description"
              maxLength={4000}
              rows={2}
              value={form.description}
              onChange={(e) => update("description", e.target.value)}
            />
          </label>
          <label className="perception-upload">
            <FileUp size={20} />
            Original ONNX weights
            <input
              aria-label="ONNX model file"
              type="file"
              accept=".onnx,application/octet-stream"
              required={!artifact}
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setArtifact(null);
              }}
            />
            <small>
              {file
                ? `${file.name} · ${formatBytes(file.size)}`
                : artifact
                  ? `Retain ${artifact.name} · ${formatBytes(artifact.size_bytes)}`
                  : "A single local ONNX file, up to 128 MiB. External data files are unsupported."}
            </small>
          </label>
          {artifact && (
            <p className="perception-hash">
              Weight SHA-256 <code>{artifact.sha256}</code>
            </p>
          )}
          <div className="perception-form-grid two">
            <label>
              Input width
              <input
                aria-label="Model input width"
                type="number"
                min={32}
                max={1280}
                step={1}
                required
                value={form.input_width}
                onChange={(e) => update("input_width", Number(e.target.value))}
              />
            </label>
            <label>
              Input height
              <input
                aria-label="Model input height"
                type="number"
                min={32}
                max={1280}
                step={1}
                required
                value={form.input_height}
                onChange={(e) => update("input_height", Number(e.target.value))}
              />
            </label>
          </div>
          <label>
            Ordered class labels
            <textarea
              aria-label="Ordered class labels"
              aria-describedby="model-label-help"
              required
              rows={5}
              value={labels}
              onChange={(e) => setLabels(e.target.value)}
              placeholder="One class per line"
            />
            <small id="model-label-help">
              First line = class 0. The ordered label map is captured and
              hashed.
            </small>
          </label>
          <details className="perception-provenance-form" open>
            <summary>Source & license declarations</summary>
            <div className="perception-form-grid two">
              {provenance.map((field) => (
                <label key={field.key}>
                  {field.label}
                  <input
                    aria-label={field.label}
                    maxLength={field.max}
                    value={form[field.key]}
                    onChange={(e) => update(field.key, e.target.value)}
                  />
                </label>
              ))}
            </div>
            <label>
              Training data note
              <textarea
                aria-label="Training data note"
                maxLength={4000}
                rows={2}
                value={form.training_data_note}
                onChange={(e) => update("training_data_note", e.target.value)}
              />
            </label>
            <p>
              Use “unknown” when provenance is unavailable. Weights, code, and
              training data have separate declarations; registration does not
              approve their use.
            </p>
          </details>
          <label>
            Revision note
            <textarea
              aria-label="Model revision note"
              required
              maxLength={4000}
              rows={2}
              value={form.note}
              onChange={(e) => update("note", e.target.value)}
            />
          </label>
          {model && (
            <p className="perception-muted">
              Creating from revision {model.revision}. Expected authoring head:
              r{guard}. Old revisions and weights remain unchanged.
            </p>
          )}
        </fieldset>
        {upload !== null && (
          <div className="perception-upload-progress" role="status">
            <progress max={100} value={upload} />
            <span>
              {upload}% bytes sent
              {upload === 100 ? " · awaiting artifact registration" : ""}
            </span>
            <button
              type="button"
              className="button secondary small"
              onClick={() => xhr.current?.abort()}
            >
              Stop upload
            </button>
          </div>
        )}
        {error && (
          <div role="alert" className="inline-error">
            {error}
            <p>
              If the model response was lost, refresh the registry before
              creating another definition. Artifact uploads deduplicate
              identical bytes and names.
            </p>
          </div>
        )}
        <footer>
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button className="button primary" disabled={busy}>
            {busy && <LoaderCircle size={15} className="spin" />}
            {busy
              ? "Saving model…"
              : model
                ? "Publish model revision"
                : "Register model"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
