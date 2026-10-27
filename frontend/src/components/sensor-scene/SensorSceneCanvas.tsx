import { useEffect, useMemo, useRef } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Line, OrbitControls, OrthographicCamera, PerspectiveCamera } from "@react-three/drei";
import { BufferGeometry, Float32BufferAttribute, Matrix4, MOUSE, TOUCH, OrthographicCamera as ThreeOrthographicCamera, Vector3 } from "three";
import type { FeedbackEvidence, FeedbackPoint } from "../../pages/carla-feedback";
import { boxWorldCenter, laneBoundary, multiplyMatrices, nativeToRenderMatrix, nativeToRenderPoint, type NativeMatrix, type SensorLayers, type SensorScene } from "./sensor-scene";

type Props = { evidence: FeedbackEvidence; scene: SensorScene | null; index: number; mode: "2d" | "3d"; follow: boolean; fit: { count: number; map: boolean }; zoom: { count: number; direction: number }; layers: SensorLayers };
function renderMatrix(matrix: NativeMatrix) { return new Matrix4().set(...nativeToRenderMatrix(matrix).flat() as [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number]); }
function LaneSurfaces({ scene }: { scene: SensorScene }) {
  const geometry = useMemo(() => { const positions: number[] = []; for (const lane of scene.map.lanes) for (let i = 1; i < lane.points.length; i++) { const a = lane.points[i - 1], b = lane.points[i], corners = [laneBoundary(a, -1), laneBoundary(a, 1), laneBoundary(b, -1), laneBoundary(b, 1)].map(nativeToRenderPoint); for (const j of [0, 1, 2, 2, 1, 3]) positions.push(...corners[j]); } const geo = new BufferGeometry(); geo.setAttribute("position", new Float32BufferAttribute(positions, 3)); geo.computeVertexNormals(); return geo; }, [scene]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return <><mesh geometry={geometry}><meshStandardMaterial color="#414d58" side={2} polygonOffset polygonOffsetFactor={1} polygonOffsetUnits={1} /></mesh>{scene.map.lanes.filter(lane => lane.points.length > 1).flatMap(lane => ([-1, 1] as const).map(side => <Line key={`${lane.id}:${side}`} points={lane.points.map(p => { const point = laneBoundary(p, side); return nativeToRenderPoint({ ...point, z_m: point.z_m + .025 }); })} color="#c2cbd0" lineWidth={1} transparent opacity={.6} />))}</>;
}
function CameraFrusta({ scene, index }: { scene: SensorScene; index: number }) {
  return <>{scene.frames[index].cameras.map((camera, i) => { const intrinsics = scene.cameras[i].pinhole_intrinsics, distance = 8, corners = [[0, 0], [640, 0], [640, 360], [0, 360]].map(([u, v]) => nativeToRenderPoint({ x_m: distance, y_m: (u - intrinsics.cx) * distance / intrinsics.fx, z_m: -(v - intrinsics.cy) * distance / intrinsics.fy })), color = camera.key === "front" ? "#e2b760" : "#c994e6"; return <group key={camera.key} matrixAutoUpdate={false} matrix={renderMatrix(camera.sensor_world_transform.matrix)}><mesh><sphereGeometry args={[.14, 10, 10]} /><meshBasicMaterial color={color} /></mesh>{corners.map((corner, j) => <Line key={j} points={[[0, 0, 0], corner]} color={color} transparent opacity={.6} lineWidth={1} />)}<Line points={[...corners, corners[0]]} color={color} lineWidth={1.4} /></group>; })}</>;
}
function SceneCamera({ evidence, scene, index, mode, fit, zoom, follow }: Omit<Props, "layers">) {
  const { camera, size, invalidate } = useThree(), controls = useRef<React.ComponentRef<typeof OrbitControls>>(null);
  useEffect(() => { const points: FeedbackPoint[] = fit.map && scene ? scene.map.lanes.flatMap(l => l.points.flatMap(p => [laneBoundary(p, -1), laneBoundary(p, 1)])) : [evidence.ticks[0].input_ego, ...evidence.ticks.map(t => t.ego), ...evidence.ticks.flatMap(t => t.planned_path)]; const minX = Math.min(...points.map(p => p.x_m)), maxX = Math.max(...points.map(p => p.x_m)), minY = Math.min(...points.map(p => p.y_m)), maxY = Math.max(...points.map(p => p.y_m)), minZ = Math.min(...points.map(p => p.z_m)), width = Math.max(20, maxX - minX + 14), depth = Math.max(20, maxY - minY + 14), target = new Vector3((minX + maxX) / 2, minZ, (minY + maxY) / 2);
    if (camera instanceof ThreeOrthographicCamera) { camera.position.set(target.x, target.y + 500, target.z); camera.up.set(0, 0, -1); camera.zoom = Math.min(size.width / width, size.height / depth) * .9; } else { const distance = Math.max(width / Math.max(.1, size.width / size.height), depth) * 1.45; camera.position.set(target.x + distance * .4, target.y + distance * .9, target.z + distance * .7); camera.up.set(0, 1, 0); }
    camera.lookAt(target); camera.updateProjectionMatrix(); controls.current?.target.copy(target); controls.current?.update(); invalidate();
  }, [camera, mode, scene, evidence, fit, size.width, size.height, invalidate]);
  useEffect(() => { if (!follow || !controls.current) return; const state = evidence.ticks[index].ego, target = new Vector3(state.x_m, state.z_m, state.y_m), delta = target.clone().sub(controls.current.target); camera.position.add(delta); controls.current.target.copy(target); controls.current.update(); invalidate(); }, [camera, follow, index, evidence, invalidate]);
  useEffect(() => { if (!zoom.count || !controls.current) return; const factor = zoom.direction > 0 ? 1.35 : 1 / 1.35; if (camera instanceof ThreeOrthographicCamera) { camera.zoom = Math.max(.1, Math.min(100, camera.zoom * factor)); camera.updateProjectionMatrix(); } else camera.position.copy(controls.current.target.clone().add(camera.position.clone().sub(controls.current.target).divideScalar(factor))); controls.current.update(); invalidate(); }, [camera, zoom, invalidate]);
  return <OrbitControls ref={controls} makeDefault enableRotate={mode === "3d"} enableDamping={false} mouseButtons={{ LEFT: mode === "2d" ? MOUSE.PAN : MOUSE.ROTATE, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.PAN }} touches={{ ONE: mode === "2d" ? TOUCH.PAN : TOUCH.ROTATE, TWO: TOUCH.DOLLY_PAN }} minDistance={3} maxDistance={5000} maxPolarAngle={Math.PI / 2.02} screenSpacePanning />;
}
function Content(props: Props) {
  const { evidence, scene, index, layers, mode } = props, tick = evidence.ticks[index], actual = [evidence.ticks[0].input_ego, ...evidence.ticks.slice(0, index + 1).map(t => t.ego)];
  const bboxMatrix = scene ? renderMatrix(multiplyMatrices(scene.frames[index].ego_world_transform.matrix, scene.ego.localBox.matrix)) : null;
  const center = scene ? boxWorldCenter(scene.frames[index], scene) : tick.ego;
  return <><color attach="background" args={["#172633"]} /><ambientLight intensity={1.5} /><directionalLight position={[50, 100, 50]} intensity={2} />
    {scene && layers.map && <LaneSurfaces scene={scene} />}
    {layers.plan && <Line points={tick.planned_path.map(p => nativeToRenderPoint({ ...p, z_m: p.z_m + .07 }))} color="#82b4ff" lineWidth={2.5} dashed dashSize={1} gapSize={.7} />}
    {layers.history && actual.length > 1 && <Line points={actual.map(p => nativeToRenderPoint({ ...p, z_m: p.z_m + .1 }))} color="#72dca0" lineWidth={3} />}
    <mesh position={nativeToRenderPoint({ ...tick.input_ego, z_m: tick.input_ego.z_m + .1 })} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[.25, .45, 20]} /><meshBasicMaterial color="#e2b760" /></mesh>
    {scene && bboxMatrix && layers.boxes ? <group matrixAutoUpdate={false} matrix={bboxMatrix}><mesh><boxGeometry args={[scene.ego.extent[0] * 2, scene.ego.extent[2] * 2, scene.ego.extent[1] * 2]} /><meshStandardMaterial color="#58b58d" transparent opacity={mode === "2d" ? .95 : .75} /></mesh><mesh position={[scene.ego.extent[0] + .02, 0, 0]}><boxGeometry args={[.06, scene.ego.extent[2] * 1.2, scene.ego.extent[1] * 1.3]} /><meshBasicMaterial color="#e7fff5" /></mesh></group> : <group position={nativeToRenderPoint({ ...tick.ego, z_m: tick.ego.z_m + .2 })} rotation={[0, -tick.ego.yaw_rad, 0]}><mesh rotation={[-Math.PI / 2, 0, -Math.PI / 2]}><coneGeometry args={[.55, 1.5, 3]} /><meshBasicMaterial color="#72dca0" /></mesh></group>}
    {scene && layers.cameras && <CameraFrusta scene={scene} index={index} />}
    <mesh position={nativeToRenderPoint({ ...center, z_m: center.z_m + (scene ? scene.ego.extent[2] : .4) + .05 })} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[.08, .15, 16]} /><meshBasicMaterial color="#ffffff" /></mesh>
    <SceneCamera {...props} />
  </>;
}
export default function SensorSceneCanvas(props: Props) { return <Canvas frameloop="demand" dpr={[1, 1.8]} gl={{ antialias: true, preserveDrawingBuffer: true }} role="img" aria-label={`CARLA sensor scene ${props.mode === "2d" ? "2D top down" : "3D perspective"}`}>
  {props.mode === "2d" ? <OrthographicCamera makeDefault position={[0, 500, 0]} near={.1} far={10000} /> : <PerspectiveCamera makeDefault position={[0, 100, 100]} near={.1} far={10000} fov={45} />}<Content {...props} />
</Canvas>; }
