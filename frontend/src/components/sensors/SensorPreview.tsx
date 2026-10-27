import { useMemo } from "react";
import SensorCameraView from "./SensorCameraView";
import SensorPointCloudView, {
  type DeclaredTransform,
} from "./SensorPointCloudView";
import type { SensorDecodeResult } from "./SensorTypes";

export default function SensorPreview({
  decoded,
  transform,
  frame,
  convention,
}: {
  decoded: SensorDecodeResult;
  transform: DeclaredTransform | null;
  frame: string;
  convention: string;
}) {
  const camera = useMemo(
    () =>
      decoded.camera
        ? {
            base64: decoded.camera.data_base64,
            size: decoded.camera.size_bytes,
            sha256: decoded.camera.sha256,
            width: decoded.camera.width,
            height: decoded.camera.height,
          }
        : null,
    [decoded.camera],
  );
  const cloud = useMemo(
    () =>
      decoded.point_cloud
        ? {
            xyz: {
              base64: decoded.point_cloud.data_base64,
              size: decoded.point_cloud.size_bytes,
              sha256: decoded.point_cloud.sha256,
            },
            intensity: decoded.point_cloud.intensity
              ? {
                  base64: decoded.point_cloud.intensity.data_base64,
                  size: decoded.point_cloud.intensity.size_bytes,
                  sha256: decoded.point_cloud.intensity.sha256,
                }
              : null,
            count: decoded.point_cloud.point_count,
          }
        : null,
    [decoded.point_cloud],
  );
  if (decoded.kind === "camera" && camera)
    return (
      <SensorCameraView
        image={camera}
        label={`Decoded camera frame · ${decoded.frame_id ?? "unknown source frame"}`}
      />
    );
  if (decoded.kind === "lidar" && cloud)
    return (
      <SensorPointCloudView
        cloud={cloud}
        transform={transform}
        frame={frame}
        convention={convention}
      />
    );
  return (
    <p className="sensor-preview-empty">
      The decoder returned no renderable payload.
    </p>
  );
}
