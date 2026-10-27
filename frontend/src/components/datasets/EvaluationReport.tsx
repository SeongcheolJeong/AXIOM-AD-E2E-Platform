import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { BarChart3, Camera, ShieldQuestion } from "lucide-react";
import EvaluationImage from "./EvaluationImage";
import CaseCapture from "../cases/CaseCapture";
import type { EvaluationDetail, EvaluationMetrics } from "./EvaluationTypes";

const metric = (value: number | null | undefined) =>
  value === null || value === undefined
    ? "Unavailable"
    : `${(value * 100).toFixed(2)}%`;

export default function EvaluationReportView({
  job,
}: {
  job: EvaluationDetail;
}) {
  const report = job.report!;
  const targetImage = new URLSearchParams(location.hash.split("?")[1] ?? "").get("image");
  const [imageId, setImageId] = useState(targetImage && report.rows.some((r) => r.image_id === targetImage) ? targetImage : report.rows[0]?.image_id ?? "");
  const [className, setClassName] = useState(report.classes[0]?.label ?? "");
  const row =
    report.rows.find((item) => item.image_id === imageId) ?? report.rows[0];
  const input = job.evaluation_input.rows.find(
    (item) => item.image_id === row?.image_id,
  );
  const asset = job.dataset_snapshot.image_assets.find(
    (item) => item.id === row?.image_id,
  );
  const declaration = job.dataset_snapshot.images.find(
    (item) => item.image_id === row?.image_id,
  );
  const lineage = job.source_context.rows.find(
    (item) => item.image_id === row?.image_id,
  );
  const selectedClass =
    report.classes.find((item) => item.label === className) ??
    report.classes[0];
  const curve = selectedClass?.pr_curve;
  const chart = curve
    ? curve.recall.map((recall, index) => ({
        recall,
        precision: curve.precision[index],
      }))
    : [];
  const highlight: { key: keyof EvaluationMetrics; label: string }[] = [
    { key: "ap", label: "COCO AP · stored predictions" },
    { key: "ap50", label: "AP at IoU 0.50" },
    { key: "ap75", label: "AP at IoU 0.75" },
    { key: "precision", label: "Diagnostic precision" },
    { key: "recall", label: "Diagnostic recall" },
    { key: "f1", label: "Diagnostic F1" },
  ];
  return (
    <div
      className="evaluation-report"
      data-testid="detection-evaluation-report"
    >
      {row && <CaseCapture key={`${job.id}/${row.image_id}`} source={{ kind: "evaluation", evaluation_id: job.id, image_id: row.image_id }} />}
      <section className="panel evaluation-coverage">
        <div className="dataset-section-toolbar">
          <h3>
            <ShieldQuestion size={17} />
            Full selected-image denominator
          </h3>
          <span
            className={`dataset-review-badge ${report.coverage.complete ? "reviewed" : ""}`}
          >
            {report.coverage.evaluated} / {report.coverage.total} images
            eligible
          </span>
        </div>
        <div className="evaluation-coverage-grid">
          {Object.entries(report.coverage)
            .filter(([key]) => key !== "complete")
            .map(([key, value]) => (
              <div key={key}>
                <strong>{String(value)}</strong>
                <span>{key.replaceAll("_", " ")}</span>
              </div>
            ))}
        </div>
        {report.metrics === null ? (
          <p
            className="dataset-scope"
            data-testid="evaluation-metrics-withheld"
          >
            Aggregate metrics withheld. Every image must be reviewed and have a
            matched, valid prediction before aggregate AP or precision/recall
            can be reported. Diagnostics below describe the eligible subset
            only.
          </p>
        ) : (
          <>
            <div className="evaluation-metric-grid">
              {highlight.map((item) => (
                <div key={item.key}>
                  <span>{item.label}</span>
                  <strong data-testid={`evaluation-metric-${item.key}`}>
                    {metric(report.metrics![item.key])}
                  </strong>
                </div>
              ))}
            </div>
            <details className="dataset-details">
              <summary>All aggregate metrics & definitions</summary>
              <div className="table-scroll">
                <table className="data-table">
                  <tbody>
                    {Object.entries(report.metrics).map(([key, value]) => (
                      <tr key={key}>
                        <th>{key}</th>
                        <td>{metric(value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>
                Unavailable means the evaluator did not define that metric. COCO
                AP caps predictions at 100 per image/category; diagnostics use
                retained predictions at IoU{" "}
                {job.request.diagnostic_iou.toFixed(2)} and score threshold{" "}
                {job.request.score_threshold}.
              </p>
            </details>
          </>
        )}
        <p className="dataset-muted">
          These are metrics over declared labels and stored, already
          filtered/NMS-limited predictions. Authored fixtures test arithmetic,
          not road perception performance.
        </p>
      </section>
      <section className="panel evaluation-classes">
        <div className="dataset-section-toolbar">
          <h3>
            <BarChart3 size={17} />
            Per-class diagnostics
          </h3>
          <span>Scope: evaluated image rows only</span>
        </div>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Dataset class</th>
                <th>GT</th>
                <th>Crowd GT</th>
                <th>Pred.</th>
                <th>TP</th>
                <th>FP</th>
                <th>FN</th>
                <th>Crowd ignored</th>
                <th>AP</th>
              </tr>
            </thead>
            <tbody>
              {report.classes.map((item) => (
                <tr key={item.label}>
                  <td>{item.label}</td>
                  <td>{item.gt}</td>
                  <td>{item.crowd_gt}</td>
                  <td>{item.predictions}</td>
                  <td>{item.tp}</td>
                  <td>{item.fp}</td>
                  <td>{item.fn}</td>
                  <td>{item.crowd_ignored}</td>
                  <td>{metric(item.ap)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="evaluation-pr-heading">
          <label>
            Precision–recall class
            <select
              aria-label="Precision recall class"
              value={selectedClass?.label ?? ""}
              onChange={(event) => setClassName(event.target.value)}
            >
              {report.classes.map((item) => (
                <option key={item.label} value={item.label}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <p>
            pycocotools' 101 recall samples. AP/PR remain unavailable while full
            image coverage is incomplete.
          </p>
        </div>
        {curve && chart.length ? (
          <div
            className="evaluation-pr-chart"
            data-testid="evaluation-pr-chart"
          >
            <ResponsiveContainer width="100%" height={240}>
              <LineChart
                data={chart}
                margin={{ top: 10, right: 20, left: 0, bottom: 18 }}
              >
                <CartesianGrid stroke="#dce6d7" strokeDasharray="3 3" />
                <XAxis
                  dataKey="recall"
                  type="number"
                  domain={[0, 1]}
                  tick={{ fontSize: 11, fill: "#6d8064" }}
                  label={{
                    value: "Recall",
                    position: "insideBottom",
                    offset: -10,
                    fontSize: 11,
                  }}
                />
                <YAxis
                  domain={[0, 1]}
                  tick={{ fontSize: 11, fill: "#6d8064" }}
                  width={42}
                />
                <Tooltip
                  formatter={(value) => Number(value).toFixed(4)}
                  labelFormatter={(value) =>
                    `Recall ${Number(value).toFixed(2)}`
                  }
                />
                <Line
                  type="stepAfter"
                  dataKey="precision"
                  name="Interpolated precision"
                  stroke="#417d66"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="dataset-scope">
            Precision–recall curve unavailable for this class and evaluation
            coverage.
          </p>
        )}
      </section>
      <section className="panel evaluation-image-review">
        <div className="dataset-section-toolbar">
          <h3>
            <Camera size={17} />
            Image evidence & matches
          </h3>
          <label>
            Selected image
            <select
              aria-label="Evaluation image row"
              value={row?.image_id ?? ""}
              onChange={(e) => setImageId(e.target.value)}
            >
              {report.rows.map((item, index) => {
                const image = job.dataset_snapshot.image_assets.find(
                  (asset) => asset.id === item.image_id,
                );
                return (
                  <option key={item.image_id} value={item.image_id}>
                    {index + 1} · ordinal #{image?.input.ordinal ?? "unknown"} ·{" "}
                    {item.status.replaceAll("_", " ")}
                  </option>
                );
              })}
            </select>
          </label>
        </div>
        {row && (
          <div className="dataset-review-strip">
            <span
              className={`dataset-review-badge ${row.status === "evaluated" ? "reviewed" : ""}`}
            >
              {row.status.replaceAll("_", " ")}
            </span>
            {row.diagnostics && (
              <span>
                TP {row.diagnostics.tp} · FP {row.diagnostics.fp} · FN{" "}
                {row.diagnostics.fn} · Crowd ignored{" "}
                {row.diagnostics.crowd_ignored}
              </span>
            )}
          </div>
        )}
        {lineage?.reason && (
          <p className="dataset-scope" data-testid="evaluation-image-reason">
            {lineage.reason}
          </p>
        )}
        {asset && declaration && (
          <div className="evaluation-image-provenance">
            <span>
              Channel #{asset.input.channel_id} · exact file ordinal #
              {asset.input.ordinal} · log {asset.input.log_time_ns} ns
            </span>
            <span>
              Annotations: {declaration.annotation_source} ·{" "}
              {declaration.review_status} · reviewer{" "}
              {declaration.reviewer || "not declared"}
            </span>
            <span>
              Source predictions:{" "}
              {lineage?.inference_job_id ?? "no matching input"}
              {lineage?.selection_index != null
                ? ` · selection ${lineage.selection_index}`
                : ""}{" "}
              · {lineage?.excluded_class_predictions ?? 0} explicitly excluded
              by class mapping
            </span>
            <details className="dataset-details">
              <summary>Exact image and review declaration</summary>
              <div className="dataset-hash">
                <span>Saved PNG SHA-256</span>
                <code>{asset.image.sha256}</code>
              </div>
              <p>
                {asset.input.decoder_id}/{asset.input.decoder_version} · native
                frame {asset.image.frame_id ?? "unknown"} · capture{" "}
                {asset.image.capture_time_ns ?? "unknown"} ns
              </p>
              <p>{declaration.review_note || "No review note declared."}</p>
              <p>
                Source and reviewer are declarations; they do not establish
                independent review or reviewer identity.
              </p>
            </details>
          </div>
        )}
        {row && input && asset ? (
          <EvaluationImage
            key={row.image_id}
            asset={asset}
            input={input}
            row={row}
          />
        ) : (
          <p className="inline-error" role="alert">
            The report row could not be joined to its exact captured image and
            prepared labels.
          </p>
        )}
      </section>
      {report.warnings.length > 0 && (
        <section className="panel evaluation-warnings">
          <h3>Evaluator notices</h3>
          {report.warnings.map((warning, index) => (
            <p key={index}>{warning}</p>
          ))}
        </section>
      )}
    </div>
  );
}
