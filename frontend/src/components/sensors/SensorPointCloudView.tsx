import { Component, useEffect, useMemo, useState, type ReactNode } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import {
  BufferAttribute,
  BufferGeometry,
  Color,
  Matrix4,
  Quaternion,
  Vector3,
} from "three";
import { Focus, RotateCcw } from "lucide-react";
import { base64Bytes, readFloat32LE, verifyBuffer } from "./SensorData";

export interface PointPreviewData {
  xyz: { base64: string; size: number; sha256: string };
  intensity: { base64: string; size: number; sha256: string } | null;
  count: number;
}
export interface DeclaredTransform {
  translation_m: [number, number, number];
  rotation_xyzw: [number, number, number, number];
}
type View = "orbit" | "xy" | "xz" | "yz";
type ColorMode = "solid" | "z" | "intensity";

export function transformPoint(
  point: [number, number, number],
  transform: DeclaredTransform | null,
) {
  const value = new Vector3(...point);
  if (transform)
    value
      .applyQuaternion(new Quaternion(...transform.rotation_xyzw))
      .add(new Vector3(...transform.translation_m));
  return value.toArray() as [number, number, number];
}

function Cloud({
  positions,
  intensity,
  transform,
  colorMode,
  size,
  onPick,
}: {
  positions: Float32Array;
  intensity: Float32Array | null;
  transform: DeclaredTransform | null;
  colorMode: ColorMode;
  size: number;
  onPick: (index: number) => void;
}) {
  const geometry = useMemo(() => {
    const geometry = new BufferGeometry();
    geometry.setAttribute(
      "position",
      new BufferAttribute(positions.slice(), 3),
    );
    if (transform)
      geometry.applyMatrix4(
        new Matrix4().compose(
          new Vector3(...transform.translation_m),
          new Quaternion(...transform.rotation_xyzw),
          new Vector3(1, 1, 1),
        ),
      );
    if (colorMode !== "solid") {
      const values =
        colorMode === "intensity" && intensity
          ? intensity
          : Float32Array.from({ length: positions.length / 3 }, (_, index) =>
              geometry.attributes.position.getZ(index),
            );
      let min = Infinity,
        max = -Infinity;
      for (const value of values) {
        min = Math.min(min, value);
        max = Math.max(max, value);
      }
      const colors = new Float32Array(values.length * 3);
      const color = new Color();
      values.forEach((value, index) => {
        const fraction = max > min ? (value - min) / (max - min) : 0.5;
        color.setHSL(0.58 - fraction * 0.5, 0.68, 0.62);
        color.toArray(colors, index * 3);
      });
      geometry.setAttribute("color", new BufferAttribute(colors, 3));
    }
    geometry.computeBoundingSphere();
    return geometry;
  }, [positions, intensity, transform, colorMode]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <points
      geometry={geometry}
      onClick={(event) => {
        if (event.index !== undefined) {
          event.stopPropagation();
          onPick(event.index);
        }
      }}
    >
      <pointsMaterial
        size={size}
        sizeAttenuation={false}
        color={colorMode === "solid" ? "#ade3c7" : "white"}
        vertexColors={colorMode !== "solid"}
      />
    </points>
  );
}

