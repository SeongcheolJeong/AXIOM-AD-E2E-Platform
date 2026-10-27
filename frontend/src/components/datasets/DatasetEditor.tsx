import { useState } from "react";
import {
  Camera,
  CheckCircle2,
  FileEdit,
  LoaderCircle,
  Plus,
  Trash2,
} from "lucide-react";
import { api } from "../../api";
import { Modal } from "../ui";
import type { InferenceSource } from "../perception/PerceptionTypes";
import AnnotationEditor from "./AnnotationEditor";
import DatasetImageCapture from "./DatasetImageCapture";
import { AnnotationLineage } from "./AnnotationAssist";
import type {
  AnnotationSource,
  DatasetCreate,
  DatasetDetail,
  DatasetImage,
  DatasetMember,
  DatasetSplit,
} from "./DatasetTypes";

const defaults: DatasetCreate = {
  name: "",
  description: "",
  labels: [],
  source: "unknown",
  source_revision: "unknown",
  license: "unknown",
  note: "",
  images: [],
};
const pick = (dataset?: DatasetDetail): DatasetCreate =>
  dataset
    ? (Object.fromEntries(
        Object.keys(defaults).map((key) => [
          key,
          dataset[key as keyof DatasetCreate],
        ]),
      ) as unknown as DatasetCreate)
    : defaults;
function declaredReview(member: DatasetMember) {
  return (
    member.review_status === "reviewed" &&
    Boolean(member.reviewer.trim()) &&
    Boolean(member.review_note.trim()) &&
    member.annotation_source !== "unknown"
  );
}

