import {
  Suspense,
  useEffect,
  useRef,
  useState,
  Component,
  type ReactNode,
} from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Line, OrbitControls } from "@react-three/drei";
import { Vector3 } from "three";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ArrowUpRight,
  Focus,
  Layers3,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  Video,
} from "lucide-react";
import type { Frame, Pose, RunDetail } from "../types";
import { Badge, Empty } from "./ui";

type ViewMode = "orbit" | "top" | "chase";
function CameraRig({ x, mode }: { x: number; mode: ViewMode }) {
  const { camera, controls } = useThree(),
    previous = useRef(x);
  useEffect(() => {
    const offset =
      mode === "top"
        ? [0, 66, 0.01]
        : mode === "chase"
          ? [-14, 7, 2]
          : [-30, 29, 31];
    camera.position.set(x + offset[0], offset[1], offset[2]);
    camera.lookAt(x + 8, 0, 0);
    const orbit = controls as unknown as
      { target: Vector3; update: () => void } | undefined;
    if (orbit) {
      orbit.target.set(x + 8, 0, 0);
      orbit.update();
    }
    previous.current = x;
  }, [camera, controls, mode]);
  useEffect(() => {
    const dx = x - previous.current;
    camera.position.x += dx;
    const orbit = controls as unknown as
      { target: Vector3; update: () => void } | undefined;
    if (orbit) {
      orbit.target.x += dx;
      orbit.update();
    }
    previous.current = x;
  }, [camera, controls, x]);
  return null;
}
function Vehicle({
  x,
  y,
  yaw,
  color,
  ego = false,
  type = "vehicle",
}: {
  x: number;
  y: number;
  yaw: number;
  color: string;
  ego?: boolean;
  type?: string;
}) {
  if (type === "pedestrian")
    return (
      <group position={[x, 0, y]}>
        <mesh position={[0, 0.85, 0]}>
          <capsuleGeometry args={[0.28, 1.05, 4, 8]} />
          <meshStandardMaterial color="#f8ab72" />
        </mesh>
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.04, 0]}>
          <ringGeometry args={[0.7, 0.79, 32]} />
          <meshBasicMaterial color="#ef9c69" transparent opacity={0.7} />
        </mesh>
      </group>
    );
  return (
    <group position={[x, 0, y]} rotation={[0, -yaw, 0]}>
      <mesh position={[0, 0.55, 0]} castShadow>
        <boxGeometry args={[4.5, 0.9, 1.82]} />
        <meshStandardMaterial color={color} metalness={0.35} roughness={0.3} />
      </mesh>
      <mesh position={[-0.3, 1.17, 0]} castShadow>
        <boxGeometry args={[2.25, 0.65, 1.55]} />
        <meshStandardMaterial color={color} metalness={0.3} roughness={0.25} />
      </mesh>
      <mesh position={[0.85, 1.19, 0]} rotation={[0, 0, -0.3]}>
        <boxGeometry args={[0.07, 0.55, 1.36]} />
        <meshStandardMaterial
          color="#1d3542"
          metalness={0.6}
          roughness={0.15}
        />
      </mesh>
      <mesh position={[-0.2, 1.53, 0]}>
        <boxGeometry args={[1.4, 0.025, 1.3]} />
        <meshStandardMaterial color="#233f49" />
      </mesh>
      {[-1.4, 1.4].flatMap((a) =>
        [-0.94, 0.94].map((b) => (
          <mesh
            key={`${a}-${b}`}
            position={[a, 0.35, b]}
            rotation={[Math.PI / 2, 0, 0]}
          >
            <cylinderGeometry args={[0.35, 0.35, 0.19, 12]} />
            <meshStandardMaterial color="#111b24" roughness={0.9} />
          </mesh>
        )),
      )}
      {[-0.62, 0.62].map((z) => (
        <mesh key={z} position={[2.26, 0.66, z]}>
          <boxGeometry args={[0.025, 0.15, 0.35]} />
          <meshBasicMaterial color="#faf5d2" />
        </mesh>
      ))}
      {ego && (
        <>
          <mesh position={[0, 0.035, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[3.7, 3.75, 80]} />
            <meshBasicMaterial color="#a8eec7" transparent opacity={0.28} />
          </mesh>
          <mesh position={[0, 0.03, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[6.4, 6.43, 80]} />
            <meshBasicMaterial color="#a8eec7" transparent opacity={0.15} />
          </mesh>
        </>
      )}
    </group>
  );
}
function Scene({
  frame: rawFrame,
  run,
  index,
  mode,
}: {
  frame: Frame;
  run: RunDetail;
  index: number;
  mode: ViewMode;
}) {
  const origin = run.trajectory[0].ego;
  const normalize = (pose: Pose): Pose => {
    const dx = pose.x - origin.x,
      dy = pose.y - origin.y;
    return {
      ...pose,
      x: dx * Math.cos(origin.yaw) + dy * Math.sin(origin.yaw),
      y: -dx * Math.sin(origin.yaw) + dy * Math.cos(origin.yaw),
      yaw: pose.yaw - origin.yaw,
    };
  };
  const frame = {
    ...rawFrame,
    ego: normalize(rawFrame.ego),
    actors: rawFrame.actors.map((actor) => ({ ...actor, ...normalize(actor) })),
  };
  const center = Math.floor(frame.ego.x / 40) * 40;
  const trail = run.trajectory
    .slice(Math.max(0, index - 100), index + 1)
    .map((f) => {
      const p = normalize(f.ego);
      return [p.x, 0.08, p.y] as [number, number, number];
    });
  return (
    <>
      <color attach="background" args={["#14242d"]} />
      <fog attach="fog" args={["#14242d", 95, 220]} />
      <ambientLight intensity={1.7} />
      <directionalLight
        position={[-30, 70, 20]}
        intensity={2.8}
        castShadow
        shadow-mapSize={[1024, 1024]}
      />
      <group position={[center, 0, 0]}>
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[400, 260]} />
          <meshStandardMaterial color="#23333b" roughness={1} />
        </mesh>
        <gridHelper
          args={[400, 80, "#33464b", "#2d3d44"]}
          position={[0, 0.018, 0]}
        />
        <mesh
          rotation={[-Math.PI / 2, 0, 0]}
          position={[0, 0.025, 0]}
          receiveShadow
        >
          <planeGeometry args={[400, 11.2]} />
          <meshStandardMaterial color="#34464d" roughness={1} />
        </mesh>
        {[-5.8, 5.8].map((z) => (
          <mesh key={z} position={[0, 0.12, z]}>
            <boxGeometry args={[400, 0.24, 0.35]} />
            <meshStandardMaterial color="#65747a" />
          </mesh>
        ))}
        {[-5.15, 5.15].map((z) => (
          <mesh key={z} position={[0, 0.04, z]} rotation={[-Math.PI / 2, 0, 0]}>
            <planeGeometry args={[400, 0.07]} />
            <meshBasicMaterial color="#b8c2bc" />
          </mesh>
        ))}
        {Array.from({ length: 54 }, (_, i) => (i - 27) * 7.5).flatMap((x) =>
          [-1.75, 1.75].map((z) => (
            <mesh
              key={`${x}-${z}`}
              position={[x, 0.04, z]}
              rotation={[-Math.PI / 2, 0, 0]}
            >
              <planeGeometry args={[3.2, 0.09]} />
              <meshBasicMaterial color="#a2b1ae" />
            </mesh>
          )),
        )}
        {[-1, 1].flatMap((side) =>
          Array.from({ length: 10 }, (_, i) => {
            const x = i * 26 - 130;
            return (
              <group key={`${side}-${i}`} position={[x, 0, side * 23]}>
                <mesh position={[0, 4 + (i % 3), 0]}>
                  <boxGeometry args={[12, 8 + (i % 3) * 2, 12]} />
                  <meshStandardMaterial
                    color={i % 2 ? "#2c4149" : "#293d46"}
                    roughness={0.8}
                  />
                </mesh>
                <mesh position={[0, 8.1 + (i % 3) * 2, 0]}>
                  <boxGeometry args={[12.4, 0.2, 12.4]} />
                  <meshStandardMaterial color="#3b535c" />
                </mesh>
                <mesh
                  position={[0, 0.025, -side * 11]}
                  rotation={[-Math.PI / 2, 0, 0]}
                >
                  <planeGeometry args={[17, 3]} />
                  <meshStandardMaterial color="#3c5154" />
                </mesh>
              </group>
            );
          }),
        )}
      </group>
      {trail.length > 1 && (
        <Line
          points={trail}
          color="#b8ed9c"
          lineWidth={3}
          transparent
          opacity={0.75}
        />
      )}
      <Vehicle {...frame.ego} color="#b6e49c" ego />
      {frame.actors.map((actor) => (
        <Vehicle
          key={actor.id}
          {...actor}
          color={actor.type === "pedestrian" ? "#f8ab72" : "#bcace8"}
        />
      ))}
      <OrbitControls
        makeDefault
        enablePan
        minDistance={8}
        maxDistance={130}
        maxPolarAngle={Math.PI / 2.08}
      />
      <CameraRig x={frame.ego.x} mode={mode} />
    </>
  );
}
class ViewportBoundary extends Component<
  { children: ReactNode },
  { error: boolean }
> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  render() {
    return this.state.error ? (
      <div className="viewport-fallback">
        <Layers3 size={32} />
        <strong>3D view is unavailable</strong>
        <span>
          Your browser needs WebGL. Recorded telemetry and evidence remain
          available below.
        </span>
      </div>
    ) : (
      this.props.children
    );
  }
}
function SignalChart({
  run,
  index,
  type,
}: {
  run: RunDetail;
  index: number;
  type: "speed" | "ttc";
}) {
  const data = run.trajectory.map((f) => ({
    time: f.time_s,
    value: type === "speed" ? f.ego.speed_mps * 3.6 : f.ttc_s,
  }));
  const current = data[index];
  const color = type === "speed" ? "#68a794" : "#ab9bd2";
  return (
    <div className="signal-chart">
      <div className="signal-label">
        <span>
          <i style={{ background: color }} />
          {type === "speed" ? "Ego velocity" : "Time to collision"}
        </span>
        <strong>
          {current?.value == null ? "—" : current.value.toFixed(1)}{" "}
          <small>{type === "speed" ? "km/h" : "s"}</small>
        </strong>
      </div>
      <ResponsiveContainer width="100%" height={86}>
        <AreaChart
          data={data}
          margin={{ top: 7, right: 2, left: -36, bottom: 0 }}
        >
          <defs>
            <linearGradient
              id={`gradient-${run.id}-${type}`}
              x1="0"
              y1="0"
              x2="0"
              y2="1"
            >
              <stop offset="0%" stopColor={color} stopOpacity={0.2} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid
            strokeDasharray="3 3"
            vertical={false}
            stroke="#e9eeea"
          />
          <XAxis
            dataKey="time"
            type="number"
            domain={[0, "dataMax"]}
            tick={{ fontSize: 9, fill: "#8d9690" }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `${v}s`}
          />
          <YAxis
            tick={{ fontSize: 9, fill: "#8d9690" }}
            axisLine={false}
            tickLine={false}
            tickCount={3}
          />
          <Tooltip
            formatter={(v) => (typeof v === "number" ? v.toFixed(2) : "—")}
            labelFormatter={(v) => `${Number(v).toFixed(1)} s`}
          />
          <Area
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={1.7}
            fill={`url(#gradient-${run.id}-${type})`}
            isAnimationActive={false}
            connectNulls={false}
          />
          <ReferenceLine
            x={current?.time ?? 0}
            stroke="#6e7c74"
            strokeDasharray="3 3"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
export default function Replay({
  run,
  onExplore,
  large = false,
}: {
  run: RunDetail | null;
  onExplore?: () => void;
  large?: boolean;
}) {
  const [index, setIndex] = useState(0),
    [playing, setPlaying] = useState(false),
    [mode, setMode] = useState<ViewMode>("orbit"),
    [expanded, setExpanded] = useState(false);
  useEffect(() => {
    setIndex(0);
    setPlaying(false);
  }, [run?.id]);
  useEffect(() => {
    if (!playing || !run) return;
    const interval = setInterval(
      () =>
        setIndex((current) => {
          if (current >= run.trajectory.length - 1) {
            setPlaying(false);
            return current;
          }
          return current + 1;
        }),
      Math.max(
        16,
        ((run.trajectory[1]?.time_s ?? 0.1) -
          (run.trajectory[0]?.time_s ?? 0)) *
          1000,
      ),
    );
    return () => clearInterval(interval);
  }, [playing, run]);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);
  if (!run || !run.trajectory.length)
    return (
      <section className="panel">
        <Empty
          title="Your first replay starts here"
          description="Execute a scenario to inspect the recorded trajectory, signals, and events."
        />
      </section>
    );
  const frame = run.trajectory[Math.min(index, run.trajectory.length - 1)],
    duration = run.trajectory.at(-1)?.time_s ?? 0;
  return (
    <section
      className={`panel replay-panel ${large ? "large" : ""} ${expanded ? "expanded" : ""}`}
    >
      <div className="panel-heading">
        <div>
          <div className="inline-title">
            <h2>Simulation replay</h2>
            <span className="mini-label">3D VIEW</span>
          </div>
          <p>
            {run.scenario_name}{" "}
            <span className="subtle">/ revision {run.scenario_revision}</span>
          </p>
        </div>
        {onExplore ? (
          <button
            className="icon-button"
            aria-label="Inspect this run"
            onClick={onExplore}
          >
            <ArrowUpRight size={19} />
          </button>
        ) : (
          <Badge status={run.verdict} />
        )}
      </div>
      <div className="viewport">
        <ViewportBoundary>
          <Canvas
            shadows
            dpr={[1, 1.6]}
            camera={{ position: [-30, 29, 31], fov: 44, near: 0.1, far: 500 }}
          >
            <Suspense fallback={null}>
              <Scene frame={frame} run={run} index={index} mode={mode} />
            </Suspense>
          </Canvas>
        </ViewportBoundary>
        <div className="viewport-top">
          <span className="view-label">
            <i />
            {run.adapter_id === "reference"
              ? "REFERENCE OBJECT MODEL"
              : `${run.adapter_id.toUpperCase()} · RECORDED TRAJECTORY`}
          </span>
          <div className="view-actions">
            {(["orbit", "top", "chase"] as ViewMode[]).map((value) => (
              <button
                key={value}
                onClick={() => setMode(value)}
                className={mode === value ? "selected" : ""}
              >
                {value === "orbit"
                  ? "Orbit"
                  : value === "top"
                    ? "Top-down"
                    : "Chase"}
              </button>
            ))}
            <button
              onClick={() => setExpanded((value) => !value)}
              aria-label={expanded ? "Exit full screen" : "Expand replay"}
            >
              <Maximize2 size={14} />
            </button>
          </div>
        </div>
        <div className="viewport-legend">
          <span>
            <i className="ego-dot" />
            Ego vehicle
          </span>
          <span>
            <i className="actor-dot" />
            Traffic actor
          </span>
          <span>
            <i className="trail-line" />
            Recorded trajectory
          </span>
        </div>
        <div className="viewport-compass">
          <span>N</span>
          <svg viewBox="0 0 40 40">
            <path d="M20 4 27 30 20 24 13 30Z" fill="#b3c8c6" />
            <circle
              cx="20"
              cy="20"
              r="19"
              fill="none"
              stroke="#6e8684"
              strokeWidth=".5"
            />
          </svg>
        </div>
        <div className="viewport-bottom">
          <span>
            <Focus size={12} /> X {frame.ego.x.toFixed(1)} · Y{" "}
            {frame.ego.y.toFixed(1)} m
          </span>
          <span>Schematic visualization · not a sensor image</span>
        </div>
        <div className="object-tag">
          <span className="ego-dot" /> EGO{" "}
          <strong>
            {(frame.ego.speed_mps * 3.6).toFixed(1)} <small>km/h</small>
          </strong>
        </div>
      </div>
      <div className="replay-controls">
        <button
          className="play-button"
          aria-label={playing ? "Pause replay" : "Play replay"}
          onClick={() => {
            if (index >= run.trajectory.length - 1) setIndex(0);
            setPlaying((value) => !value);
          }}
        >
          {playing ? (
            <Pause size={16} fill="currentColor" />
          ) : (
            <Play size={16} fill="currentColor" />
          )}
        </button>
        <button
          className="icon-button"
          aria-label="Reset replay"
          onClick={() => {
            setIndex(0);
            setPlaying(false);
          }}
        >
          <RotateCcw size={16} />
        </button>
        <span className="timecode">
          {frame.time_s.toFixed(1)} <span>/ {duration.toFixed(1)}s</span>
        </span>
        <div className="timeline">
          <input
            aria-label="Replay timeline"
            type="range"
            min="0"
            max={run.trajectory.length - 1}
            value={index}
            onChange={(e) => {
              setIndex(Number(e.target.value));
              setPlaying(false);
            }}
          />
          {run.events.map((event, i) => (
            <button
              key={i}
              className={`event-tick ${event.type.includes("collision") ? "danger" : ""}`}
              style={{
                left: `${duration ? (event.time_s / duration) * 100 : 0}%`,
              }}
              title={`${event.time_s.toFixed(1)}s · ${event.message}`}
              aria-label={`Jump to ${event.message}`}
              onClick={() => {
                setIndex(
                  run.trajectory.reduce(
                    (closest, f, n) =>
                      Math.abs(f.time_s - event.time_s) <
                      Math.abs(run.trajectory[closest].time_s - event.time_s)
                        ? n
                        : closest,
                    0,
                  ),
                );
                setPlaying(false);
              }}
            />
          ))}
        </div>
        <span className="playback-speed">1×</span>
        <Video size={15} className="muted" />
      </div>
      <div className="signal-grid">
        <SignalChart run={run} index={index} type="speed" />
        <SignalChart run={run} index={index} type="ttc" />
      </div>
    </section>
  );
}
