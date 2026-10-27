import type { CaptureRecord, PointFrame } from "./types";

/** Decode retained tuples only; registration is derived from verified native
 * measurement/ego matrices. Never resample, track, fill or correct raw returns. */
export function decodeNativePoints(bytes: Uint8Array, record: CaptureRecord): PointFrame {
  if (record.kind !== "lidar" && record.kind !== "radar") throw new Error("Point decoder needs an original ranging stream.");
  const max = record.kind === "lidar" ? 3200 : 150;
  if (!Number.isSafeInteger(record.pointCount) || record.pointCount < 0 || record.pointCount > max || bytes.length !== record.member.size_bytes || bytes.length !== record.pointCount * 16) throw new Error("Native ranging count/stride/byte bound differs.");
  const raw = new Float32Array(record.pointCount * 4), positions = new Float32Array(record.pointCount * 3), values = new Float32Array(record.pointCount), view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let valueMin = Infinity, valueMax = -Infinity, anomalous = 0;
  for (let index = 0; index < record.pointCount; index++) {
    const tuple = [0, 1, 2, 3].map(slot => view.getFloat32(index * 16 + slot * 4, true));
    if (tuple.some(v => !Number.isFinite(v) || Math.abs(v) > 1e6)) throw new Error("Native ranging tuple is nonfinite or exceeds its numeric rendering bound.");
    raw.set(tuple, index * 4); let x: number, y: number, z: number, v: number;
    if (record.kind === "lidar") { [x, y, z, v] = tuple; if (v < 0 || v > 1) throw new Error("Native LiDAR intensity is outside its declared normalized layout."); }
    else {
      const [velocity, azimuth, altitude, depth] = tuple;
      if (Math.abs(azimuth) > Math.PI || Math.abs(altitude) > Math.PI / 2 || depth < 0) throw new Error("Native radar angle/depth is outside its supported tuple layout.");
      // CARLA native polar return -> native sensor LH. Nominal range is not a
      // hard clipping threshold: the retained engine may overshoot slightly.
      x = depth * Math.cos(altitude) * Math.cos(azimuth); y = depth * Math.cos(altitude) * Math.sin(azimuth); z = depth * Math.sin(altitude); v = velocity;
      if (Math.abs(velocity) > 100) anomalous++;
    }
    // LH local -> RH local, then verified observed relative RH matrix.
    const p = [x, -y, z, 1], m = record.relativeRh;
    for (let axis = 0; axis < 3; axis++) { const coordinate = m[axis].reduce((sum, n, k) => sum + n * p[k], 0); if (!Number.isFinite(coordinate) || Math.abs(coordinate) > 1e6) throw new Error("Registered native position exceeds the rendering bound."); positions[index * 3 + axis] = coordinate; }
    values[index] = v; valueMin = Math.min(valueMin, v); valueMax = Math.max(valueMax, v);
  }
  const warnings = anomalous ? [`Native radar velocity anomaly: ${anomalous} / ${record.pointCount} returns exceed |100| m/s (${valueMin.toFixed(3)}…${valueMax.toFixed(3)} m/s). Original values are retained; no physical velocity calibration is established.`] : [];
  return { kind: record.kind, count: record.pointCount, raw, positions, values, warnings, valueMin: record.pointCount ? valueMin : null, valueMax: record.pointCount ? valueMax : null };
}
