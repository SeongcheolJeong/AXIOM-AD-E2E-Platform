import { useEffect, useState } from "react";
import type { RunDetail } from "../../types";
import { liveArtifact, readLiveArtifact } from "./CarlaLiveData";
import type { CarlaPointSlice } from "./CarlaTypes";
import { readFloat32LE, verifyBuffer } from "../sensors/SensorData";
import SensorPointCloudView, {
  type PointPreviewData,
} from "../sensors/SensorPointCloudView";

export default function CarlaLivePoints({
  run,
  points,
}: {
  run: RunDetail;
  points: CarlaPointSlice | null;
}) {
  const fileKey = `${run.id}/${JSON.stringify(run.artifacts.filter((item) => item.name === "carla-stack-points.bin"))}`;
  const [file, setFile] = useState<{
    key: string;
    bytes: Uint8Array<ArrayBuffer> | null;
    error: string;
  }>({ key: "", bytes: null, error: "" });
  const [cloud, setCloud] = useState<{
    key: string;
    data: PointPreviewData | null;
    error: string;
  }>({ key: "", data: null, error: "" });
  const sliceKey = `${fileKey}/${JSON.stringify(points)}`;
  useEffect(() => {
    let active = true;
    const controller = new AbortController(),
      timer = setTimeout(() => controller.abort(), 20000);
    setFile({ key: fileKey, bytes: null, error: "" });
    void (async () => {
      const artifact = liveArtifact(
        run,
        "carla-stack-points.bin",
        "live_stack_points",
        20 * 1024 * 1024,
      );
      const bytes = await readLiveArtifact(artifact, controller.signal);
      if (active) setFile({ key: fileKey, bytes, error: "" });
    })()
      .catch((error: unknown) => {
        if (active)
          setFile({
            key: fileKey,
            bytes: null,
            error: controller.signal.aborted
              ? "Point artifact verification timed out."
              : (error as Error).message,
          });
      })
      .finally(() => clearTimeout(timer));
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [fileKey]);
  useEffect(() => {
    let active = true;
    setCloud({ key: sliceKey, data: null, error: "" });
    if (!points || file.key !== fileKey || !file.bytes) return;
    void (async () => {
      if (points.offset_bytes + points.size_bytes > file.bytes!.length)
        throw new Error("Point slice lies outside the verified artifact.");
      const bytes = file.bytes!.slice(
        points.offset_bytes,
        points.offset_bytes + points.size_bytes,
      );
      await verifyBuffer(bytes, points.sha256);
      if (readFloat32LE(bytes).some((value) => Math.abs(value) > 1e6))
        throw new Error("Point coordinates exceed the kernel bound.");
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 8192)
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
      if (active)
        setCloud({
          key: sliceKey,
          data: {
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
    })().catch((error: unknown) => {
      if (active)
        setCloud({
          key: sliceKey,
          data: null,
          error: (error as Error).message,
        });
    });
    return () => {
      active = false;
    };
  }, [sliceKey, file, fileKey, points]);
  const error =
    (file.key === fileKey && file.error) ||
    (cloud.key === sliceKey && cloud.error);
  return (
    <section className="carla-live-points" data-testid="carla-live-points">
      <h3>Exact consumed LiDAR points</h3>
      {!points ? (
        <p className="muted">
          No consumed point buffer was recorded for this interval. Missing
          points are not a clear corridor.
        </p>
      ) : error ? (
        <p className="inline-error" role="alert">
          Point evidence rejected: {error}
        </p>
      ) : cloud.key !== sliceKey || !cloud.data ? (
        <p role="status">Verifying whole point artifact and selected slice…</p>
      ) : (
        <>
          <p className="carla-subtle">
            {points.point_count} full points · {points.size_bytes} bytes · ego
            frame · no decimation. Both the entire artifact and this exact slice
            passed SHA-256 checks.
          </p>
          <code>{points.sha256}</code>
          {points.point_count > 0 ? (
            <SensorPointCloudView
              cloud={cloud.data}
              transform={null}
              frame="ego"
              convention="right_handed_z_up"
              coordinateDescription="Already reflected from CARLA left-handed XYZI and transformed using the pinned mount. X forward, Y left, Z up. No additional display transform."
            />
          ) : (
            <p>Verified empty input; no free-space claim.</p>
          )}
        </>
      )}
    </section>
  );
}
