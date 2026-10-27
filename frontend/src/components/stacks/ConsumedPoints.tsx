import { useEffect, useState } from "react";
import { DownloadLink } from "../ArtifactAccess";
import { authHeaders, formatBytes } from "../../api";
import SensorPointCloudView, {
  type PointPreviewData,
} from "../sensors/SensorPointCloudView";
import { readFloat32LE, verifyBuffer } from "../sensors/SensorData";
import type { StackReplayPoints } from "./StackReplayTypes";

/** Inspect the saved bytes actually consumed by the stack; never reselect a source sample. */
export default function ConsumedPoints({
  points,
}: {
  points: StackReplayPoints;
}) {
  const key = `${points.download_url}/${points.sha256}/${points.point_count}/${points.frame_id}`;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    key: string;
    cloud: PointPreviewData | null;
    error: string;
  }>({ key: "", cloud: null, error: "" });
  useEffect(() => {
    const controller = new AbortController();
    setState({ key, cloud: null, error: "" });
    async function load() {
      if (
        points.encoding !== "xyz_float32_le" ||
        !Number.isSafeInteger(points.point_count) ||
        points.point_count < 0 ||
        points.point_count > 20000 ||
        points.size_bytes !== points.point_count * 12
      )
        throw new Error(
          "Consumed point buffer does not satisfy the bounded XYZ descriptor.",
        );
      const response = await fetch(points.download_url, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!response.ok)
        throw new Error(
          `Consumed point buffer unavailable (${response.status}).`,
        );
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength !== points.size_bytes)
        throw new Error(
          "Consumed point byte count differs from its immutable descriptor.",
        );
      await verifyBuffer(bytes, points.sha256);
      readFloat32LE(bytes);
      if (controller.signal.aborted) return;
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 8192)
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
      setState({
        key,
        cloud: {
          xyz: {
            base64: btoa(binary),
            size: bytes.length,
            sha256: points.sha256,
          },
          intensity: null,
          count: points.point_count,
        },
        error: "",
      });
    }
    void load().catch((reason: unknown) => {
      if (!controller.signal.aborted)
        setState({
          key,
          cloud: null,
          error:
            reason instanceof Error
              ? reason.message
              : "Consumed point buffer unavailable.",
        });
    });
    return () => controller.abort();
  }, [
    key,
    attempt,
    points.download_url,
    points.sha256,
    points.size_bytes,
    points.point_count,
    points.encoding,
  ]);
  const cloud = state.key === key ? state.cloud : null;
  const error = state.key === key ? state.error : "";
  return (
    <div
      className="stack-consumed-points"
      data-testid="stack-consumed-points"
      data-points-sha256={cloud ? points.sha256 : undefined}
    >
      {error ? (
        <div className="stack-preview-empty" role="alert">
          <p>{error}</p>
          <button
            type="button"
            className="button secondary small"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Retry consumed points
          </button>
        </div>
      ) : !cloud ? (
        <div className="stack-preview-empty" role="status">
          Verifying the exact consumed point buffer…
        </div>
      ) : (
        <SensorPointCloudView
          cloud={cloud}
          transform={null}
          frame={points.frame_id}
          convention="right_handed_z_up"
          coordinateDescription="consumed ego frame"
        />
      )}
      <div className="stack-buffer-evidence">
        <span>
          Saved consumed XYZ · {points.point_count.toLocaleString()} points ·{" "}
          {formatBytes(points.size_bytes)}
        </span>
        <code>{points.sha256}</code>
        <DownloadLink
          className="button secondary small"
          href={points.download_url}
          download
        >
          Download consumed points
        </DownloadLink>
      </div>
    </div>
  );
}
