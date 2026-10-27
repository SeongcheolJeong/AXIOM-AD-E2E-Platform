import { useEffect, useState } from "react";
import { Eye, EyeOff, Maximize2, ZoomIn } from "lucide-react";
import { authHeaders } from "../../api";
import { verifyBuffer } from "../sensors/SensorData";
import type { Detection, InferenceImage } from "./PerceptionTypes";

const colors = ["#84ebc9", "#ffa975", "#c5b8ff", "#9dd1ff", "#f3dc87"];

/** The source image and every box share decoded-image pixel coordinates. */
export default function DetectionImage({
  image,
  detections,
  label,
}: {
  image: InferenceImage;
  detections: Detection[];
  label: string;
}) {
  const [mode, setMode] = useState<"fit" | "pixels">("fit");
  const [overlay, setOverlay] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [state, setState] = useState({ key: "", url: "", error: "" });
  const key = `${image.download_url}:${image.sha256}:${image.width}:${image.height}`;
  const invalid = detections.some(
    (d) =>
      !Number.isFinite(d.score) ||
      d.score < 0 ||
      d.score > 1 ||
      !Number.isSafeInteger(d.class_id) ||
      d.class_id < 0 ||
      d.bbox_xyxy.some((value) => !Number.isFinite(value)) ||
      d.bbox_xyxy[0] < 0 ||
      d.bbox_xyxy[1] < 0 ||
      d.bbox_xyxy[2] > image.width ||
      d.bbox_xyxy[3] > image.height ||
      d.bbox_xyxy[2] <= d.bbox_xyxy[0] ||
      d.bbox_xyxy[3] <= d.bbox_xyxy[1],
  );
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setState({ key, url: "", error: "" });
    setSelected(null);
    async function load() {
      if (
        !Number.isSafeInteger(image.size_bytes) ||
        image.size_bytes > 64 * 1024 * 1024
      )
        throw new Error("Input image exceeds the browser preview bound.");
      const response = await fetch(image.download_url, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!response.ok)
        throw new Error(`Saved input image unavailable (${response.status}).`);
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.length !== image.size_bytes)
        throw new Error(
          "Saved image size differs from its evidence descriptor.",
        );
      await verifyBuffer(bytes, image.sha256);
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
      const decoded = new window.Image();
      await new Promise<void>((resolve, reject) => {
        decoded.onload = () =>
          decoded.naturalWidth === image.width &&
          decoded.naturalHeight === image.height
            ? resolve()
            : reject(
                new Error(
                  "Saved image dimensions differ from the evidence descriptor.",
                ),
              );
        decoded.onerror = () =>
          reject(new Error("The saved input PNG could not be decoded."));
        decoded.src = objectUrl;
      });
      if (!controller.signal.aborted)
        setState({ key, url: objectUrl, error: "" });
    }
    void load().catch((reason: unknown) => {
      if (!controller.signal.aborted)
        setState({
          key,
          url: "",
          error:
            reason instanceof Error ? reason.message : "Image unavailable.",
        });
    });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [
    key,
    image.download_url,
    image.sha256,
    image.size_bytes,
    image.width,
    image.height,
  ]);
  const current = state.key === key;
  return (
    <div className="prediction-image" data-testid="prediction-image">
      <div className="prediction-image-toolbar">
        <span>
          {image.width} × {image.height} px
        </span>
        <div>
          <button
            type="button"
            aria-label="Toggle detection overlay"
            aria-pressed={overlay}
            onClick={() => setOverlay((v) => !v)}
          >
            {overlay ? <Eye size={14} /> : <EyeOff size={14} />} Boxes
          </button>
          <button
            type="button"
            aria-label="Fit prediction image"
            aria-pressed={mode === "fit"}
            onClick={() => setMode("fit")}
          >
            <Maximize2 size={14} /> Fit
          </button>
          <button
            type="button"
            aria-label="Prediction image actual pixels"
            aria-pressed={mode === "pixels"}
            onClick={() => setMode("pixels")}
          >
            <ZoomIn size={14} /> 100%
          </button>
        </div>
      </div>
      <div className={`prediction-image-stage ${mode}`}>
        {current && state.error ? (
          <p role="alert">{state.error}</p>
        ) : invalid ? (
          <p role="alert">Prediction geometry is invalid. Overlay withheld.</p>
        ) : current && state.url ? (
          <svg
            viewBox={`0 0 ${image.width} ${image.height}`}
            width={image.width}
            height={image.height}
            role="img"
            aria-label={label}
            data-testid="prediction-overlay"
            data-image-sha256={image.sha256}
          >
            <image href={state.url} width={image.width} height={image.height} />
            {overlay &&
              detections.map((d) => {
                const [x1, y1, x2, y2] = d.bbox_xyxy;
                const color = colors[d.class_id % colors.length];
                const size = Math.max(12, image.width / 55);
                return (
                  <g
                    key={d.id}
                    opacity={selected && selected !== d.id ? 0.38 : 1}
                  >
                    <rect
                      data-testid="detection-box"
                      data-detection-id={d.id}
                      x={x1}
                      y={y1}
                      width={x2 - x1}
                      height={y2 - y1}
                      fill="none"
                      stroke={color}
                      strokeWidth={selected === d.id ? 3 : 1.5}
                      vectorEffect="non-scaling-stroke"
                    />
                    <text
                      x={Math.min(x1 + 3, Math.max(0, image.width - 150))}
                      y={y1 >= size + 5 ? y1 - 5 : y1 + size + 3}
                      fontSize={size}
                      fontFamily="DM Sans, sans-serif"
                      fontWeight="600"
                      fill={color}
                      stroke="#10242b"
                      strokeWidth={3}
                      paintOrder="stroke"
                    >
                      {d.label} · {d.score.toFixed(3)}
                    </text>
                  </g>
                );
              })}
          </svg>
        ) : (
          <p role="status">Verifying the saved input image…</p>
        )}
      </div>
      <div className="prediction-detections">
        <div className="perception-small-heading">
          <strong>
            {detections.length} model prediction
            {detections.length === 1 ? "" : "s"}
          </strong>
          <span>Score is not calibrated probability</span>
        </div>
        {!detections.length ? (
          <p className="perception-muted">
            Inference completed with zero detections at the captured threshold.
          </p>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Class</th>
                  <th>Model score</th>
                  <th>Pixel xyxy</th>
                </tr>
              </thead>
              <tbody>
                {detections.map((d) => (
                  <tr
                    key={d.id}
                    className={selected === d.id ? "selected" : ""}
                  >
                    <td>
                      <button
                        className="perception-detection-button"
                        type="button"
                        aria-pressed={selected === d.id}
                        onClick={() =>
                          setSelected(selected === d.id ? null : d.id)
                        }
                      >
                        <i
                          style={{
                            background: colors[d.class_id % colors.length],
                          }}
                        />
                        {d.label}
                        <small>#{d.class_id}</small>
                      </button>
                    </td>
                    <td className="mono">{d.score.toFixed(6)}</td>
                    <td className="mono" data-testid="detection-coordinates">
                      {d.bbox_xyxy.map((n) => Number(n.toFixed(3))).join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
