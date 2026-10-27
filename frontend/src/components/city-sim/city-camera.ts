import type { CityScene, CityState } from "./city-scene.ts";
import { decisionRelations } from "./city-decisions.ts";
export type CityCameraMode = "follow" | "overhead" | "all";

/** Camera composition uses only the current/explicit decision frame; no vehicle poses are generated. */
export function decisionCameraStates(scene: CityScene, index: number, selected: string, focusIndex?: number): CityState[] {
  const frame = scene.frames[index], focus = scene.frames[focusIndex ?? index] ?? frame;
  const egoId = scene.actors.find((actor) => actor.role === "ego")?.id;
  const anchor = frame.actors.find((actor) => actor.id === selected) ?? frame.actors.find((actor) => actor.id === egoId) ?? frame.actors[0];
  const ids = new Set([anchor.id, ...(egoId ? [egoId] : [])]);
  let hasObservedTarget = false;
  for (const identity of [selected, egoId]) {
    const observed = focus.actors.find((actor) => actor.id === identity);
    if (observed) for (const relation of decisionRelations(observed, focus.actors)) { ids.add(relation.to.id); hasObservedTarget = true; }
  }
  if (!hasObservedTarget) {
    // Nearby retained actors provide shot context only; they never become decision links or planner targets.
    const stationary = (actor: CityState) => scene.actors.find((spec) => spec.id === actor.id)?.controller?.kind === "static" || actor.speed_mps <= .3;
    const nearby = frame.actors.filter((actor) => !ids.has(actor.id) && Math.hypot(actor.x - anchor.x, actor.y - anchor.y) <= 75);
    nearby.sort((a, b) => Number(stationary(b)) - Number(stationary(a)) || Math.hypot(a.x - anchor.x, a.y - anchor.y) - Math.hypot(b.x - anchor.x, b.y - anchor.y));
    for (const actor of nearby.slice(0, Math.max(0, 5 - ids.size))) ids.add(actor.id);
  }
  const candidates = frame.actors.filter((actor) => ids.has(actor.id) && Math.hypot(actor.x - anchor.x, actor.y - anchor.y) <= 75);
  candidates.sort((a, b) => a.id === anchor.id ? -1 : b.id === anchor.id ? 1 : Math.hypot(a.x - anchor.x, a.y - anchor.y) - Math.hypot(b.x - anchor.x, b.y - anchor.y));
  return candidates.slice(0, 5);
}

export function presentationCameraKey(keys: string[], sensor: "front" | "chase"): string | undefined {
  const candidates = keys.filter((key) => sensor === "front" ? /front|forward/i.test(key) : /chase|rear.follow|third.person|observer/i.test(key));
  return candidates[0]; // Never label another camera as the requested view when it was not captured.
}
