import { Component, Suspense, useEffect, useMemo } from "react";
import { useGLTF } from "@react-three/drei";
import { Box3, Group, Mesh, MeshStandardMaterial, Vector3 } from "three";

export const CITY_CAR_VISUAL_URL = "/assets/kenney-car-kit/sedan.glb";

function Model({ length, width, height, color }: { length: number; width: number; height: number; color: string }) {
  const { scene } = useGLTF(CITY_CAR_VISUAL_URL, false, false);
  const { visual, geometries, materials } = useMemo(() => {
    const source = scene.clone(true), visual = new Group();
    source.rotation.y = Math.PI / 2; // Kenney sedan +Z front -> retained local +X front.
    visual.add(source); visual.updateMatrixWorld(true);
    const bounds = new Box3().setFromObject(visual), center = bounds.getCenter(new Vector3()), size = bounds.getSize(new Vector3());
    source.position.sub(center);
    visual.scale.set(length / size.x, height / size.y, width / size.z);
    const geometries: Mesh["geometry"][] = [], materials: MeshStandardMaterial[] = [];
    source.traverse((node) => {
      if (!(node instanceof Mesh)) return;
      node.castShadow = true; node.receiveShadow = true;
      const original = (Array.isArray(node.material) ? node.material[0] : node.material) as MeshStandardMaterial;
      const retained = original.clone(); retained.roughness = .52; retained.metalness = .12;
      materials.push(retained); node.material = retained;
      if (node.name !== "body") return;
      const paint = new MeshStandardMaterial({ color, roughness: .38, metalness: .2 });
      materials.push(paint);
      const geometry = node.geometry.clone(), uv = geometry.getAttribute("uv"), index = geometry.index;
      geometries.push(geometry); geometry.clearGroups();
      // Only the body's red palette region becomes the actor color. Wheels, windows and lights retain their supplied texture.
      const count = index?.count ?? geometry.getAttribute("position").count;
      let start = 0, previous = -1;
      for (let triangle = 0; triangle < count; triangle += 3) {
        const vertices = [0, 1, 2].map((offset) => index ? index.getX(triangle + offset) : triangle + offset);
        const isPaint = vertices.every((vertex) => Math.abs(uv.getX(vertex) - .84375) < .001 && uv.getY(vertex) >= .25 && uv.getY(vertex) < .5);
        const material = isPaint ? 1 : 0;
        if (previous !== -1 && previous !== material) { geometry.addGroup(start, triangle - start, previous); start = triangle; }
        previous = material;
      }
      geometry.addGroup(start, count - start, previous);
      node.geometry = geometry; node.material = [retained, paint];
    });
    return { visual, geometries, materials };
  }, [scene, length, width, height, color]);
  useEffect(() => () => { geometries.forEach((geometry) => geometry.dispose()); materials.forEach((material) => material.dispose()); }, [geometries, materials]);
  return <primitive object={visual} dispose={null} />;
}

function Fallback({ length, width, height, color }: { length: number; width: number; height: number; color: string }) {
  return <mesh castShadow><boxGeometry args={[length, height, width]} /><meshStandardMaterial color={color} roughness={.6} /></mesh>;
}
class VisualBoundary extends Component<{ children: React.ReactNode; fallback: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

/** Visual skin only. Actor pose, center and authoritative dimensions are supplied by native evidence. */
export default function CityCarVisual(props: { length: number; width: number; height: number; color: string }) {
  const fallback = <Fallback {...props} />;
  return <VisualBoundary fallback={fallback}><Suspense fallback={fallback}><Model {...props} /></Suspense></VisualBoundary>;
}
