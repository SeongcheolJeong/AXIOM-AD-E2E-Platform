import { useEffect, useMemo, useRef } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Html, Line, OrbitControls, OrthographicCamera, PerspectiveCamera } from "@react-three/drei";
import { MOUSE, TOUCH, OrthographicCamera as ThreeOrthographicCamera, Vector3 } from "three";
import { actorCenter, actorColor, perspectiveFitDistance, SCENE_CAMERA_DIRECTION, sceneBounds, type NativeActorState, type ObjectScene, type SceneActor } from "./object-scene";

type Props = { scene: ObjectScene; index: number; mode: "2d" | "3d"; selected: string; hidden: Set<string>; trails: boolean; labels: boolean; boxes: boolean; follow: boolean; fit: { count: number; map: boolean; focus: boolean }; zoom: { count: number; direction: number }; select: (id: string) => void };
function Vehicle({ state, actor, selected, mode, boxes, select }: { state: NativeActorState; actor: SceneActor; selected: boolean; mode: "2d" | "3d"; boxes: boolean; select: () => void }) {
  const center = actorCenter(state), color = actorColor(actor.id, actor.role), height = actor.dimensions_m.height_m;
  const top = mode === "2d";
  return <group position={[center.x, state.z, -center.y]} rotation={[0, state.heading_rad, 0]} onClick={(event) => { event.stopPropagation(); select(); }}>
    {actor.kind === "pedestrian" ? <>
      <mesh position={[0, height * .45, 0]}><cylinderGeometry args={[state.width_m * .32, state.width_m * .4, height * .6, 12]} /><meshStandardMaterial color={color} /></mesh>
      <mesh position={[0, height * .85, 0]}><sphereGeometry args={[Math.min(state.width_m * .4, height * .14), 12, 12]} /><meshStandardMaterial color={top ? color : "#dbb594"} /></mesh>
      <mesh position={[state.length_m * .45, .06, 0]}><boxGeometry args={[state.length_m * .25, .08, .09]} /><meshBasicMaterial color="#ffffff" /></mesh>
    </> : <>
      <mesh position={[0, height * .27, 0]}><boxGeometry args={[state.length_m, height * .48, state.width_m]} /><meshStandardMaterial color={color} roughness={.65} /></mesh>
      <mesh position={[-state.length_m * .08, height * .7, 0]}><boxGeometry args={[state.length_m * .48, height * .42, state.width_m * .8]} /><meshStandardMaterial color={top ? color : "#243547"} roughness={.35} /></mesh>
      <mesh position={[state.length_m * .5 + .01, height * .34, 0]}><boxGeometry args={[.08, .16, state.width_m * .65]} /><meshBasicMaterial color="#fff5bc" /></mesh>
      {!top && [-1, 1].flatMap((side) => [-1, 1].map((end) => <mesh key={`${side}-${end}`} position={[end * state.length_m * .3, .22, side * state.width_m * .5]} rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[.25, .25, .15, 12]} /><meshStandardMaterial color="#17212c" /></mesh>))}
    </>}
    {(boxes || selected) && <Line points={[[-state.length_m / 2, .035, -state.width_m / 2], [state.length_m / 2, .035, -state.width_m / 2], [state.length_m / 2, .035, state.width_m / 2], [-state.length_m / 2, .035, state.width_m / 2], [-state.length_m / 2, .035, -state.width_m / 2]]} color={selected ? "#ffffff" : color} lineWidth={selected ? 2 : 1} />}
  </group>;
}
function SceneCamera({ mode, scene, selected, index, follow, fit, zoom }: Pick<Props, "mode" | "scene" | "selected" | "index" | "follow" | "fit" | "zoom">) {
  const { camera, size, invalidate } = useThree();
  const controls = useRef<React.ComponentRef<typeof OrbitControls>>(null);
  useEffect(() => {
    const state = scene.frames[index].actors.find((actor) => actor.id === selected)!, center = actorCenter(state);
    const bounds = fit.focus ? { xmin: center.x - 20, xmax: center.x + 20, ymin: center.y - 12, ymax: center.y + 12 } : sceneBounds(scene, fit.map), x = (bounds.xmin + bounds.xmax) / 2, y = (bounds.ymin + bounds.ymax) / 2, width = bounds.xmax - bounds.xmin, depth = bounds.ymax - bounds.ymin;
    const target = new Vector3(x, 0, -y);
    if (camera instanceof ThreeOrthographicCamera) { camera.position.set(x, 200, -y); camera.up.set(0, 0, -1); camera.zoom = Math.min(size.width / width, size.height / depth) * .85; }
    else {
      let low = 0, high = 0;
      for (const frame of scene.frames) for (const state of frame.actors) { low = Math.min(low, state.z); high = Math.max(high, state.z + scene.actors.find((actor) => actor.id === state.id)!.dimensions_m.height_m); }
      const distance = perspectiveFitDistance(bounds, size.width / size.height, 45, { min: low, max: high }), direction = SCENE_CAMERA_DIRECTION;
      camera.position.set(x + distance * direction.x, distance * direction.y, -y + distance * direction.z); camera.up.set(0, 1, 0);
    }
    camera.lookAt(target); camera.updateProjectionMatrix(); if (controls.current) { controls.current.target.copy(target); controls.current.update(); } invalidate();
  }, [camera, mode, scene, fit, size.width, size.height, invalidate]);
  useEffect(() => {
    if (!follow || !controls.current) return;
    const state = scene.frames[index].actors.find((actor) => actor.id === selected); if (!state) return;
    const center = actorCenter(state), target = new Vector3(center.x, state.z, -center.y), delta = target.clone().sub(controls.current.target);
    camera.position.add(delta); controls.current.target.copy(target); controls.current.update(); invalidate();
  }, [camera, follow, index, selected, scene, invalidate]);
  useEffect(() => {
    if (!zoom.count || !controls.current) return;
    const factor = zoom.direction > 0 ? 1.35 : 1 / 1.35;
    if (camera instanceof ThreeOrthographicCamera) { camera.zoom = Math.min(100, Math.max(.1, camera.zoom * factor)); camera.updateProjectionMatrix(); }
    else camera.position.copy(controls.current.target.clone().add(camera.position.clone().sub(controls.current.target).divideScalar(factor)));
    controls.current.update(); invalidate();
  }, [camera, zoom, invalidate]);
  return <OrbitControls ref={controls} makeDefault enableRotate={mode === "3d"} enableDamping={false} mouseButtons={{ LEFT: mode === "2d" ? MOUSE.PAN : MOUSE.ROTATE, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.PAN }} touches={{ ONE: mode === "2d" ? TOUCH.PAN : TOUCH.ROTATE, TWO: TOUCH.DOLLY_PAN }} minDistance={3} maxDistance={6000} maxPolarAngle={Math.PI / 2.05} screenSpacePanning />;
}
function Content(props: Props) {
  const { scene, index, mode, selected, hidden, labels, boxes, select } = props, road = scene.map, frame = scene.frames[index];
  const histories = useMemo(() => scene.actors.map((actor) => ({ actor, points: scene.frames.slice(0, index + 1).map((sample) => { const state = sample.actors.find((value) => value.id === actor.id)!; return [state.x, state.z + .035, -state.y] as [number, number, number]; }) })), [scene, index]);
  return <>
    <color attach="background" args={["#182530"]} /><ambientLight intensity={1.35} /><directionalLight position={[20, 80, 50]} intensity={2} />
    <mesh position={[road.length_m / 2, -.04, road.lane_width_m]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[road.length_m + 30, 40]} /><meshStandardMaterial color="#263c3b" roughness={1} /></mesh>
    <mesh position={[road.length_m / 2, -.01, road.lane_width_m]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[road.length_m, road.lane_width_m * 2]} /><meshStandardMaterial color="#3c4650" roughness={1} /></mesh>
    {[0, -road.lane_width_m, -road.lane_width_m * 2].map((y, index) => <Line key={y} points={[[0, .015, -y], [road.length_m, .015, -y]]} color={index === 1 ? "#b8c7ce" : "#e5e3d1"} lineWidth={index === 1 ? 1 : 1.5} dashed={index === 1} dashSize={3} gapSize={3} />)}
    {[0, road.length_m].map((x) => <Line key={x} points={[[x, .018, 0], [x, .018, road.lane_width_m * 2]]} color="#91a2aa" lineWidth={1} />)}
    {labels && road.lanes.map((lane) => <Html key={lane.id} position={[Math.min(road.length_m / 2, 40), .05, -lane.centerline[0].y]} center><span className="object-scene-lane-label">Lane {lane.id}</span></Html>)}
    {props.trails && histories.filter(({ actor }) => !hidden.has(actor.id)).map(({ actor, points }) => points.length > 1 && <Line key={actor.id} points={points} color={actorColor(actor.id, actor.role)} lineWidth={actor.id === selected ? 2.5 : 1.2} transparent opacity={actor.id === selected ? .9 : .45} />)}
    {frame.actors.filter((state) => !hidden.has(state.id)).map((state) => {
      const actor = scene.actors.find((value) => value.id === state.id)!;
      return <group key={state.id}>
        <Vehicle state={state} actor={actor} selected={selected === state.id} mode={mode} boxes={boxes} select={() => select(state.id)} />
        <mesh position={[state.x, state.z + .025, -state.y]} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[.09, .16, 16]} /><meshBasicMaterial color="#ffffff" /></mesh>
        {labels && <Html position={[state.x, state.z + actor.dimensions_m.height_m + .6, -state.y]} center><button className={`object-scene-actor-label ${selected === state.id ? "selected" : ""}`} style={{ borderColor: actorColor(actor.id, actor.role) }} onClick={() => select(state.id)}>{actor.id}<small>{actor.role} · {actor.kind}</small></button></Html>}
      </group>;
    })}
    <SceneCamera {...props} />
  </>;
}
export default function ObjectSceneCanvas(props: Props) {
  return <Canvas frameloop="demand" dpr={[1, 1.8]} gl={{ antialias: true, preserveDrawingBuffer: true }} role="img" aria-label={`Object simulation ${props.mode === "2d" ? "2D top down" : "3D perspective"} scene`}>
    {props.mode === "2d" ? <OrthographicCamera makeDefault position={[0, 200, 0]} near={.1} far={10000} /> : <PerspectiveCamera makeDefault position={[0, 80, 80]} fov={45} near={.1} far={10000} />}
    <Content {...props} />
  </Canvas>;
}