function FitCamera({
  positions,
  transform,
  view,
  reset,
  handedness,
}: {
  positions: Float32Array;
  transform: DeclaredTransform | null;
  view: View;
  reset: number;
  handedness: string;
}) {
  const { camera, controls } = useThree();
  useEffect(() => {
    const min = new Vector3(Infinity, Infinity, Infinity),
      max = new Vector3(-Infinity, -Infinity, -Infinity);
    for (let index = 0; index < positions.length; index += 3) {
      const point = new Vector3(
        ...transformPoint(
          [positions[index], positions[index + 1], positions[index + 2]],
          transform,
        ),
      );
      if (handedness === "left_handed_z_up") point.y *= -1;
      min.min(point);
      max.max(point);
    }
    if (!positions.length) {
      min.set(-1, -1, -1);
      max.set(1, 1, 1);
    }
    const center = min.clone().add(max).multiplyScalar(0.5);
    const distance = Math.max(1, min.distanceTo(max)) * 1.1;
    const direction =
      view === "xy"
        ? new Vector3(0, 0, 1)
        : view === "xz"
          ? new Vector3(0, -1, 0)
          : view === "yz"
            ? new Vector3(1, 0, 0)
            : new Vector3(1, -1.3, 0.9).normalize();
    camera.up.set(0, view === "xy" ? 1 : 0, view === "xy" ? 0 : 1);
    camera.position.copy(center).addScaledVector(direction, distance);
    camera.near = Math.max(0.0001, distance / 10000);
    camera.far = Math.max(1000, distance * 100);
    camera.updateProjectionMatrix();
    camera.lookAt(center);
    const orbit = controls as unknown as
      { target: Vector3; update: () => void } | undefined;
    if (orbit) {
      orbit.target.copy(center);
      orbit.update();
    }
  }, [camera, controls, positions, transform, view, reset, handedness]);
  return null;
}

class SensorViewportBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="sensor-viewport-unavailable" role="status">
        3D rendering is unavailable in this browser. Decoded counts, coordinates
        and source evidence remain available.
      </div>
    ) : (
      this.props.children
    );
  }
}

