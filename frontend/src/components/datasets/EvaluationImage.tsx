import { useState } from "react";
import { Eye, Scan, ZoomIn } from "lucide-react";
import type { DatasetImage } from "./DatasetTypes";
import type {
  EvaluationInputRow,
  EvaluationReportRow,
} from "./EvaluationTypes";
import { useDatasetImage } from "./useDatasetImage";

const outcomeLabels = {
  true_positive: "True positive",
  false_positive: "False positive",
  false_negative: "False negative",
  crowd_ignored: "Crowd ignored",
};

export default function EvaluationImage({
  asset,
  input,
  row,
}: {
  asset: DatasetImage;
  input: EvaluationInputRow;
  row: EvaluationReportRow;
}) {
  const source = useDatasetImage(asset.image);
  const [showLabels, setShowLabels] = useState(true),
    [showPredictions, setShowPredictions] = useState(true);
  const [zoom, setZoom] = useState<"fit" | "pixels">("fit");
  const [matchIndex, setMatchIndex] = useState<number | null>(null);
  const selected =
    matchIndex !== null ? row.diagnostics?.matches[matchIndex] : null;
  const consistent =
    input.image_id === asset.id &&
    row.image_id === asset.id &&
    input.width === asset.image.width &&
    input.height === asset.image.height;
  return (
    <div className="evaluation-image" data-testid="evaluation-image">
      <div className="annotation-toolbar">
        <span>
          {asset.image.width} × {asset.image.height} original pixels
        </span>
        <div>
          <button
            type="button"
            aria-label="Show dataset labels"
            aria-pressed={showLabels}
            onClick={() => setShowLabels((value) => !value)}
          >
            <Eye size={13} />
            Labels
          </button>
          <button
            type="button"
            aria-label="Show mapped predictions"
            aria-pressed={showPredictions}
            onClick={() => setShowPredictions((value) => !value)}
          >
            <Eye size={13} />
            Predictions
          </button>
          <button
            type="button"
            aria-label="Fit evaluation image"
            aria-pressed={zoom === "fit"}
            onClick={() => setZoom("fit")}
          >
            <Scan size={13} />
            Fit
          </button>
          <button
            type="button"
            aria-label="Evaluation image actual pixels"
            aria-pressed={zoom === "pixels"}
            onClick={() => setZoom("pixels")}
          >
            <ZoomIn size={13} />
            100%
          </button>
        </div>
      </div>
      <div className={`annotation-image-stage ${zoom}`}>
        {!consistent ? (
          <p role="alert">
            Image identity or pixel dimensions differ from the captured
            evaluation. Overlay withheld.
          </p>
        ) : source.error ? (
          <p role="alert">
            {source.error}
            <br />
            <button
              type="button"
              className="button secondary small"
              onClick={source.retry}
            >
              Retry evaluation source image
            </button>
          </p>
        ) : !source.url ? (
          <p role="status">Verifying the labeled source image…</p>
        ) : (
          <svg
            role="img"
            aria-label="Dataset labels and recorded predictions"
            data-testid="evaluation-overlay"
            data-image-sha256={asset.image.sha256}
            width={asset.image.width}
            height={asset.image.height}
            viewBox={`0 0 ${asset.image.width} ${asset.image.height}`}
          >
            <image
              href={source.url}
              width={asset.image.width}
              height={asset.image.height}
            />
            {showLabels &&
              input.annotations.map((annotation) => {
                const [x1, y1, x2, y2] = annotation.bbox_xyxy;
                const active = selected?.annotation_id === annotation.id;
                return (
                  <g
                    key={annotation.id}
                    opacity={selected && !active ? 0.45 : 1}
                  >
                    <rect
                      data-testid="evaluation-label-box"
                      data-annotation-id={annotation.id}
                      x={x1}
                      y={y1}
                      width={x2 - x1}
                      height={y2 - y1}
                      fill="none"
                      stroke="#8de3c3"
                      strokeWidth={active ? 3 : 1.5}
                      strokeDasharray={annotation.iscrowd ? "2 2" : undefined}
                      vectorEffect="non-scaling-stroke"
                    />
                    <text
                      x={x1 + 2}
                      y={y1 > 14 ? y1 - 4 : y1 + 14}
                      fontSize={Math.max(11, asset.image.width / 65)}
                      fill="#8de3c3"
                      stroke="#132a2b"
                      strokeWidth={3}
                      paintOrder="stroke"
                    >
                      Label · {annotation.label}
                      {annotation.iscrowd ? " · crowd" : ""}
                    </text>
                  </g>
                );
              })}
            {showPredictions &&
              input.predictions.map((prediction) => {
                const [x1, y1, x2, y2] = prediction.bbox_xyxy;
                const active = selected?.prediction_id === prediction.id;
                return (
                  <g
                    key={prediction.id}
                    opacity={selected && !active ? 0.45 : 1}
                  >
                    <rect
                      data-testid="evaluation-prediction-box"
                      data-prediction-id={prediction.id}
                      x={x1}
                      y={y1}
                      width={x2 - x1}
                      height={y2 - y1}
                      fill="none"
                      stroke="#ffc084"
                      strokeWidth={active ? 3 : 1.5}
                      strokeDasharray="7 4"
                      vectorEffect="non-scaling-stroke"
                    />
                    <text
                      x={x1 + 2}
                      y={Math.min(asset.image.height - 3, y2 + 14)}
                      fontSize={Math.max(11, asset.image.width / 65)}
                      fill="#ffc084"
                      stroke="#132a2b"
                      strokeWidth={3}
                      paintOrder="stroke"
                    >
                      Prediction · {prediction.label} ·{" "}
                      {prediction.score.toFixed(3)}
                    </text>
                  </g>
                );
              })}
          </svg>
        )}
      </div>
      <div className="evaluation-overlay-legend">
        <span>
          <i />
          Declared dataset labels{row.status === "unreviewed" ? " · draft" : ""}
        </span>
        <span>
          <i className="prediction" />
          Mapped stored predictions
        </span>
        <small>Scores are not calibrated probabilities.</small>
      </div>
      {row.diagnostics ? (
        <div className="evaluation-matches">
          <div className="dataset-section-toolbar">
            <strong>Evaluator diagnostic matches</strong>
            <span>
              {row.diagnostics.score_filtered} predictions removed by evaluation
              score filtering
            </span>
          </div>
          {row.diagnostics.matches.length ? (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Outcome</th>
                    <th>Prediction</th>
                    <th>Annotation</th>
                    <th>IoU</th>
                  </tr>
                </thead>
                <tbody>
                  {row.diagnostics.matches.map((match, index) => (
                    <tr key={index}>
                      <td>
                        <button
                          type="button"
                          aria-pressed={matchIndex === index}
                          className={`evaluation-match ${match.outcome}`}
                          onClick={() =>
                            setMatchIndex(matchIndex === index ? null : index)
                          }
                        >
                          {outcomeLabels[match.outcome]}
                        </button>
                      </td>
                      <td>
                        <code>{match.prediction_id ?? "—"}</code>
                      </td>
                      <td>
                        <code>{match.annotation_id ?? "—"}</code>
                      </td>
                      <td>{match.iou === null ? "—" : match.iou.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p>
              {input.annotations.length === 0 && input.predictions.length === 0
                ? "No mapped predictions or declared target instances on this evaluated negative image."
                : "No diagnostic matches were reported at the captured thresholds. Crowd annotations are not counted as false negatives."}
            </p>
          )}
        </div>
      ) : (
        <p className="dataset-scope">
          This image has no evaluation diagnostics:{" "}
          {row.status.replaceAll("_", " ")}. Its absence from the diagnostic
          subset does not remove it from the full denominator.
        </p>
      )}
    </div>
  );
}
