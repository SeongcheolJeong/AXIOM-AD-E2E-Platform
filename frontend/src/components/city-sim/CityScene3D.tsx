import { Component, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import {
  Edges,
  Html,
  Line,
  OrbitControls,
  PerspectiveCamera,
} from "@react-three/drei";
import {
  BufferGeometry,
  DoubleSide,
  Float32BufferAttribute,
  MOUSE,
  TOUCH,
  Vector3,
} from "three";
import { actorColor } from "../../scenario-authoring";
import {
  perspectiveFitDistance,
  SCENE_CAMERA_DIRECTION,
} from "../object-scene/object-scene";
import type { CityScene, CityState } from "./city-scene";
import CityCarVisual from "./CityCarVisual";
import { decisionCameraStates } from "./city-camera";
import type { CityCameraMode } from "./city-camera";
import { currentPlan, decisionLabel, decisionRelations, followGapTarget, reactiveDecision } from "./city-decisions";
import {
  cityBounds,
  cityBoxCenter,
  cityToThree,
  laneRibbonData,
} from "./city-geometry";
type Actor = CityScene["actors"][number];
type Props = {
  scene: CityScene;
  index: number;
  selected: string;
  hidden: Set<string>;
  follow: boolean;
  initialFit?: "trace" | "map" | "actor";
  onSelect: (id: string) => void;
  cameraMode?: CityCameraMode;
  onCameraModeChange?: (mode: CityCameraMode) => void;
  onManualInteraction?: () => void;
  decisionFocusIndex?: number;
  presentationActive?: boolean;
};
type Fit = { count: number; mode: "trace" | "map" | "actor" | "overhead" };
type PathLayers = { desired: boolean; actual: boolean; planned: boolean };

function ActorProxy({
  actor,
  state,
  selected,
  targeted,
  labels,
  boxes,
  select,
}: {
  actor: Actor;
  state: CityState;
  selected: boolean;
  targeted: boolean;
  labels: boolean;
  boxes: boolean;
  select: () => void;
}) {
  const dims = actor.dimensions_m,
    length = dims.length_m,
    width = dims.width_m,
    height = dims.height_m,
    center = cityBoxCenter(state, actor.bbox_center),
    color = actorColor(actor.id),
    base = -height / 2;
  const tall = /bus|truck|van|carlacola|ambulance|firetruck/.test(
      actor.native_blueprint,
    ),
    cycle = /bicycle|crossbike|century|low_rider|zx125|yzf|ninja|vespa/.test(
      actor.native_blueprint,
    ),
    cone = /cone|trafficwarning/.test(actor.native_blueprint);
  return (
    <group
      position={cityToThree(center)}
      rotation={[0, -state.heading_rad, 0]}
      onClick={(event) => {
        event.stopPropagation();
        select();
      }}
    >
      {actor.kind === "vehicle" ? (
        cycle ? (
          <>
            {[-1, 1].map((end) => (
              <mesh
                key={end}
                position={[end * length * 0.31, base + height * 0.24, 0]}
                rotation={[Math.PI / 2, 0, 0]}
              >
                <cylinderGeometry
                  args={[height * 0.22, height * 0.22, width * 0.35, 18]}
                />
                <meshStandardMaterial color="#162027" roughness={0.8} />
              </mesh>
            ))}
            <mesh position={[0, base + height * 0.53, 0]}>
              <boxGeometry args={[length * 0.6, height * 0.25, width * 0.55]} />
              <meshStandardMaterial color={color} />
            </mesh>
            <mesh position={[length * 0.24, base + height * 0.78, 0]}>
              <boxGeometry args={[length * 0.08, height * 0.4, width]} />
              <meshStandardMaterial color="#99bac0" />
            </mesh>
          </>
        ) : !tall ? (
          <CityCarVisual length={length} width={width} height={height} color={color} />
        ) : (
          <>
            <mesh position={[0, base + height * 0.33, 0]}>
              <boxGeometry args={[length, height * 0.44, width * 0.96]} />
              <meshStandardMaterial
                color={color}
                roughness={0.48}
                metalness={0.12}
              />
            </mesh>
            <mesh
              position={[
                -length * (tall ? 0.02 : 0.08),
                base + height * (tall ? 0.64 : 0.72),
                0,
              ]}
            >
              <boxGeometry
                args={[
                  length * (tall ? 0.84 : 0.53),
                  height * (tall ? 0.69 : 0.47),
                  width * 0.82,
                ]}
              />
              <meshStandardMaterial
                color={tall ? color : "#274450"}
                roughness={0.26}
                metalness={0.22}
              />
            </mesh>
            {[-1, 1].flatMap((side) =>
              [-1, 1].map((end) => (
                <mesh
                  key={`${side}-${end}`}
                  position={[
                    end * length * 0.32,
                    base + height * 0.17,
                    side * width * 0.45,
                  ]}
                  rotation={[Math.PI / 2, 0, 0]}
                >
                  <cylinderGeometry
                    args={[height * 0.16, height * 0.16, width * 0.1, 16]}
                  />
                  <meshStandardMaterial color="#131d23" roughness={0.9} />
                </mesh>
              )),
            )}
            {[-1, 1].map((side) => (
              <group key={side}>
                <mesh
                  position={[
                    length * 0.48,
                    base + height * 0.42,
                    side * width * 0.34,
                  ]}
                >
                  <boxGeometry args={[0.04, height * 0.1, width * 0.2]} />
                  <meshBasicMaterial color="#fff3c9" />
                </mesh>
                <mesh
                  position={[
                    -length * 0.48,
                    base + height * 0.42,
                    side * width * 0.34,
                  ]}
                >
                  <boxGeometry args={[0.04, height * 0.1, width * 0.18]} />
                  <meshBasicMaterial color="#f26e5c" />
                </mesh>
              </group>
            ))}
          </>
        )
      ) : actor.kind === "pedestrian" ? (
        <>
          <mesh position={[0, base + height * 0.56, 0]}>
            <cylinderGeometry
              args={[width * 0.31, width * 0.39, height * 0.43, 12]}
            />
            <meshStandardMaterial color={color} />
          </mesh>
          <mesh
            position={[0, base + height * 0.86, 0]}
            scale={[
              Math.min(length, width) * 0.32,
              height * 0.12,
              width * 0.32,
            ]}
          >
            <sphereGeometry args={[1, 16, 12]} />
            <meshStandardMaterial color="#dfbea0" />
          </mesh>
          {[-1, 1].map((side) => (
            <group key={side}>
              <mesh position={[0, base + height * 0.2, side * width * 0.19]}>
                <boxGeometry
                  args={[length * 0.29, height * 0.38, width * 0.25]}
                />
                <meshStandardMaterial color="#36444f" />
              </mesh>
              <mesh position={[0, base + height * 0.56, side * width * 0.43]}>
                <boxGeometry
                  args={[length * 0.22, height * 0.35, width * 0.14]}
                />
                <meshStandardMaterial color={color} />
              </mesh>
            </group>
          ))}
        </>
      ) : cone ? (
        <mesh>
          <coneGeometry args={[Math.min(length, width) * 0.5, height, 12]} />
          <meshStandardMaterial color={color} roughness={0.9} />
        </mesh>
      ) : (
        <mesh>
          <boxGeometry args={[length, height, width]} />
          <meshStandardMaterial color={color} roughness={0.85} />
          <Edges color="#e0cc95" />
        </mesh>
      )}
      {(boxes || selected || targeted) && (
        <mesh>
          <boxGeometry args={[length, height, width]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
          <Edges
            color={targeted ? "#ff9f43" : selected ? "#ffdc83" : color}
            lineWidth={targeted ? 2 : selected ? 1.3 : 0.7}
          />
        </mesh>
      )}
      <mesh position={[0, base + 0.025, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry
          args={[
            Math.max(length, width) * 0.56,
            Math.max(length, width) * (targeted ? 0.64 : 0.58),
            40,
          ]}
        />
        <meshBasicMaterial
          color={targeted ? "#ff9f43" : selected ? "#f5ce73" : color}
          transparent
          opacity={targeted ? 0.95 : selected ? 0.8 : 0.2}
          side={DoubleSide}
        />
      </mesh>
      {labels && (
        <Html position={[0, height / 2 + 0.8, 0]} center>
          <button
            className={`city-3d-label ${selected ? "selected" : ""} ${targeted ? "decision-target" : ""}`}
            aria-label={`${actor.id}${targeted ? " · 현재 간격 판단 대상" : ""} · ${state.speed_mps.toFixed(1)} m/s`}
            title={`${actor.id}${targeted ? " · 현재 간격 판단 대상" : ""} · ${state.speed_mps.toFixed(1)} m/s`}
            onClick={select}
            style={{ borderColor: targeted ? "#ff9f43" : color, ...(targeted ? { background: "#513a22", color: "#ffe0aa" } : {}) }}
          >
            {actor.id}
          </button>
        </Html>
      )}
    </group>
  );
}
function Road({ scene }: { scene: CityScene }) {
  const data = useMemo(() => laneRibbonData(scene.map.lanes), [scene]),
    geometry = useMemo(() => {
      const result = new BufferGeometry();
      result.setAttribute(
        "position",
        new Float32BufferAttribute(data.positions, 3),
      );
      result.setIndex(data.indices);
      result.computeVertexNormals();
      return result;
    }, [data]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <>
      <mesh geometry={geometry} receiveShadow>
        <meshStandardMaterial color="#475c66" roughness={.92} side={DoubleSide} />
      </mesh>
      {scene.map.lanes
        .filter((lane) => lane.points.length > 1)
        .map((lane) => (
          <Line
            key={lane.id}
            points={lane.points.map((point) => cityToThree(point, 0.025))}
            color="#edf0dd"
            lineWidth={.9}
            dashed
            dashSize={2}
            gapSize={2}
            transparent
            opacity={0.72}
          />
        ))}
      {data.isolated.map(({ point, width }, index) => (
        <mesh
          key={index}
          position={cityToThree(point, 0.025)}
          rotation={[-Math.PI / 2, 0, 0]}
        >
          <circleGeometry args={[Math.min(0.25, width / 2), 10]} />
          <meshBasicMaterial color="#879b9f" />
        </mesh>
      ))}
    </>
  );
}
function Signals({
  scene,
  index,
  labels,
}: {
  scene: CityScene;
  index: number;
  labels: boolean;
}) {
  const signals = scene.map.signals ?? [],
    observations = scene.frames[index].signals ?? [];
  return (
    <>
      {signals.map((signal) => {
        const observed = observations.find((item) => item.id === signal.id),
          state = observed?.state ?? "unknown",
          color =
            state === "red"
              ? "#ff715c"
              : state === "yellow"
                ? "#ffd36d"
                : state === "green"
                  ? "#79d7a0"
                  : "#81909b";
        return (
          <group key={signal.id} position={cityToThree(signal.pose)}>
            <mesh position={[0, 1.25, 0]}>
              <cylinderGeometry args={[0.045, 0.06, 2.5, 8]} />
              <meshStandardMaterial color="#88989e" />
            </mesh>
            <mesh position={[0, 2.75, 0]}>
              <boxGeometry args={[0.25, 0.6, 0.22]} />
              <meshStandardMaterial color="#1b2830" />
            </mesh>
            <mesh position={[0, 2.75, -0.12]}>
              <sphereGeometry args={[0.1, 10, 10]} />
              <meshBasicMaterial
                color={signal.kind === "stop" ? "#cb695a" : color}
              />
            </mesh>
            {labels && (
              <Html position={[0, 3.5, 0]} center distanceFactor={40}>
                <span className="city-signal-label" style={{ color }}>
                  {signal.kind === "stop"
                    ? "STOP"
                    : observed
                      ? `${signal.id} · ${state}`
                      : "상태 미보존"}
                </span>
              </Html>
            )}
          </group>
        );
      })}
    </>
  );
}
function SceneCamera(props: Props & { fit: Fit; zoom: { count: number; factor: number } }) {
  const { scene, index, selected, follow, fit, zoom, decisionFocusIndex, onManualInteraction } = props;
  const { camera, size, invalidate, gl } = useThree(), controls = useRef<React.ComponentRef<typeof OrbitControls>>(null);
  const destination = useRef<{ position: Vector3; target: Vector3 } | null>(null), initialized = useRef(false);
  const lastComposition = useRef<{ scene: CityScene; key: string } | null>(null);
  const renderCount = useRef(0), performanceEnabled = new URLSearchParams(location.search).get("cityPerf") === "1";
  useEffect(() => {
    const tracking = props.cameraMode ? props.cameraMode !== "all" : follow;
    const compositionKey = `${fit.count}:${fit.mode}:${selected}:${size.width}:${size.height}:${decisionFocusIndex}:${tracking}`;
    if (!tracking && lastComposition.current?.scene === scene && lastComposition.current.key === compositionKey) return;
    lastComposition.current = { scene, key: compositionKey };
    const context = fit.mode === "actor" || fit.mode === "overhead" || tracking;
    const points = fit.mode === "map" ? scene.map.lanes.flatMap((lane) => lane.points)
      : context ? decisionCameraStates(scene, index, selected, decisionFocusIndex)
      : scene.frames.flatMap((frame) => frame.actors);
    const bounds = cityBounds(points), x = (bounds.xmin + bounds.xmax) / 2, z = (bounds.ymin + bounds.ymax) / 2, y = (bounds.zmin + bounds.zmax) / 2;
    const target = new Vector3(x, y, z), overhead = fit.mode === "overhead";
    const distance = overhead
      ? Math.max(bounds.ymax - bounds.ymin, (bounds.xmax - bounds.xmin) / (size.width / size.height)) / (2 * Math.tan(42 * Math.PI / 360)) * 1.15
      : perspectiveFitDistance(bounds, size.width / size.height, 42, { min: bounds.zmin - y, max: bounds.zmax - y });
    const direction = overhead ? { x: .001, y: 1, z: .001 } : SCENE_CAMERA_DIRECTION;
    const position = new Vector3(x + distance * direction.x, y + distance * direction.y, z + distance * direction.z);
    destination.current = { position, target };
    if (!initialized.current) { camera.position.copy(position); controls.current?.target.copy(target); camera.lookAt(target); initialized.current = true; }
    invalidate();
  }, [scene, index, selected, follow, fit, props.cameraMode, decisionFocusIndex, camera, size.width, size.height, invalidate]);
  useFrame((_state, delta) => {
    if (performanceEnabled) gl.domElement.dataset.renderCount = String(++renderCount.current);
    if (!destination.current || !controls.current) return;
    const amount = 1 - Math.exp(-Math.min(delta, .1) * 7);
    camera.position.lerp(destination.current.position, amount);
    controls.current.target.lerp(destination.current.target, amount);
    controls.current.update();
    if (camera.position.distanceTo(destination.current.position) > .01 || controls.current.target.distanceTo(destination.current.target) > .01) invalidate();
    else destination.current = null;
  });
  useEffect(() => {
    if (!zoom.count || !controls.current) return;
    destination.current = null;
    camera.position.copy(controls.current.target.clone().add(camera.position.clone().sub(controls.current.target).divideScalar(zoom.factor)));
    controls.current.update(); invalidate();
  }, [camera, zoom, invalidate]);
  return <OrbitControls ref={controls} makeDefault enableDamping={false} screenSpacePanning
    onStart={() => { destination.current = null; onManualInteraction?.(); }}
    mouseButtons={{ LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.PAN }} touches={{ ONE: TOUCH.ROTATE, TWO: TOUCH.DOLLY_PAN }}
    minDistance={3} maxDistance={10000} maxPolarAngle={Math.PI / 2.01} />;
}
function Content(
  props: Props & {
    labels: boolean;
    boxes: boolean;
    paths: PathLayers;
    fit: Fit;
    zoom: { count: number; factor: number };
  },
) {
  const { scene, index, hidden, selected, onSelect, labels, boxes, paths } =
    props;
  const leadTarget = followGapTarget(scene.frames[index].actors.find(actor => actor.id === selected)!, scene.frames[index].actors);
  const extent = useMemo(
    () => cityBounds(scene.map.lanes.flatMap((lane) => lane.points)),
    [scene],
  );
  const traces = useMemo(
    () =>
      scene.actors
        .filter((actor) => !hidden.has(actor.id))
        .map((actor) => ({
          actor,
          points: scene.frames.slice(0, index + 1).map((frame) =>
            cityToThree(
              frame.actors.find((state) => state.id === actor.id)!,
              0.16,
            ),
          ),
        })),
    [scene, hidden, index],
  );
  return (
    <>
      <color attach="background" args={["#20343e"]} />
      <ambientLight intensity={.85} />
      <hemisphereLight args={["#cbe3ec", "#263e34", 1]} />
      <directionalLight position={[100, 200, 100]} intensity={2.6} castShadow shadow-mapSize={[1024, 1024]} shadow-camera-left={-160} shadow-camera-right={160} shadow-camera-top={160} shadow-camera-bottom={-160} shadow-camera-near={1} shadow-camera-far={700} shadow-bias={-.001} />
      <mesh receiveShadow
        position={[
          (extent.xmin + extent.xmax) / 2,
          extent.zmin - 0.1,
          (extent.ymin + extent.ymax) / 2,
        ]}
        rotation={[-Math.PI / 2, 0, 0]}
      >
        <planeGeometry
          args={[
            extent.xmax - extent.xmin + 30,
            extent.ymax - extent.ymin + 30,
          ]}
        />
        <meshStandardMaterial color="#2d4547" roughness={1} />
      </mesh>
      <Road scene={scene} />
      <Signals scene={scene} index={index} labels={labels} />
      {paths.desired &&
        scene.actors
          .filter((actor) => !hidden.has(actor.id))
          .map(
            (actor) =>
              actor.route.length > 1 && (
                <Line
                  key={`desired-${actor.id}`}
                  points={actor.route.map((point) => cityToThree(point, 0.11))}
                  color={actorColor(actor.id)}
                  lineWidth={actor.id === selected ? 2 : 1}
                  dashed
                  dashSize={1}
                  gapSize={0.6}
                  transparent
                  opacity={0.5}
                />
              ),
          )}
      {paths.actual &&
        traces.map(
          ({ actor, points }) =>
            points.length > 1 && (
              <Line
                key={`actual-${actor.id}`}
                points={points}
                color={actorColor(actor.id)}
                lineWidth={actor.id === selected ? 3 : 1.5}
              />
            ),
        )}
      {paths.planned &&
        Object.entries(scene.planned_paths)
          .filter(([id]) => !hidden.has(id))
          .map(([id, series]) => {
            const points = currentPlan(
              series,
              index,
              scene.actors.find((a) => a.id === id)?.controller.kind ===
                "reactive",
            )?.points;
            return points && points.length > 1 ? (
              <Line
                key={`plan-${id}`}
                points={points.map((point) => cityToThree(point, 0.22))}
                color="#b9b9ff"
                lineWidth={2}
              />
            ) : null;
          })}
      {decisionRelations(
        scene.frames[index].actors.find((a) => a.id === selected)!,
        scene.frames[index].actors,
      ).map((relation, i) => (
        <group key={`relation-${i}`}>
          <Line
            points={[
              cityToThree(relation.from, 0.6),
              cityToThree(relation.to, 0.6),
            ]}
            color="#efac69"
            lineWidth={2}
            dashed
            dashSize={1}
            gapSize={0.5}
          />
        </group>
      ))}
      {scene.frames[index].actors
        .filter((state) => !hidden.has(state.id))
        .map((state) => (
          <ActorProxy
            key={state.id}
            actor={scene.actors.find((item) => item.id === state.id)!}
            state={state}
            selected={state.id === selected}
            targeted={state.id === leadTarget?.target.id}
            labels={labels}
            boxes={boxes}
            select={() => onSelect(state.id)}
          />
        ))}
      <SceneCamera {...props} />
    </>
  );
}
class ViewportBoundary extends Component<
  { children: React.ReactNode },
  { error: boolean }
> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  render() {
    return this.state.error ? (
      <div className="city-3d-error" role="alert">
        3D WebGL 화면을 열 수 없습니다. 2D 지도와 원본 카메라 Viewer를 사용할 수
        있습니다.
      </div>
    ) : (
      this.props.children
    );
  }
}
export default function CityScene3D(props: Props) {
  const [labels, setLabels] = useState(true),
    [boxes, setBoxes] = useState(false),
    [paths, setPaths] = useState<PathLayers>({
      desired: true,
      actual: true,
      planned: true,
    }),
    [fit, setFit] = useState<Fit>({
      count: 0,
      mode: props.initialFit ?? "trace",
    }),
    [zoom, setZoom] = useState({ count: 0, factor: 1 });
  useEffect(() => {
    if (props.cameraMode) setFit((value) => ({ count: value.count + 1, mode: props.cameraMode === "overhead" ? "overhead" : props.cameraMode === "follow" ? "actor" : "trace" }));
  }, [props.cameraMode]);
  function chooseFit(mode: Fit["mode"]) {
    props.onManualInteraction?.();
    props.onCameraModeChange?.(mode === "overhead" ? "overhead" : mode === "actor" ? "follow" : "all");
    setFit((value) => ({ count: value.count + 1, mode }));
  }
  const currentState = props.scene.frames[props.index].actors.find((actor) => actor.id === props.selected)!;
  const decision = reactiveDecision(currentState);
  const leadTarget = followGapTarget(currentState, props.scene.frames[props.index].actors);
  const targets = decisionRelations(currentState, props.scene.frames[props.index].actors).slice(0, 2);
  return (
    <div
      className="city-map-canvas city-3d-canvas"
      aria-label="City 3D viewport"
    >
      <div className="city-3d-viewport">
      <ViewportBoundary>
        <Canvas
          frameloop={props.presentationActive ? "always" : "demand"}
          shadows
          dpr={[1, 1.75]}
          gl={{ antialias: true, preserveDrawingBuffer: true }}
          role="img"
          aria-label="Native CARLA city geometry 3D schematic"
        >
          <PerspectiveCamera
            makeDefault
            position={[20, 40, 30]}
            fov={42}
            near={0.1}
            far={20000}
          />
          <Content
            {...props}
            fit={fit}
            zoom={zoom}
            labels={labels}
            boxes={boxes}
            paths={paths}
          />
        </Canvas>
      </ViewportBoundary>
      </div>
      <div className="city-3d-toolbar" role="group" aria-label="3D 시점 조절">
        <button
          type="button"
          onClick={() => chooseFit("map")}
        >
          지도 전체
        </button>
        <button
          type="button"
          onClick={() => chooseFit("trace")}
        >
          전체 궤적
        </button>
        <button
          type="button"
          onClick={() => chooseFit("actor")}
        >
          상황 보기
        </button>
        <button type="button" onClick={() => chooseFit("overhead")}>위에서 보기</button>
        <button
          type="button"
          aria-label="Zoom in city 3D"
          onClick={() =>
            (props.onManualInteraction?.(), setZoom((value) => ({ count: value.count + 1, factor: 1.4 })))
          }
        >
          ＋
        </button>
        <button
          type="button"
          aria-label="Zoom out city 3D"
          onClick={() =>
            (props.onManualInteraction?.(), setZoom((value) => ({ count: value.count + 1, factor: 1 / 1.4 })))
          }
        >
          −
        </button>
      </div>
      <section className="city-3d-readout" aria-label="3D 현재 판단 정보">
        <div className="city-3d-summary">
          <strong title={`${props.selected} · ${decision ? decisionLabel(decision.reason) : "보존 객체 좌표"}`}>
            {props.selected} · {decision ? decisionLabel(decision.reason) : "보존 객체 좌표"}
          </strong>
          {decision && <span>{decision.stop ? "정지 요청" : `${decision.speed_mps.toFixed(1)} m/s 요청`}</span>}
        </div>
        <div className="city-3d-target-summary">
          {targets.length ? targets.map((target) => <span key={target.to.id}><b>{target.to.id}</b> · {target.gapM === undefined ? target.label : `간격 ${target.gapM.toFixed(1)} m`}{target.requiredGapM === undefined ? "" : ` / 요구 ${target.requiredGapM.toFixed(1)} m`}</span>) : <span>기록된 판단 대상 없음</span>}
        </div>
        <details className="city-3d-details">
          <summary>판단·표시 상세</summary>
          {decision && <p><b>{props.selected} · {decisionLabel(decision.reason)}</b><br />보존 planner 진단 · 관측 {props.scene.frames[props.index].time_s.toFixed(2)}s · 입력 frame {props.scene.frames[props.index].input_frame}</p>}
          {leadTarget && <p>선행차 {leadTarget.target.id} · GT 속도 {leadTarget.leadSpeedMps.toFixed(1)} m/s · 주황색 테두리</p>}
          <p>{props.scene.map.name} · 보존 XYZ/yaw<br />native 외곽에 맞춘 Kenney 시각 모델 · CARLA 원본 mesh 아님 · pitch/roll·바퀴 움직임 미보존</p>
      <div className="city-3d-options">
        <label>
          <input
            type="checkbox"
            checked={labels}
            onChange={(event) => setLabels(event.target.checked)}
          />
          객체 / 신호 이름
        </label>
        <label>
          <input
            type="checkbox"
            checked={boxes}
            onChange={(event) => setBoxes(event.target.checked)}
          />
          Native bounding boxes
        </label>
      </div>
      <div className="city-3d-legend">
        {(
          [
            ["desired", "┄ 목표 경로 · 객체색"],
            ["actual", "━ 실제 궤적 · 객체색"],
            ["planned", "━ planner 출력"],
          ] as const
        ).map(([key, label]) => (
          <label key={key}>
            <input
              type="checkbox"
              aria-label={`City 3D ${key} paths`}
              checked={paths[key]}
              onChange={(event) =>
                setPaths((current) => ({
                  ...current,
                  [key]: event.target.checked,
                }))
              }
            />
            <span
              style={{
                color:
                  key === "planned" ? "#b9b9ff" : actorColor(props.selected),
              }}
            >
              {label.slice(0, 1)}
            </span>
            {label.slice(2)}
          </label>
        ))}
        <span>드래그: 회전 · 우클릭: 이동 · 휠: 확대</span>
      </div>
        </details>
      </section>
    </div>
  );
}