export default function SensorPointCloudView({
  cloud,
  transform,
  frame,
  convention,
  coordinateDescription,
}: {
  cloud: PointPreviewData;
  transform: DeclaredTransform | null;
  frame: string;
  convention: string;
  coordinateDescription?: string;
}) {
  const [view, setView] = useState<View>("orbit");
  const [colorMode, setColorMode] = useState<ColorMode>("solid");
  const [size, setSize] = useState(3);
  const [reset, setReset] = useState(0);
  const [picked, setPicked] = useState(0);
  const key = `${cloud.xyz.sha256}:${cloud.intensity?.sha256 ?? ""}:${cloud.count}`;
  const [state, setState] = useState<{
    key: string;
    positions: Float32Array | null;
    intensity: Float32Array | null;
    error: string;
  }>({ key: "", positions: null, intensity: null, error: "" });
  useEffect(() => {
    let active = true;
    setState({ key, positions: null, intensity: null, error: "" });
    async function load() {
      const bytes = base64Bytes(cloud.xyz.base64, cloud.xyz.size);
      await verifyBuffer(bytes, cloud.xyz.sha256);
      const positions = readFloat32LE(bytes);
      if (positions.length !== cloud.count * 3)
        throw new Error("Point count differs from the XYZ buffer.");
      let intensity: Float32Array | null = null;
      if (cloud.intensity) {
        const bytes = base64Bytes(cloud.intensity.base64, cloud.intensity.size);
        await verifyBuffer(bytes, cloud.intensity.sha256);
        intensity = readFloat32LE(bytes);
        if (intensity.length !== cloud.count)
          throw new Error("Intensity count differs from the XYZ buffer.");
      }
      if (active) {
        setState({ key, positions, intensity, error: "" });
        setPicked(0);
      }
    }
    load().catch((error: unknown) => {
      if (active)
        setState({
          key,
          positions: null,
          intensity: null,
          error:
            error instanceof Error ? error.message : "Point decode failed.",
        });
    });
    return () => {
      active = false;
    };
  }, [key, cloud]);
  const current = state.key === key;
  const positions = current ? state.positions : null;
  const transformError = useMemo(() => {
    if (!positions || !transform) return "";
    for (let index = 0; index < positions.length; index += 3) {
      const point = transformPoint(
        [positions[index], positions[index + 1], positions[index + 2]],
        transform,
      );
      if (point.some((value) => !Number.isFinite(Math.fround(value))))
        return "Declared transform exceeds the finite float32 rendering range. Rig view is withheld.";
    }
    return "";
  }, [positions, transform]);
  const selected =
    positions && positions.length >= 3
      ? Math.min(picked, positions.length / 3 - 1)
      : null;
  const native =
    selected === null || !positions
      ? null
      : ([
          positions[selected * 3],
          positions[selected * 3 + 1],
          positions[selected * 3 + 2],
        ] as [number, number, number]);
  const shown =
    native && !transformError ? transformPoint(native, transform) : null;
  return (
    <div className="sensor-point-view" data-testid="sensor-point-view">
      <div className="sensor-view-controls">
        <span>{cloud.count.toLocaleString()} rendered points</span>
        <div>
          {(["orbit", "xy", "xz", "yz"] as View[]).map((mode) => (
            <button
              type="button"
              key={mode}
              aria-label={`Point cloud ${mode} view`}
              aria-pressed={view === mode}
              onClick={() => setView(mode)}
            >
              {mode === "orbit" ? "Orbit" : mode.toUpperCase()}
            </button>
          ))}
          <button
            type="button"
            aria-label="Fit point cloud"
            onClick={() => setReset((value) => value + 1)}
          >
            <Focus size={14} />
          </button>
        </div>
      </div>
      <div className="sensor-point-stage">
        {(current && state.error) || transformError ? (
          <p role="alert">{transformError || state.error}</p>
        ) : !positions ? (
          <p role="status">Verifying decoded points…</p>
        ) : cloud.count === 0 ? (
          <p>No finite points to render.</p>
        ) : (
          <SensorViewportBoundary>
            <Canvas
              dpr={[1, 1.5]}
              camera={{ fov: 48, position: [8, -10, 6], up: [0, 0, 1] }}
              raycaster={{
                params: {
                  Points: { threshold: 0.2 },
                  Line: { threshold: 1 },
                  Mesh: {},
                  LOD: {},
                  Sprite: {},
                },
              }}
            >
              <color attach="background" args={["#15282c"]} />
              <group scale={[1, convention === "left_handed_z_up" ? -1 : 1, 1]}>
                <Cloud
                  positions={positions}
                  intensity={state.intensity}
                  transform={transform}
                  colorMode={
                    colorMode === "intensity" && !state.intensity
                      ? "solid"
                      : colorMode
                  }
                  size={size}
                  onPick={setPicked}
                />
                <axesHelper args={[1]} />
              </group>
              <OrbitControls makeDefault enableDamping={false} />
              <FitCamera
                positions={positions}
                transform={transform}
                view={view}
                reset={reset}
                handedness={convention}
              />
            </Canvas>
          </SensorViewportBoundary>
        )}
        <span className="sensor-point-frame">
          {frame || "Frame ID not recorded"} · coordinates in m
        </span>
      </div>
      <div className="sensor-cloud-settings">
        <label>
          Color
          <select
            aria-label="Point cloud color"
            value={colorMode}
            onChange={(event) => setColorMode(event.target.value as ColorMode)}
          >
            <option value="solid">Single color</option>
            <option value="z">Z coordinate</option>
            <option value="intensity" disabled={!cloud.intensity}>
              Recorded intensity
            </option>
          </select>
        </label>
        <label>
          Point size
          <input
            aria-label="Point size"
            type="range"
            min="1"
            max="7"
            step="1"
            value={size}
            onChange={(event) => setSize(Number(event.target.value))}
          />
        </label>
        <button
          type="button"
          className="sensor-text-button"
          onClick={() => {
            setView("orbit");
            setColorMode("solid");
            setSize(3);
            setReset((value) => value + 1);
          }}
        >
          <RotateCcw size={12} /> Reset view
        </button>
      </div>
      {shown && (
        <div className="sensor-picked-point">
          <span data-testid="sensor-point-coordinate-label">
            Point {selected} ·{" "}
            {coordinateDescription ?? (transform ? "declared rig" : "native")}{" "}
            XYZ
          </span>
          <code data-testid="sensor-point-coordinate">
            {shown.map((value) => Number(value.toFixed(5))).join(", ")}
          </code>
          <small>
            Click a point to inspect its coordinates. Color scales use this
            sample only.
          </small>
        </div>
      )}
    </div>
  );
}
