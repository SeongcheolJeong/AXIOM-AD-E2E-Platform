import { useEffect, useState } from "react";
import { authHeaders } from "../../api";
import { verifyBuffer } from "../sensors/SensorData";
import type { InferenceImage } from "../perception/PerceptionTypes";

/** Every editor/diagnostic overlay is tied to a verified saved-image identity. */
export function useDatasetImage(image: InferenceImage) {
  const key = `${image.download_url}/${image.sha256}/${image.width}/${image.height}`;
  const [state, setState] = useState({ key: "", url: "", error: "" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let url = "";
    setState({ key, url: "", error: "" });
    async function load() {
      if (
        !Number.isSafeInteger(image.size_bytes) ||
        image.size_bytes < 1 ||
        image.size_bytes > 8 * 1024 * 1024 ||
        image.width * image.height > 2_000_000
      )
        throw new Error("Saved image exceeds the dataset image bound.");
      const response = await fetch(image.download_url, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!response.ok)
        throw new Error(
          `Saved dataset image unavailable (${response.status}).`,
        );
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength !== image.size_bytes)
        throw new Error(
          "Dataset image size differs from its immutable descriptor.",
        );
      await verifyBuffer(bytes, image.sha256);
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
      const img = new window.Image();
      await new Promise<void>((resolve, reject) => {
        img.onload = () =>
          img.naturalWidth === image.width && img.naturalHeight === image.height
            ? resolve()
            : reject(
                new Error(
                  "Decoded image dimensions differ from the dataset descriptor.",
                ),
              );
        img.onerror = () =>
          reject(new Error("The saved dataset PNG cannot be decoded."));
        img.src = url;
      });
      if (!controller.signal.aborted) setState({ key, url, error: "" });
    }
    void load().catch((reason: unknown) => {
      if (!controller.signal.aborted)
        setState({
          key,
          url: "",
          error:
            reason instanceof Error
              ? reason.message
              : "Dataset image unavailable.",
        });
    });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [
    key,
    image.download_url,
    image.sha256,
    image.size_bytes,
    image.width,
    image.height,
    attempt,
  ]);
  return {
    url: state.key === key ? state.url : "",
    error: state.key === key ? state.error : "",
    retry: () => setAttempt((value) => value + 1),
  };
}
