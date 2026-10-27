import { useEffect, useRef, useState, type PointerEvent } from "react";
import {
  Crosshair,
  MousePointer2,
  Plus,
  Scan,
  Trash2,
  ZoomIn,
} from "lucide-react";
import type { InferenceImage } from "../perception/PerceptionTypes";
import type { DatasetAnnotation } from "./DatasetTypes";
import { useDatasetImage } from "./useDatasetImage";

type Point = { x: number; y: number };
const palette = ["#8fe4c0", "#ffbe80", "#c6b8ff", "#91d5f3", "#f1d97f"];
const rounded = (n: number) => Number(n.toFixed(3));

export default function AnnotationEditor({
  image,
  labels,
  annotations,
  onChange,
  readOnly = false,
  reviewedNegative = false,
}: {
  image: InferenceImage;
  labels: string[];
  annotations: DatasetAnnotation[];
  onChange: (annotations: DatasetAnnotation[]) => void;
  readOnly?: boolean;
  reviewedNegative?: boolean;
}) {
  const source = useDatasetImage(image);
  const svg = useRef<SVGSVGElement | null>(null);
  const [mode, setMode] = useState<"select" | "draw">("select");
  const [zoom, setZoom] = useState<"fit" | "pixels">("fit");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draw, setDraw] = useState<{ start: Point; end: Point } | null>(null);
  const [coordinates, setCoordinates] = useState<string[]>(["", "", "", ""]);
  const [error, setError] = useState("");
  const selected =
    annotations.find((annotation) => annotation.id === selectedId) ?? null;
  const selectedCoordinates = selected?.bbox_xyxy.join("|") ?? "";
  useEffect(() => {
    setCoordinates(
      selectedCoordinates ? selectedCoordinates.split("|") : ["", "", "", ""],
    );
    setError("");
  }, [selectedId, selectedCoordinates]);
  function fromPointer(event: PointerEvent<SVGSVGElement>): Point | null {
    const matrix = svg.current?.getScreenCTM();
    if (!matrix) return null;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(
      matrix.inverse(),
    );
    return { x: point.x, y: point.y };
  }
  function bounded(point: Point): Point {
    return {
      x: Math.min(image.width, Math.max(0, point.x)),
      y: Math.min(image.height, Math.max(0, point.y)),
    };
  }
  function add(box: DatasetAnnotation["bbox_xyxy"]) {
    if (!labels.length || annotations.length >= 100 || readOnly) return;
    const annotation: DatasetAnnotation = {
      id: crypto.randomUUID(),
      label: labels[0],
      bbox_xyxy: box,
      iscrowd: false,
    };
    onChange([...annotations, annotation]);
    setSelectedId(annotation.id);
    setMode("select");
  }
  function down(event: PointerEvent<SVGSVGElement>) {
    if (
      mode !== "draw" ||
      readOnly ||
      !source.url ||
      annotations.length >= 100 ||
      event.button !== 0
    )
      return;
    const point = fromPointer(event);
    if (
      !point ||
      point.x < 0 ||
      point.y < 0 ||
      point.x > image.width ||
      point.y > image.height
    )
      return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDraw({ start: point, end: point });
  }
  function move(event: PointerEvent<SVGSVGElement>) {
    if (!draw) return;
    const point = fromPointer(event);
    if (point) setDraw({ ...draw, end: bounded(point) });
  }
  function up(event: PointerEvent<SVGSVGElement>) {
    if (!draw) return;
    const end = bounded(fromPointer(event) ?? draw.end);
    const box: DatasetAnnotation["bbox_xyxy"] = [
      Math.min(draw.start.x, end.x),
      Math.min(draw.start.y, end.y),
      Math.max(draw.start.x, end.x),
      Math.max(draw.start.y, end.y),
    ].map(rounded) as DatasetAnnotation["bbox_xyxy"];
    setDraw(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    if (box[2] - box[0] >= 1 && box[3] - box[1] >= 1) add(box);
    else
      setError(
        "Draw a box at least one original-image pixel wide and high, or use precise coordinate inputs.",
      );
  }
  function update(patch: Partial<DatasetAnnotation>) {
    if (selected && !readOnly)
      onChange(
        annotations.map((annotation) =>
          annotation.id === selected.id
            ? { ...annotation, ...patch }
            : annotation,
        ),
      );
  }
  function applyCoordinates() {
    const box = coordinates.map(Number) as DatasetAnnotation["bbox_xyxy"];
    if (
      coordinates.some((value) => !value.trim()) ||
      box.some((value) => !Number.isFinite(value)) ||
      box[0] < 0 ||
      box[1] < 0 ||
      box[2] > image.width ||
      box[3] > image.height ||
      box[2] <= box[0] ||
      box[3] <= box[1]
    ) {
      setError(
        `Use finite xyxy with positive area within 0…${image.width} × 0…${image.height} original pixels.`,
      );
      return;
    }
    update({ bbox_xyxy: box });
    setError("");
  }
  return (
    <div className="annotation-editor" data-testid="annotation-editor">
      <div className="annotation-toolbar">
        <span>
          {image.width} × {image.height} original pixels
        </span>
        <div>
          {!readOnly && (
            <>
              <button
                type="button"
                aria-label="Select annotation"
                aria-pressed={mode === "select"}
                onClick={() => {
                  setMode("select");
                  setDraw(null);
                }}
              >
                <MousePointer2 size={14} />
                Select
              </button>
              <button
                type="button"
                aria-label="Draw annotation box"
                aria-pressed={mode === "draw"}
                disabled={
                  !labels.length || annotations.length >= 100 || !source.url
                }
                onClick={() => setMode("draw")}
              >
                <Crosshair size={14} />
                Draw box
              </button>
            </>
          )}
          <button
            type="button"
            aria-label="Fit annotation image"
            aria-pressed={zoom === "fit"}
            onClick={() => setZoom("fit")}
          >
            <Scan size={14} />
            Fit
          </button>
          <button
            type="button"
            aria-label="Annotation image actual pixels"
            aria-pressed={zoom === "pixels"}
            onClick={() => setZoom("pixels")}
          >
            <ZoomIn size={14} />
            100%
          </button>
        </div>
      </div>
      <div className={`annotation-image-stage ${zoom} ${mode}`}>
        {source.error ? (
          <p role="alert">
            {source.error}
            <br />
            <button
              type="button"
              className="button secondary small"
              onClick={source.retry}
            >
              Retry original dataset image
            </button>
          </p>
        ) : !source.url ? (
          <p role="status">Verifying original dataset image…</p>
        ) : (
          <svg
            ref={svg}
            width={image.width}
            height={image.height}
            viewBox={`0 0 ${image.width} ${image.height}`}
            role="img"
            aria-label="Annotation canvas"
            data-testid="annotation-canvas"
            data-image-sha256={image.sha256}
            onPointerDown={down}
            onPointerMove={move}
            onPointerUp={up}
            onPointerCancel={() => setDraw(null)}
          >
            <image
              href={source.url}
              width={image.width}
              height={image.height}
            />
            {annotations.map((annotation, index) => {
              const [x1, y1, x2, y2] = annotation.bbox_xyxy;
              const color =
                palette[
                  Math.max(0, labels.indexOf(annotation.label)) % palette.length
                ];
              return (
                <g
                  key={annotation.id}
                  opacity={selectedId && selectedId !== annotation.id ? 0.6 : 1}
                  onClick={() => {
                    if (mode === "select") setSelectedId(annotation.id);
                  }}
                  style={{
                    cursor: mode === "select" ? "pointer" : undefined,
                    pointerEvents: mode === "draw" ? "none" : undefined,
                  }}
                >
                  <rect
                    data-testid="annotation-box"
                    data-annotation-id={annotation.id}
                    x={x1}
                    y={y1}
                    width={x2 - x1}
                    height={y2 - y1}
                    fill={
                      selectedId === annotation.id
                        ? `${color}20`
                        : "transparent"
                    }
                    stroke={color}
                    strokeWidth={selectedId === annotation.id ? 3 : 1.5}
                    strokeDasharray={annotation.iscrowd ? "5 3" : undefined}
                    vectorEffect="non-scaling-stroke"
                  />
                  <text
                    x={x1 + 2}
                    y={y1 > 16 ? y1 - 4 : y1 + 15}
                    fill={color}
                    fontSize={Math.max(12, image.width / 60)}
                    stroke="#172b30"
                    strokeWidth={3}
                    paintOrder="stroke"
                  >
                    {index + 1} · {annotation.label}
                    {annotation.iscrowd ? " · crowd" : ""}
                  </text>
                </g>
              );
            })}
            {draw && (
              <rect
                x={Math.min(draw.start.x, draw.end.x)}
                y={Math.min(draw.start.y, draw.end.y)}
                width={Math.abs(draw.end.x - draw.start.x)}
                height={Math.abs(draw.end.y - draw.start.y)}
                fill="#c2f4d21a"
                stroke="#c2f4d2"
                strokeWidth={1.5}
                strokeDasharray="5 3"
                vectorEffect="non-scaling-stroke"
              />
            )}
          </svg>
        )}
      </div>
      <div className="annotation-edit-panel">
        <div className="dataset-section-toolbar">
          <div>
            <strong>
              {annotations.length} annotation
              {annotations.length === 1 ? "" : "s"}
            </strong>
            <p>
              {readOnly
                ? "Published coordinates from this immutable dataset revision."
                : "These boxes are authored labels. Prediction outputs are never copied automatically."}
            </p>
          </div>
          {!readOnly && (
            <button
              className="button secondary small"
              type="button"
              disabled={
                !labels.length || annotations.length >= 100 || !source.url
              }
              onClick={() =>
                add([
                  rounded(image.width * 0.25),
                  rounded(image.height * 0.25),
                  rounded(image.width * 0.75),
                  rounded(image.height * 0.75),
                ])
              }
            >
              <Plus size={14} />
              Add box by coordinates
            </button>
          )}
        </div>
        {annotations.length > 0 && (
          <label>
            Annotation
            <select
              aria-label="Select dataset annotation"
              value={selectedId ?? ""}
              onChange={(event) => setSelectedId(event.target.value || null)}
            >
              <option value="">Select an annotation to inspect</option>
              {annotations.map((annotation, index) => (
                <option key={annotation.id} value={annotation.id}>
                  {index + 1} · {annotation.label}
                  {annotation.iscrowd ? " · crowd" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        {selected && (
          <div
            className="annotation-selected"
            data-testid="selected-annotation"
          >
            <div className="dataset-form-grid two">
              <label>
                Class
                <select
                  aria-label="Annotation class"
                  disabled={readOnly}
                  value={selected.label}
                  onChange={(e) => update({ label: e.target.value })}
                >
                  {!labels.includes(selected.label) && (
                    <option value={selected.label}>
                      Unmapped · {selected.label}
                    </option>
                  )}
                  {labels.map((label) => (
                    <option key={label} value={label}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="dataset-check">
                <input
                  aria-label="Crowd annotation"
                  type="checkbox"
                  checked={selected.iscrowd}
                  disabled={readOnly}
                  onChange={(e) => update({ iscrowd: e.target.checked })}
                />
                Crowd region (iscrowd)
              </label>
            </div>
            <div className="dataset-form-grid four">
              {["X minimum", "Y minimum", "X maximum", "Y maximum"].map(
                (label, index) => (
                  <label key={label}>
                    {label}
                    <input
                      aria-label={label}
                      inputMode="decimal"
                      value={coordinates[index]}
                      readOnly={readOnly}
                      onChange={(e) =>
                        setCoordinates((values) =>
                          values.map((value, position) =>
                            position === index ? e.target.value : value,
                          ),
                        )
                      }
                    />
                  </label>
                ),
              )}
            </div>
            <p
              className="dataset-coordinate-readout"
              data-testid="annotation-coordinates"
            >
              Saved xyxy: {selected.bbox_xyxy.join(", ")} px
            </p>
            {!readOnly && (
              <div className="dataset-actions">
                <button
                  type="button"
                  className="button secondary small"
                  onClick={applyCoordinates}
                >
                  Apply box coordinates
                </button>
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => {
                    onChange(
                      annotations.filter(
                        (annotation) => annotation.id !== selected.id,
                      ),
                    );
                    setSelectedId(null);
                  }}
                >
                  <Trash2 size={14} />
                  Delete selected annotation
                </button>
              </div>
            )}
          </div>
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        {!annotations.length && (
          <p className="dataset-muted">
            {reviewedNegative
              ? "No target instances are declared in this reviewed negative image."
              : "No annotations. A blank draft is not a reviewed negative image."}
          </p>
        )}
      </div>
    </div>
  );
}