export default function DatasetEditor({
  dataset,
  expectedRevision,
  source,
  onClose,
  onSaved,
}: {
  dataset?: DatasetDetail;
  expectedRevision?: number;
  source?: InferenceSource | null;
  onClose: () => void;
  onSaved: (dataset: DatasetDetail) => void;
}) {
  const [form, setForm] = useState(() => pick(dataset));
  const [guard] = useState(expectedRevision ?? dataset?.revision ?? 1);
  const [labels, setLabels] = useState(dataset?.labels.join("\n") ?? "");
  const [assets, setAssets] = useState<DatasetImage[]>(
    dataset?.image_assets ?? [],
  );
  const [selectedId, setSelectedId] = useState(
    dataset?.images[0]?.image_id ?? "",
  );
  const [capture, setCapture] = useState(Boolean(source));
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const taxonomy = labels.trim()
    ? labels
        .trim()
        .split("\n")
        .map((label) => label.trim())
    : [];
  const selected = form.images.find((member) => member.image_id === selectedId);
  const asset = assets.find((image) => image.id === selectedId);
  function update<K extends keyof DatasetCreate>(
    field: K,
    value: DatasetCreate[K],
  ) {
    setForm((previous) => ({ ...previous, [field]: value }));
  }
  function updateMember(patch: Partial<DatasetMember>) {
    setForm((previous) => ({
      ...previous,
      images: previous.images.map((member) =>
        member.image_id === selectedId ? { ...member, ...patch } : member,
      ),
    }));
  }
  function captured(image: DatasetImage) {
    if (!assets.some((asset) => asset.id === image.id))
      setAssets((previous) => [...previous, image]);
    if (!form.images.some((member) => member.image_id === image.id))
      update("images", [
        ...form.images,
        {
          image_id: image.id,
          split: "unspecified",
          review_status: "unreviewed",
          reviewer: "",
          review_note: "",
          annotation_source: "unknown",
          annotations: [],
        },
      ]);
    setSelectedId(image.id);
    setCapture(false);
  }
  async function publish() {
    setError("");
    if (!form.name.trim() || !form.note.trim()) {
      setError("Dataset name and revision note are required.");
      return;
    }
    if (
      !taxonomy.length ||
      taxonomy.length > 256 ||
      new Set(taxonomy).size !== taxonomy.length ||
      taxonomy.some((label) => !label || label.length > 128)
    ) {
      setError(
        "Provide 1–256 unique, case-sensitive class labels, one per line without empty lines, each at most 128 characters.",
      );
      return;
    }
    if (!form.images.length || form.images.length > 256) {
      setError("Capture 1–256 original image assets before publishing.");
      return;
    }
    const incomplete = form.images.find(
      (member) =>
        member.review_status === "reviewed" && !declaredReview(member),
    );
    if (incomplete) {
      setSelectedId(incomplete.image_id);
      setError(
        "A reviewed image needs an explicit annotation source, reviewer, and review note. Empty reviewed annotations declare a negative image.",
      );
      return;
    }
    const unmapped = form.images.find((member) =>
      member.annotations.some(
        (annotation) => !taxonomy.includes(annotation.label),
      ),
    );
    if (unmapped) {
      setSelectedId(unmapped.image_id);
      setError(
        "An annotation label no longer belongs to the taxonomy. Select its intended class explicitly before publishing.",
      );
      return;
    }
    setBusy(true);
    try {
      const saved = await api<DatasetDetail>(
        dataset
          ? `/datasets/${encodeURIComponent(dataset.id)}/revisions`
          : "/datasets",
        {
          method: "POST",
          body: JSON.stringify({
            ...form,
            labels: taxonomy,
            ...(dataset ? { expected_revision: guard } : {}),
          }),
        },
      );
      onSaved(saved);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Dataset publication failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={dataset ? "Create dataset revision" : "Create detection dataset"}
      subtitle="Author labels on exact source images. Published images, annotations, and review declarations are immutable."
      onClose={() => {
        if (!busy) onClose();
      }}
      className="dataset-editor-modal"
    >
      <div className="dataset-editor-body">
        {dataset && <AnnotationLineage dataset={dataset} />}
        <fieldset disabled={busy} className="dataset-definition-fields">
          <div className="dataset-form-grid two">
            <label>
              Dataset name
              <input
                aria-label="Dataset name"
                required
                maxLength={180}
                value={form.name}
                onChange={(e) => update("name", e.target.value)}
              />
            </label>
            <label>
              Revision note
              <input
                aria-label="Dataset revision note"
                required
                maxLength={4000}
                value={form.note}
                onChange={(e) => update("note", e.target.value)}
              />
            </label>
          </div>
          <details className="dataset-authoring-details" open={!dataset}>
            <summary>Taxonomy, description & source declarations</summary>
            <div className="dataset-form-grid two">
              <label>
                Ordered dataset labels
                <textarea
                  aria-label="Dataset class labels"
                  rows={4}
                  value={labels}
                  onChange={(e) => {
                    setLabels(e.target.value);
                    setForm((previous) => ({
                      ...previous,
                      images: previous.images.map((member) => ({
                        ...member,
                        review_status: "unreviewed",
                        reviewer: "",
                        review_note: "",
                      })),
                    }));
                  }}
                  placeholder="One case-sensitive class per line"
                />
              </label>
              <label>
                Description
                <textarea
                  aria-label="Dataset description"
                  maxLength={4000}
                  rows={4}
                  value={form.description}
                  onChange={(e) => update("description", e.target.value)}
                />
              </label>
            </div>
            <div className="dataset-form-grid three">
              {(
                [
                  ["source", "Dataset source", 2000],
                  ["source_revision", "Dataset source revision", 300],
                  ["license", "Dataset license", 1000],
                ] as const
              ).map(([key, label, max]) => (
                <label key={key}>
                  {label}
                  <input
                    aria-label={label}
                    maxLength={max}
                    value={form[key]}
                    onChange={(e) => update(key, e.target.value)}
                  />
                </label>
              ))}
            </div>
            <p className="dataset-muted">
              Source and rights are declarations. Changing the taxonomy returns
              all draft members to unreviewed. Exact duplicate PNG bytes across
              assigned splits are rejected; near-duplicates and training-set
              independence are not established.
            </p>
          </details>
        </fieldset>
        <div className="dataset-editor-workspace">
          <aside
            className="dataset-member-list"
            aria-label="Dataset image members"
          >
            <div className="dataset-section-toolbar">
              <strong>{form.images.length} images</strong>
              <button
                className="button secondary small"
                type="button"
                disabled={busy || form.images.length >= 256}
                onClick={() => setCapture(true)}
              >
                <Plus size={14} />
                Add image
              </button>
            </div>
            {form.images.length ? (
              form.images.map((member, index) => {
                const image = assets.find(
                  (asset) => asset.id === member.image_id,
                );
                const reviewed = declaredReview(member);
                return (
                  <button
                    type="button"
                    key={member.image_id}
                    className="dataset-member-card"
                    aria-label={`Select dataset image ${index + 1}, ordinal ${image?.input.ordinal ?? "unknown"}`}
                    aria-pressed={selectedId === member.image_id}
                    disabled={busy}
                    onClick={() => {
                      setSelectedId(member.image_id);
                      setCapture(false);
                    }}
                  >
                    <span>
                      <Camera size={14} />
                      Ordinal #{image?.input.ordinal ?? "unknown"}
                    </span>
                    <strong>
                      {member.annotations.length} box
                      {member.annotations.length === 1 ? "" : "es"} ·{" "}
                      {member.split}
                    </strong>
                    <small>
                      {reviewed
                        ? member.annotations.length
                          ? "Reviewed labels"
                          : "Reviewed negative"
                        : "Unreviewed draft"}
                    </small>
                  </button>
                );
              })
            ) : (
              <div className="dataset-list-empty">
                <Camera size={24} />
                <p>Capture original camera messages to start annotating.</p>
              </div>
            )}
          </aside>
          <div className="dataset-editor-main">
            {capture ? (
              <DatasetImageCapture
                initialSource={source}
                onCaptured={captured}
                onClose={() => setCapture(false)}
              />
            ) : selected && asset ? (
              <>
                <div className="dataset-image-title">
                  <div>
                    <h3>
                      Image{" "}
                      {form.images.findIndex(
                        (member) => member.image_id === selectedId,
                      ) + 1}{" "}
                      · ordinal #{asset.input.ordinal}
                    </h3>
                    <span>
                      Channel #{asset.input.channel_id} · log{" "}
                      {asset.input.log_time_ns} ns
                    </span>
                  </div>
                  <button
                    type="button"
                    className="button secondary small"
                    disabled={busy}
                    onClick={() => {
                      update(
                        "images",
                        form.images.filter(
                          (member) => member.image_id !== selectedId,
                        ),
                      );
                      setSelectedId(
                        form.images.find(
                          (member) => member.image_id !== selectedId,
                        )?.image_id ?? "",
                      );
                    }}
                  >
                    <Trash2 size={13} />
                    Remove image
                  </button>
                </div>
                <AnnotationEditor
                  key={asset.id}
                  image={asset.image}
                  labels={taxonomy.filter(Boolean)}
                  annotations={selected.annotations}
                  reviewedNegative={
                    declaredReview(selected) &&
                    selected.annotations.length === 0
                  }
                  readOnly={busy}
                  onChange={(annotations) =>
                    updateMember({
                      annotations,
                      review_status: "unreviewed",
                      reviewer: "",
                      review_note: "",
                    })
                  }
                />
                <fieldset className="dataset-review-form" disabled={busy}>
                  <div className="dataset-section-toolbar">
                    <h3>
                      {declaredReview(selected) ? (
                        <CheckCircle2 size={17} />
                      ) : (
                        <FileEdit size={17} />
                      )}
                      Image review declaration
                    </h3>
                    <span
                      className={`dataset-review-badge ${declaredReview(selected) ? "reviewed" : ""}`}
                    >
                      {declaredReview(selected)
                        ? selected.annotations.length
                          ? "Reviewed labels"
                          : "Reviewed negative image"
                        : "Unreviewed / incomplete declaration"}
                    </span>
                  </div>
                  <div className="dataset-form-grid three">
                    <label>
                      Dataset split
                      <select
                        aria-label="Image dataset split"
                        value={selected.split}
                        onChange={(e) =>
                          updateMember({
                            split: e.target.value as DatasetSplit,
                          })
                        }
                      >
                        {["unspecified", "train", "validation", "test"].map(
                          (split) => (
                            <option value={split} key={split}>
                              {split}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <label>
                      Annotation source
                      <select
                        aria-label="Image annotation source"
                        value={selected.annotation_source}
                        onChange={(e) =>
                          updateMember({
                            annotation_source: e.target
                              .value as AnnotationSource,
                          })
                        }
                      >
                        {["unknown", "human", "synthetic", "imported"].map(
                          (source) => (
                            <option value={source} key={source}>
                              {source}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <label>
                      Reviewer
                      <input
                        aria-label="Image reviewer"
                        maxLength={180}
                        value={selected.reviewer}
                        onChange={(e) =>
                          updateMember({ reviewer: e.target.value })
                        }
                      />
                    </label>
                  </div>
                  <label>
                    Review note
                    <textarea
                      aria-label="Image review note"
                      rows={2}
                      maxLength={4000}
                      value={selected.review_note}
                      onChange={(e) =>
                        updateMember({ review_note: e.target.value })
                      }
                    />
                  </label>
                  <label className="dataset-check">
                    <input
                      type="checkbox"
                      aria-label="Mark image reviewed"
                      checked={selected.review_status === "reviewed"}
                      onChange={(e) =>
                        updateMember({
                          review_status: e.target.checked
                            ? "reviewed"
                            : "unreviewed",
                        })
                      }
                    />
                    I declare this image reviewed
                    {selected.annotations.length === 0
                      ? " with no target instances (explicit negative)"
                      : " with the authored labels shown above"}
                    .
                  </label>
                  <p>
                    Reviewer identity and annotation correctness are declared,
                    not authenticated. Editing a box returns the image to
                    unreviewed and clears this draft's review identity/note.
                    Changed reviewed content requires a new review note before
                    publication.
                  </p>
                </fieldset>
              </>
            ) : (
              <div className="dataset-list-empty">
                <Camera size={28} />
                <strong>Choose an image or capture a source message</strong>
                <p>Annotations can be authored before running any detector.</p>
                <button
                  type="button"
                  className="button primary"
                  onClick={() => setCapture(true)}
                >
                  Capture original image
                </button>
              </div>
            )}
          </div>
        </div>
        {error && (
          <p role="alert" className="inline-error">
            {error}
          </p>
        )}
        <footer className="dataset-editor-footer">
          <p>
            {dataset
              ? `Expected authoring head r${guard}. Earlier revisions retain their exact labels.`
              : "Drafts and reviewed images can coexist. Evaluation keeps every selected image in its denominator."}
          </p>
          <div className="dataset-actions">
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="button"
              className="button primary"
              disabled={busy || capture}
              onClick={publish}
            >
              {busy && <LoaderCircle size={14} className="spin" />}
              {busy
                ? "Publishing dataset…"
                : dataset
                  ? "Publish dataset revision"
                  : "Publish dataset"}
            </button>
          </div>
        </footer>
      </div>
    </Modal>
  );
}
