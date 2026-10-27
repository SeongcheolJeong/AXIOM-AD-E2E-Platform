import type {
  BehaviorAction,
  CityPose,
  CityRecord,
  CityScenario,
} from "../city-sim/city-contract.ts";
import type {
  DemoPreset,
  DemoPreview,
  DemoRunAttempt,
  DemoApi,
} from "./demo-contract.ts";

export const betaTemplateIds = [
  "lead_stop_start",
  "cut_in",
  "urban_left_turn",
  "urban_right_turn",
];
export type BetaActorEdit = {
  pose?: CityPose;
  destination?: CityPose;
  speed_mps?: number;
  actions?: BehaviorAction[];
};
export type BetaDraft = {
  templateId: string;
  name: string;
  parameters: Record<string, number | boolean>;
  actorEdits: Record<string, BetaActorEdit>;
  bindingRevision: number | null;
  source: NonNullable<DemoPreset["scenario_ref"]>;
  roles?: Record<string, string>;
};
export type BetaPreview = DemoPreview & {
  scenario_ref: NonNullable<DemoPreset["scenario_ref"]>;
  source_scenario_ref: NonNullable<DemoPreset["scenario_ref"]>;
  actor_roles: DemoPreview["actor_roles"] & {
    intruder?: string;
    crossing?: string;
  };
  editable?: {
    actors: {
      id: string;
      fields?: string[];
      route_editable?: boolean;
      speed_limits?: { min: number; max: number } | null;
      actions?: {
        max_count: number;
        kinds: string[];
        trigger_kinds: string[];
        requires_target_lane: string[];
        duration_s: { min: number; max: number };
      };
    }[];
    capabilities?: string[];
    scope?: string;
  };
  validation?: { valid: boolean; native_spawn_status: string };
};
export type BetaIssue = {
  actor_id?: string | null;
  field: string;
  code?: string;
  message: string;
};
export type BetaSession = {
  version: 1;
  project: string;
  identity: string;
  draft: BetaDraft | null;
  attempt: BetaAttempt | null;
  executed?: { id: string; saved: CityRecord; signature: string } | null;
  baselineId: string | null;
};
export type BetaAttempt = DemoRunAttempt & {
  revisionBase?: { id: string; revision: number };
  submissionPending?: boolean;
};

export function betaUnresolvedSubmission(attempt: BetaAttempt | null): boolean {
  return (
    !!attempt?.experiment &&
    !attempt.execution &&
    attempt.submissionPending !== false
  );
}
/** These first-submit rejections occur before any job insertion. Recovery errors
 * never prove that an earlier ambiguous request was not accepted. */
export function betaSubmissionDefinitelyRejected(
  status: number | undefined,
  recovering: boolean,
): boolean {
  return !recovering && (status === 422 || status === 429);
}
export function betaPreviewMatchesBinding(
  draft: BetaDraft,
  preview: BetaPreview,
  project?: string,
): boolean {
  const same = (ref: BetaPreview["scenario_ref"] | undefined) =>
    !!ref &&
    ref.id === draft.source.id &&
    ref.revision === draft.source.revision &&
    ref.configuration_hash === draft.source.configuration_hash &&
    ref.manifest_hash === draft.source.manifest_hash;
  return (
    preview.preset_id === draft.templateId &&
    (project === undefined || preview.project_id === project) &&
    preview.binding_revision === draft.bindingRevision &&
    same(preview.scenario_ref) &&
    same(preview.source_scenario_ref)
  );
}

export function nextBetaAttempt(
  previous: BetaAttempt | null,
  signature: string,
  key: string,
  lastSaved?: CityRecord,
): BetaAttempt {
  if (betaUnresolvedSubmission(previous)) return previous!;
  if (previous?.signature === signature && !previous.execution) return previous;
  if (previous?.signature === signature && previous.saved)
    return { key, signature, saved: previous.saved, submissionPending: false };
  const base = previous?.saved ?? lastSaved;
  return {
    key,
    signature,
    submissionPending: false,
    ...(base ? { revisionBase: { id: base.id, revision: base.revision } } : {}),
  };
}
export async function saveBetaScenario(
  request: DemoApi,
  project: string,
  scenario: CityScenario,
  attempt: BetaAttempt,
): Promise<CityRecord> {
  if (attempt.saved) return attempt.saved;
  const base = attempt.revisionBase;
  attempt.saved = await request<CityRecord>(
    base
      ? `/scenario-v2/${encodeURIComponent(base.id)}/revisions`
      : "/scenario-v2",
    {
      method: "POST",
      headers: { "Idempotency-Key": `${attempt.key}-scenario` },
      body: JSON.stringify(
        base
          ? { expected_revision: base.revision, scenario }
          : { project_id: project, scenario },
      ),
    },
  );
  return attempt.saved;
}

