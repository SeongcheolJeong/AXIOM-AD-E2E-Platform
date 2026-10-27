import { useEffect, useState } from "react";
import { Image, Maximize2, ZoomIn } from "lucide-react";
import { base64Bytes, verifyBuffer } from "./SensorData";

export interface CameraPreviewData {
  base64: string;
  sha256: string;
  size: number;
  width: number;
  height: number;
}

export default function SensorCameraView({
  image,
  label,
}: {
  image: CameraPreviewData;
  label: string;
}) {
  const [mode, setMode] = useState<"fit" | "pixels">("fit");
  const [state, setState] = useState({ key: "", url: "", error: "" });
  const key = `${image.sha256}:${image.size}:${image.width}:${image.height}`;
  useEffect(() => {
    let active = true;
    let url = "";
    setState({ key, url: "", error: "" });
    async function load() {
      const bytes = base64Bytes(image.base64, image.size);
      await verifyBuffer(bytes, image.sha256);
      if (!active) return;
      url = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
      setState({ key, url, error: "" });
    }
    load().catch((reason: unknown) => {
      if (active)
        setState({
          key,
          url: "",
          error:
            reason instanceof Error ? reason.message : "Image decode failed.",
        });
    });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [key, image.base64, image.size, image.sha256]);
  const current = state.key === key;
  return (
    <div className="sensor-camera-view" data-testid="sensor-camera-view">
      <div className="sensor-view-controls">
        <span>
          <Image size={13} /> {image.width} × {image.height} px
        </span>
        <div>
          <button
            type="button"
            aria-label="Fit camera image"
            aria-pressed={mode === "fit"}
            onClick={() => setMode("fit")}
          >
            <Maximize2 size={13} /> Fit
          </button>
          <button
            type="button"
            aria-label="Camera image actual pixels"
            aria-pressed={mode === "pixels"}
            onClick={() => setMode("pixels")}
          >
            <ZoomIn size={13} /> 100%
          </button>
        </div>
      </div>
      <div className={`sensor-image-stage ${mode}`}>
        {current && state.error ? (
          <p role="alert">{state.error}</p>
        ) : current && state.url ? (
          <img
            src={state.url}
            alt={label}
            width={image.width}
            height={image.height}
            data-testid="decoded-camera-image"
            onLoad={(event) => {
              if (
                event.currentTarget.naturalWidth !== image.width ||
                event.currentTarget.naturalHeight !== image.height
              )
                setState({
                  key,
                  url: "",
                  error:
                    "Decoded image dimensions differ from the API descriptor.",
                });
            }}
            onError={() =>
              setState({
                key,
                url: "",
                error: "The browser could not display the decoded PNG.",
              })
            }
          />
        ) : (
          <p role="status">Verifying decoded image…</p>
        )}
      </div>
    </div>
  );
}
