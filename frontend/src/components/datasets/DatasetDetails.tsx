import { useState } from "react";
import { ArrowDownToLine, Camera, GitBranch, ListChecks } from "lucide-react";
import { formatDate } from "../../api";
import { DownloadLink } from "../ArtifactAccess";
import AnnotationEditor from "./AnnotationEditor";
import CollectionLineage from "./CollectionLineage";
import AnnotationAssist from "./AnnotationAssist";
import type { DatasetDetail } from "./DatasetTypes";

export default function DatasetDetails({
  dataset,
  onEdit,
  onEvaluate,
}: {
  dataset: DatasetDetail;
  onEdit: () => void;
  onEvaluate: () => void;
}) {
  const [selectedId, setSelectedId] = useState(
    dataset.images[0]?.image_id ?? "",
  );
  const member =
    dataset.images.find((item) => item.image_id === selectedId) ??
    dataset.images[0];
  const asset = dataset.image_assets.find(
    (item) => item.id === member?.image_id,
  );
  return (
    <div className="dataset-detail" data-testid="dataset-detail">
      <section className="panel dataset-summary">
        <div className="dataset-section-toolbar">
          <div>
            <span className="dataset-eyebrow">
              IMMUTABLE DATASET · R{dataset.revision}
            </span>
            <h2>{dataset.name}</h2>
            <p>{dataset.description || dataset.note}</p>
          </div>
          <div className="dataset-actions">
            <a className="button secondary" href={`#collections?${new URLSearchParams({source_dataset:dataset.id,source_revision:String(dataset.revision)})}`}>Start collection from dataset</a>
            <button type="button" className="button secondary" onClick={onEdit}>
              <GitBranch size={15} />
              Create dataset revision
            </button>
            <button
              type="button"
              className="button primary"
              onClick={onEvaluate}
            >
              <ListChecks size={15} />
              Evaluate detections
            </button>
          </div>
        </div>
        <div className="dataset-stat-grid">
          <div>
            <span>Reviewed / all images</span>
            <strong>
              {dataset.stats.reviewed}
              <small> / {dataset.stats.images}</small>
            </strong>
          </div>
          <div>
            <span>Unreviewed</span>
            <strong>{dataset.stats.unreviewed}</strong>
          </div>
          <div>
            <span>Annotations</span>
            <strong>{dataset.stats.annotations}</strong>
          </div>
          <div>
            <span>Reviewed negatives</span>
            <strong>{dataset.stats.negative_images}</strong>
          </div>
        </div>
        <div className="dataset-splits">
          {Object.entries(dataset.stats.splits).map(([split, count]) => (
            <span key={split}>
              {split}
              <b>{count}</b>
            </span>
          ))}
        </div>
        <details className="dataset-details">
          <summary>Taxonomy, source declarations & immutable hashes</summary>
          <div className="dataset-class-labels">
            {dataset.labels.map((label, index) => (
              <span key={label}>
                <b>{index}</b>
                {label}
              </span>
            ))}
          </div>
          <div className="dataset-hash">
            <span>Dataset SHA-256</span>
            <code>{dataset.manifest_hash}</code>
          </div>
          <div className="dataset-hash">
            <span>Label map SHA-256</span>
            <code>{dataset.label_map_hash}</code>
          </div>
          <p>
            Source {dataset.source} · source revision {dataset.source_revision}{" "}
            · license {dataset.license}
          </p>
          <p>
            Published {formatDate(dataset.created_at)}.{" "}
            {dataset.stats.duplicate_image_groups} exact duplicate image
            group(s). Exact duplicates in assigned cross-split partitions are
            rejected; this does not detect near-duplicates or verify
            training-data independence.
          </p>
          <DownloadLink
            className="button secondary small"
            href={dataset.download_url}
            download
          >
            <ArrowDownToLine size={14} />
            Download dataset manifest
          </DownloadLink>
        </details>
      </section>
      <CollectionLineage datasetId={dataset.id} revision={dataset.revision} />
      <AnnotationAssist key={`${dataset.id}:${dataset.revision}`} dataset={dataset} />
      {member && asset && (
        <section className="panel dataset-published-image">
          <div className="dataset-section-toolbar">
            <h3>
              <Camera size={17} />
              Published labels
            </h3>
            <label>
              Image
              <select
                aria-label="Published dataset image"
                value={member.image_id}
                onChange={(e) => setSelectedId(e.target.value)}
              >
                {dataset.images.map((image, index) => {
                  const source = dataset.image_assets.find(
                    (asset) => asset.id === image.image_id,
                  );
                  return (
                    <option key={image.image_id} value={image.image_id}>
                      {index + 1} · ordinal #
                      {source?.input.ordinal ?? "unknown"} · {image.split} ·{" "}
                      {image.review_status}
                    </option>
                  );
                })}
              </select>
            </label>
          </div>
          <div className="dataset-review-strip">
            <span
              className={`dataset-review-badge ${member.review_status === "reviewed" ? "reviewed" : ""}`}
            >
              {member.review_status === "reviewed"
                ? member.annotations.length
                  ? "Reviewed labels"
                  : "Reviewed negative image"
                : "Unreviewed draft · not a negative image"}
            </span>
            <span>Source: {member.annotation_source}</span>
            <span>Reviewer: {member.reviewer || "not declared"}</span>
          </div>
          {member.review_note && (
            <p className="dataset-review-note">{member.review_note}</p>
          )}
          <AnnotationEditor
            key={`${dataset.id}/${dataset.revision}/${member.image_id}`}
            image={asset.image}
            labels={dataset.labels}
            annotations={member.annotations}
            reviewedNegative={
              member.review_status === "reviewed" &&
              member.annotations.length === 0
            }
            onChange={() => undefined}
            readOnly
          />
          <div className="dataset-image-source">
            <span>
              Channel #{asset.input.channel_id} · original ordinal #
              {asset.input.ordinal}
            </span>
            <code>Log {asset.input.log_time_ns} ns</code>
            <span>
              {asset.input.decoder_id}/{asset.input.decoder_version} · capture{" "}
              {asset.image.capture_time_ns ?? "unknown"} ns
            </span>
            <div className="dataset-hash">
              <span>Saved PNG SHA-256</span>
              <code>{asset.image.sha256}</code>
            </div>
            <DownloadLink
              href={asset.image.download_url}
              download
              className="button secondary small"
            >
              Download dataset image
            </DownloadLink>
            <details className="dataset-details">
              <summary>Original source evidence</summary>
              <pre>
                {JSON.stringify(
                  { input: asset.input, recording: asset.recording_snapshot },
                  null,
                  2,
                )}
              </pre>
            </details>
          </div>
        </section>
      )}
    </div>
  );
}