export function betaStorageKey(project: string, identity: string) {
  return `axiom.object-beta.v1:${encodeURIComponent(project)}:${encodeURIComponent(identity)}`;
}
export function restoreBetaSession(
  raw: string | null,
  project: string,
  identity: string,
): BetaSession | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as BetaSession;
    if (
      value.version !== 1 ||
      value.project !== project ||
      value.identity !== identity ||
      !Object.hasOwn(value, "draft")
    )
      return null;
    if (
      value.draft &&
      (!betaTemplateIds.includes(value.draft.templateId) ||
        !value.draft.source?.configuration_hash ||
        typeof value.draft.name !== "string" ||
        !value.draft.parameters ||
        !value.draft.actorEdits)
    )
      return null;
    if (
      value.attempt &&
      (typeof value.attempt.key !== "string" ||
        typeof value.attempt.signature !== "string")
    )
      return null;
    if (
      value.executed &&
      (typeof value.executed.id !== "string" ||
        !value.executed.saved?.scenario ||
        !value.executed.saved.configuration_hash)
    )
      return null;
    return value;
  } catch {
    return null;
  }
}
export function betaSourceMatches(
  draft: BetaDraft,
  preset: DemoPreset,
): boolean {
  return (
    draft.bindingRevision === preset.binding_revision &&
    draft.source.id === preset.scenario_ref?.id &&
    draft.source.revision === preset.scenario_ref.revision &&
    draft.source.configuration_hash ===
      preset.scenario_ref.configuration_hash &&
    draft.source.manifest_hash === preset.scenario_ref.manifest_hash
  );
}
export function betaDraftFromPreset(preset: DemoPreset): BetaDraft {
  if (!preset.ready || !preset.scenario_ref)
    throw new Error("이 템플릿의 지도와 시나리오를 먼저 연결해 주세요.");
  return {
    templateId: preset.id,
    name: `${preset.title} · 내 시나리오`,
    parameters: { ...preset.defaults },
    actorEdits: {},
    bindingRevision: preset.binding_revision,
    source: { ...preset.scenario_ref },
  };
}
export function betaIssues(message: string): BetaIssue[] {
  try {
    const body = JSON.parse(message);
    if (Array.isArray(body.issues))
      return body.issues.map((issue: BetaIssue) => ({
        actor_id: issue.actor_id,
        field: issue.field || "scenario",
        message: issue.message || message,
        code: issue.code,
      }));
  } catch {
    /* Ordinary API/network errors remain visible. */
  }
  return [{ field: "scenario", message }];
}
export function betaPreviewBody(project: string, draft: BetaDraft) {
  const parameters = { ...draft.parameters };
  for (const [role, actorId] of Object.entries(draft.roles ?? {})) {
    if (draft.actorEdits[actorId]?.speed_mps !== undefined)
      delete parameters[`${role}_speed_mps`];
  }
  return {
    project_id: project,
    source_scenario_ref: draft.source,
    parameters,
    actor_edits: draft.actorEdits,
  };
}
export function betaChangeParameter(
  draft: BetaDraft,
  key: string,
  value: number | boolean,
): BetaDraft {
  const edits = structuredClone(draft.actorEdits);
  const role = key === "cut_in_start_s" ? "intruder" : key.split("_")[0];
  const actorId = draft.roles?.[role];
  if (actorId && edits[actorId]) {
    if (key.endsWith("_speed_mps")) delete edits[actorId].speed_mps;
    if (
      key === "lead_wait_s" ||
      key === "lead_start_s" ||
      key === "cut_in_start_s"
    )
      delete edits[actorId].actions;
  }
  return {
    ...draft,
    parameters: { ...draft.parameters, [key]: value },
    actorEdits: edits,
  };
}
