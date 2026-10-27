import { useEffect, useState } from "react";
import { ArrowDownToLine, Cpu, GitBranch, Plus } from "lucide-react";
import { formatBytes, formatDate } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import { CopyButton, Empty, ErrorState, Loading } from "../ui";
import { useSensorResource } from "../sensors/SensorData";
import ModelRegistration from "./ModelRegistration";
import type { ModelDetail, RuntimeMetadata } from "./PerceptionTypes";

export default function ModelRegistry({
  models,
  runtimes,
  onRefresh,
  onLaunch,
}: {
  models: ModelDetail[];
  runtimes: RuntimeMetadata[];
  onRefresh: () => void;
  onLaunch: (model: ModelDetail) => void;
}) {
  const [id, setId] = useState(""),
    [revision, setRevision] = useState(1);
  const [editor, setEditor] = useState<ModelDetail | "new" | null>(null);
  const revisions = useSensorResource<ModelDetail[]>(
    id ? `/perception/models/${encodeURIComponent(id)}/revisions` : null,
  );
  useEffect(() => {
    if (!id && models[0]) {
      setId(models[0].id);
      setRevision(models[0].revision);
    }
  }, [id, models]);
  const selected =
    revisions.data?.find(
      (model) => model.id === id && model.revision === revision,
    ) ?? null;
  const head = models.find((model) => model.id === id);
  const available =
    selected &&
    runtimes.some(
      (runtime) =>
        runtime.status === "available" &&
        runtime.profiles.includes(selected.profile),
    );
  function saved(model: ModelDetail) {
    setEditor(null);
    setId(model.id);
    setRevision(model.revision);
    onRefresh();
    revisions.reload();
  }
  return (
    <div className="perception-models">
      <div className="perception-section-toolbar">
        <div>
          <h2>Model registry</h2>
          <p>
            Immutable weights, ordered classes, and reproducible input/output
            profiles.
          </p>
        </div>
        <button className="button primary" onClick={() => setEditor("new")}>
          <Plus size={15} />
          Register model
        </button>
      </div>
      <div className="perception-runtime-grid">
        {runtimes.map((runtime) => (
          <section className="panel perception-runtime" key={runtime.id}>
            <div>
              <Cpu size={19} />
              <strong>{runtime.name}</strong>
              <span
                className={`perception-pill ${runtime.status === "available" ? "ready" : "warning"}`}
              >
                {runtime.status.replaceAll("_", " ")}
              </span>
            </div>
            <p>
              {runtime.id} · {runtime.version}
            </p>
            <small>{runtime.profiles.join(" · ")}</small>
            <details>
              <summary>Runtime limits & scope</summary>
              <pre>{JSON.stringify(runtime.limits, null, 2)}</pre>
              {runtime.limitations.map((note, index) => (
                <p key={index}>{note}</p>
              ))}
            </details>
          </section>
        ))}
      </div>
      {!models.length ? (
        <section className="panel">
          <Empty
            title="Register your first model"
            description="Add a local ONNX file, declare its profile and ordered classes, then run it on stored camera images."
          />
        </section>
      ) : (
        <section
          className="panel perception-model-detail"
          data-testid="perception-model-detail"
        >
          <div className="perception-form-grid two">
            <label>
              Model
              <select
                aria-label="Select perception model"
                value={id}
                onChange={(e) => {
                  const model = models.find(
                    (item) => item.id === e.target.value,
                  )!;
                  setId(model.id);
                  setRevision(model.revision);
                }}
              >
                {models.map((model) => (
                  <option value={model.id} key={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Immutable revision
              <select
                aria-label="Perception model revision"
                value={revision}
                onChange={(e) => setRevision(Number(e.target.value))}
              >
                {revisions.data?.map((model) => (
                  <option value={model.revision} key={model.revision}>
                    r{model.revision} · {formatDate(model.created_at)}
                  </option>
                )) ?? <option value={revision}>r{revision}</option>}
              </select>
            </label>
          </div>
          {revisions.error ? (
            <ErrorState message={revisions.error} retry={revisions.reload} />
          ) : !selected ? (
            <Loading label="Loading immutable model declaration…" />
          ) : (
            <>
              <div className="perception-section-toolbar">
                <div>
                  <span className="perception-eyebrow">
                    MODEL DECLARATION · R{selected.revision}
                  </span>
                  <h2>{selected.name}</h2>
                  <p>{selected.description || selected.note}</p>
                </div>
                <div className="perception-actions">
                  <button
                    className="button secondary"
                    onClick={() => setEditor(selected)}
                  >
                    <GitBranch size={15} />
                    Create model revision
                  </button>
                  <button
                    className="button primary"
                    disabled={!available}
                    onClick={() => onLaunch(selected)}
                  >
                    Run model
                  </button>
                </div>
              </div>
              <p className="perception-scope">
                Registered model · compatibility and prediction accuracy are
                unverified.{" "}
                {available
                  ? "A runtime for this profile is available; actual execution validates the graph and output."
                  : "No available runtime advertises this profile."}
              </p>
              <div className="perception-definition-grid">
                <div>
                  <span>Profile</span>
                  <strong>{selected.profile}</strong>
                </div>
                <div>
                  <span>Model input</span>
                  <strong>
                    {selected.input_width} × {selected.input_height}
                  </strong>
                </div>
                <div>
                  <span>Ordered classes</span>
                  <strong>{selected.labels.length}</strong>
                </div>
                <div>
                  <span>Weights</span>
                  <strong>{formatBytes(selected.artifact.size_bytes)}</strong>
                </div>
              </div>
              <div className="perception-hash">
                <span>Definition SHA-256</span>
                <code>{selected.manifest_hash}</code>
                <CopyButton
                  text={selected.manifest_hash}
                  label="Copy model manifest hash"
                />
              </div>
              <div className="perception-hash">
                <span>Weights SHA-256</span>
                <code>{selected.artifact.sha256}</code>
              </div>
              <div className="perception-hash">
                <span>Label map SHA-256</span>
                <code>{selected.label_map_hash}</code>
              </div>
              <details className="perception-details">
                <summary>Ordered classes & source declarations</summary>
                <div className="perception-class-map">
                  {selected.labels.map((label, index) => (
                    <span key={label}>
                      <b>{index}</b>
                      {label}
                    </span>
                  ))}
                </div>
                <dl className="perception-evidence-list">
                  {(
                    [
                      ["Source", selected.source],
                      ["Source revision", selected.source_revision],
                      ["Weights license", selected.weights_license],
                      [
                        "Weights license URL",
                        selected.weights_license_url || "Not supplied",
                      ],
                      ["Code license", selected.code_license],
                      ["Training data", selected.training_data_note],
                      ["Revision note", selected.note],
                    ] as const
                  ).map(([name, value]) => (
                    <div key={name}>
                      <dt>{name}</dt>
                      <dd>{value || "Unknown"}</dd>
                    </div>
                  ))}
                </dl>
              </details>
              <div className="perception-actions">
                <DownloadLink
                  className="button secondary small"
                  href={selected.download_url}
                  download
                >
                  <ArrowDownToLine size={14} />
                  Download model manifest
                </DownloadLink>
                <DownloadLink
                  className="button secondary small"
                  href={selected.artifact.download_url}
                  download
                >
                  Download original weights
                </DownloadLink>
              </div>
            </>
          )}
        </section>
      )}
      {editor && (
        <ModelRegistration
          model={editor === "new" ? undefined : editor}
          expectedRevision={head?.revision}
          onClose={() => setEditor(null)}
          onSaved={saved}
        />
      )}
    </div>
  );
}
