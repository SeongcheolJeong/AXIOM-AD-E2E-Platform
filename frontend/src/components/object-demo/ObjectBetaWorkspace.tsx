import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  Copy,
  GitCompareArrows,
  LoaderCircle,
  Play,
  Save,
} from "lucide-react";
import { ApiError, api, useResource } from "../../api";
import type { BehaviorAction } from "../city-sim/city-contract";
import type { ObjectExecution } from "../object-scene/object-scene";
import CitySceneViewer from "../city-sim/CitySceneViewer";
import DemoMapCanvas from "./DemoMapCanvas";
import ObjectBetaBehavior from "./ObjectBetaBehavior";
import {
  demoParameterUnit,
  demoStatus,
  demoTerminal,
  executeDemo,
} from "./demo-contract";
import type { DemoPreset } from "./demo-contract";
import {
  betaChangeParameter,
  betaDraftFromPreset,
  betaIssues,
  betaPreviewBody,
  betaSourceMatches,
  betaStorageKey,
  betaTemplateIds,
  restoreBetaSession,
  nextBetaAttempt,
  saveBetaScenario,
  betaUnresolvedSubmission,
  betaPreviewMatchesBinding,
  betaSubmissionDefinitelyRejected,
} from "./beta-contract";
import type {
  BetaActorEdit,
  BetaDraft,
  BetaIssue,
  BetaPreview,
  BetaSession,
  BetaAttempt,
} from "./beta-contract";
import "./object-demo.css";
import "./object-beta.css";

type Identity = {
  principal_id: string;
  role: string;
  mode: string;
  project_id?: string | null;
};
type Execution = ObjectExecution & {
  error?: string | null;
  outcome_uncertain?: boolean;
  cancellation_supported?: boolean;
  cancel_requested_at?: string | null;
};
type Executed = NonNullable<BetaSession["executed"]>;
type Observation = { id: string; title: string; time_s: number; kind: string };
const behaviorNames: Record<string, string> = {
  stop: "정지",
  wait: "대기",
  resume: "재출발",
  follow_route: "경로 추종",
  set_speed: "속도 변경",
  cut_in: "끼어들기",
  lane_change: "차로 변경",
  yield: "양보",
  turn_left: "좌회전",
  turn_right: "우회전",
};
const templateMarks: Record<string, string> = {
  lead_stop_start: "01 · STOP & RESUME",
  cut_in: "02 · CUT IN",
  urban_left_turn: "03 · LEFT TURN",
  urban_right_turn: "04 · RIGHT TURN",
};

export default function ObjectBetaWorkspace({
  project,
  onAdvanced,
  onCompare,
  notify,
}: {
  project: string;
  onAdvanced: () => void;
  onCompare: (left: string, right: string) => void;
  notify?: (message: string) => void;
}) {
  const presets = useResource<{ items: DemoPreset[] }>(
    `/object-sim/presets?project_id=${encodeURIComponent(project)}`,
  );
  const identity = useResource<Identity>("/team/whoami");
  const capabilities = useResource<{
    handlers: {
      id: string;
      version: string;
      status: string;
      description?: string;
    }[];
  }>("/experiments/capabilities");
  const queue = useResource<{
    enabled: boolean;
    active_count: number;
    queued_count: number;
    active_limit: number;
    queue_limit: number;
    can_submit: boolean;
  }>(`/projects/${encodeURIComponent(project)}/object-beta-policy`);
  const [draft, setDraft] = useState<BetaDraft | null>(null),
    [preview, setPreview] = useState<BetaPreview | null>(null);
  const [previewKey, setPreviewKey] = useState(""),
    [previewing, setPreviewing] = useState(false),
    [issues, setIssues] = useState<BetaIssue[]>([]);
  const [actorId, setActorId] = useState("ego"),
    [mapMode, setMapMode] = useState<"start" | "destination">("start");
  const [baselineId, setBaselineId] = useState<string | null>(null),
    [executed, setExecuted] = useState<Executed | null>(null);
  const [execution, setExecution] = useState<Execution | null>(null),
    [shownId, setShownId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [stage, setStage] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [storageKey, setStorageKey] = useState<string | null>(null),
    [attemptVersion, setAttemptVersion] = useState(0),
    [pollVersion, setPollVersion] = useState(0);
  const [observation, setObservation] = useState({
    title: "",
    note: "",
    time_s: 0,
    kind: "observation",
    behavior_exercised: "uncertain",
    ego_outcome: "uncertain",
  });
  const [observationBusy, setObservationBusy] = useState(false),
    [observationError, setObservationError] = useState("");
  const [bindingMismatch, setBindingMismatch] = useState(false);
  const attempt = useRef<BetaAttempt | null>(null),
    submitting = useRef(false),
    observationAttempt = useRef<{ signature: string; key: string } | null>(
      null,
    );
  const displayed = useResource<Execution>(
    shownId ? `/experiments/executions/${encodeURIComponent(shownId)}` : null,
  );
  const observations = useResource<{ items: Observation[] }>(
    executed
      ? `/object-sim/observations?project_id=${encodeURIComponent(project)}&execution_id=${encodeURIComponent(executed.id)}`
      : null,
  );
  const principal = identity.data?.principal_id;
  const writable =
    !!identity.data &&
    (identity.data.mode !== "project" ||
      ["Engineer", "Admin"].includes(identity.data.role));
  const native = capabilities.data?.handlers.find(
    (item) => item.id === "carla-city-object",
  );
  const template = presets.data?.items.find(
    (item) => item.id === draft?.templateId,
  );
  const sourceChanged =
    bindingMismatch ||
    (!!draft && !!template && !betaSourceMatches(draft, template));
  const draftKey = JSON.stringify(draft);
  const scenario =
    preview && draft ? { ...preview.scenario, name: draft.name.trim() } : null;
  const signature = scenario
    ? JSON.stringify({
        project,
        scenario,
        adapter: native?.id ?? "carla-city-object",
        version: native?.version ?? null,
      })
    : "";
  const currentPreview =
    !!preview &&
    previewKey === draftKey &&
    !previewing &&
    !issues.length &&
    !sourceChanged &&
    !!template?.ready;
  const currentSave =
    !!signature &&
    attempt.current?.signature === signature &&
    !!attempt.current.saved;
  const activeRun =
    !!executed && (!execution || !demoTerminal(execution.status));
  const observationAvailable =
    !!execution &&
    ["completed", "failed", "cancelled", "interrupted"].includes(
      execution.status,
    );
  const unresolvedSubmission = betaUnresolvedSubmission(attempt.current);
  const locked = busy || activeRun || unresolvedSubmission;
  const actorBase = preview?.scenario.actors.find(
    (item) => item.id === actorId,
  );
  const actorEdit = draft?.actorEdits[actorId];
  const actor = actorBase
    ? {
        ...actorBase,
        ...(actorEdit?.speed_mps !== undefined
          ? { speed_mps: actorEdit.speed_mps }
          : {}),
        ...(actorEdit?.actions
          ? { behavior: { ...actorBase.behavior!, actions: actorEdit.actions } }
          : {}),
      }
    : undefined;
  const actorMetadata = preview?.editable?.actors.find(
    (item) => item.id === actorId,
  );
  const map = preview
    ? {
        ...preview.map.definition,
        lanes: preview.map.lanes ?? preview.map.definition.lanes,
      }
    : null;

  useEffect(() => {
    if (!principal) return;
    const key = betaStorageKey(project, principal);
    let saved: BetaSession | null = null;
    try {
      saved = restoreBetaSession(localStorage.getItem(key), project, principal);
    } catch {
      setNotice(
        "브라우저 저장소를 사용할 수 없습니다. 이 탭을 닫기 전에 시나리오를 저장해 주세요.",
      );
    }
    if (saved) {
      setDraft(saved.draft);
      attempt.current = saved.attempt;
      setExecuted(saved.executed ?? null);
      setBaselineId(saved.baselineId);
      if (!saved.executed && saved.attempt?.execution && saved.attempt.saved)
        setExecuted({
          id: saved.attempt.execution.id,
          saved: saved.attempt.saved,
          signature: saved.attempt.signature,
        });
      setNotice("이 계정과 프로젝트의 작성안과 실행 상태를 복원했습니다.");
    } else {
      setDraft(null);
      attempt.current = null;
      setExecuted(null);
      setBaselineId(null);
    }
    setExecution(null);
    setShownId(null);
    setStorageKey(key);
  }, [project, principal]);

  function persist(overrides: Partial<BetaSession> = {}) {
    if (!storageKey || !principal) return;
    try {
      localStorage.setItem(
        storageKey,
        JSON.stringify({
          version: 1,
          project,
          identity: principal,
          draft,
          attempt: attempt.current,
          executed,
          baselineId,
          ...overrides,
        } satisfies BetaSession),
      );
    } catch {
      setNotice(
        "브라우저 저장 공간이 부족합니다. 저장한 시나리오 개정과 실행 결과는 서버에 남습니다.",
      );
    }
  }
  useEffect(() => {
    persist();
  }, [storageKey, draftKey, executed, baselineId, attemptVersion]);

  useEffect(() => {
    if (!draft || !template?.ready || sourceChanged) {
      setPreviewing(false);
      return;
    }
    let active = true;
    const controller = new AbortController();
    setPreviewing(true);
    setIssues([]);
    const timer = setTimeout(() => {
      api<BetaPreview>(
        `/object-sim/presets/${encodeURIComponent(draft.templateId)}/preview`,
        {
          method: "POST",
          body: JSON.stringify(betaPreviewBody(project, draft)),
          signal: controller.signal,
        },
      )
        .then((value) => {
          if (active) {
            if (!betaPreviewMatchesBinding(draft, value, project)) {
              setPreviewKey("");
              setBindingMismatch(true);
              setIssues([
                {
                  field: "template",
                  message:
                    "템플릿 연결이 변경되었습니다. 최신 템플릿으로 다시 복제한 뒤 작성하세요.",
                },
              ]);
              presets.reload();
              return;
            }
            setPreview(value);
            setPreviewKey(draftKey);
            if (!draft.roles) setDraft({ ...draft, roles: value.actor_roles });
            setActorId((id) =>
              value.scenario.actors.some((item) => item.id === id)
                ? id
                : value.actor_roles.ego,
            );
          }
        })
        .catch((reason: unknown) => {
          if (active) {
            setIssues(
              betaIssues(
                reason instanceof Error ? reason.message : String(reason),
              ),
            );
            setPreviewKey("");
          }
        })
        .finally(() => {
          if (active) setPreviewing(false);
        });
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [project, draftKey, template?.ready, sourceChanged]);

  useEffect(() => {
    if (!executed) return;
    let active = true,
      timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function poll() {
      try {
        const value = await api<Execution>(
          `/experiments/executions/${encodeURIComponent(executed!.id)}`,
          { signal: controller.signal },
        );
        if (!active) return;
        setExecution(value);
        if (value.status === "completed" && value.manifest_hash)
          setBaselineId((current) => current ?? value.id);
        if (!demoTerminal(value.status))
          timer = setTimeout(() => void poll(), 1800);
        else queue.reload();
      } catch (reason) {
        if (active)
          setError(
            `실행 상태를 확인할 수 없습니다. 상태 다시 확인을 눌러 주세요. ${reason instanceof Error ? reason.message : String(reason)}`,
          );
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [executed?.id, pollVersion]);

  function choose(item: DemoPreset) {
    if (locked || !item.ready || !writable) return;
    setDraft(betaDraftFromPreset(item));
    setBindingMismatch(false);
    setPreview(null);
    setPreviewKey("");
    setIssues([]);
    setActorId("ego");
    setMapMode("start");
    setBaselineId(item.saved_execution_id);
    setExecuted(null);
    setExecution(null);
    setShownId(null);
    setError("");
    setNotice(
      "템플릿을 내 작성안으로 복제했습니다. 이름과 조건을 수정한 뒤 개정을 저장하세요.",
    );
    attempt.current = null;
    setAttemptVersion((value) => value + 1);
  }
  function update(next: BetaDraft) {
    if (!locked && writable) {
      setDraft(next);
      setError("");
    }
  }
  function editActor(id: string, edit: BetaActorEdit) {
    const fields = preview?.editable?.actors.find(
      (item) => item.id === id,
    )?.fields;
    if (fields && Object.keys(edit).some((field) => !fields.includes(field))) {
      setError(`${id}: 이 객체의 제어기는 선택한 편집을 지원하지 않습니다.`);
      return;
    }
    if (draft)
      update({
        ...draft,
        actorEdits: {
          ...draft.actorEdits,
          [id]: { ...draft.actorEdits[id], ...edit },
        },
      });
  }
  function actionChange(index: number, change: Partial<BehaviorAction>) {
    if (!actor) return;
    const actions = structuredClone(actor.behavior?.actions ?? []);
    actions[index] = { ...actions[index], ...change };
    editActor(actor.id, { actions });
  }
  function ensureAttempt() {
    attempt.current = nextBetaAttempt(
      attempt.current,
      signature,
      `object-beta-${crypto.randomUUID()}`,
      executed?.saved,
    );
    persist();
    return attempt.current;
  }
  async function save() {
    if (
      submitting.current ||
      !scenario ||
      !currentPreview ||
      !scenario.name ||
      !writable ||
      locked
    )
      return;
    submitting.current = true;
    setBusy(true);
    setError("");
    const current = ensureAttempt();
    try {
      await saveBetaScenario(api, project, scenario, current);
      setNotice(
        `개정 r${current.saved!.revision}을 저장했습니다. 실행은 저장된 이 개정을 사용합니다.`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      persist();
      setAttemptVersion((value) => value + 1);
      submitting.current = false;
      setBusy(false);
    }
  }
  async function run(recoverSubmission = false) {
    if (submitting.current || !writable || busy || activeRun) return;
    if (
      !recoverSubmission &&
      (!scenario ||
        !currentPreview ||
        !scenario.name ||
        !native ||
        native.status !== "available" ||
        locked)
    )
      return;
    if (
      recoverSubmission &&
      (!unresolvedSubmission ||
        !attempt.current?.saved ||
        !attempt.current.prepared ||
        !attempt.current.experiment)
    )
      return;
    submitting.current = true;
    setBusy(true);
    setError("");
    const current = recoverSubmission ? attempt.current! : ensureAttempt();
    try {
      const pinned = recoverSubmission
        ? (JSON.parse(current.signature) as {
            project: string;
            adapter: string;
            version: string;
          })
        : null;
      if (
        pinned &&
        (pinned.project !== project ||
          current.saved?.project_id !== project ||
          !pinned.adapter ||
          !pinned.version)
      )
        throw new Error(
          "저장된 제출의 프로젝트와 실행 환경 정보를 확인할 수 없습니다.",
        );
      const frozenScenario = recoverSubmission
        ? current.saved!.scenario
        : scenario!;
      const frozenAdapter = pinned
        ? { id: pinned.adapter, version: pinned.version }
        : native!;
      setStage("시나리오 개정 저장");
      await saveBetaScenario(api, project, frozenScenario, current);
      persist();
      const result = await executeDemo(
        api,
        project,
        structuredClone(frozenScenario),
        frozenAdapter,
        current,
        (value) => {
          if (value === "실행 큐에 제출") current.submissionPending = true;
          setStage(value);
          persist();
        },
      );
      current.submissionPending = false;
      const value = {
        id: result.id,
        saved: current.saved!,
        signature: current.signature,
      };
      setExecuted(value);
      setExecution(null);
      setShownId(null);
      persist({ executed: value });
      queue.reload();
      setNotice("저장 개정을 실제 CARLA 실행 큐에 제출했습니다.");
      notify?.("Object Sim 실행을 제출했습니다.");
    } catch (reason) {
      if (
        betaSubmissionDefinitelyRejected(
          reason instanceof ApiError ? reason.status : undefined,
          recoverSubmission,
        )
      )
        current.submissionPending = false;
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      persist({
        ...(current.execution && current.saved
          ? {
              executed: {
                id: current.execution.id,
                saved: current.saved,
                signature: current.signature,
              },
            }
          : {}),
      });
      setAttemptVersion((value) => value + 1);
      submitting.current = false;
      setBusy(false);
      setStage("");
    }
  }
  async function cancel() {
    if (!executed || busy) return;
    setBusy(true);
    setError("");
    try {
      setExecution(
        await api<Execution>(
          `/experiments/executions/${encodeURIComponent(executed.id)}/cancel`,
          { method: "POST" },
        ),
      );
      setPollVersion((value) => value + 1);
      queue.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }
  async function saveObservation(event: React.FormEvent) {
    event.preventDefault();
    if (
      !executed ||
      !observationAvailable ||
      !observation.title.trim() ||
      !observation.note.trim() ||
      observationBusy
    )
      return;
    const saved = executed.saved;
    const body = {
      ...observation,
      title: observation.title.trim(),
      project_id: project,
      execution_id: executed.id,
      scenario_ref: {
        id: saved.id,
        revision: saved.revision,
        configuration_hash: saved.configuration_hash,
        manifest_hash: saved.manifest_hash,
      },
    };
    const bodyKey = JSON.stringify(body);
    if (observationAttempt.current?.signature !== bodyKey)
      observationAttempt.current = {
        signature: bodyKey,
        key: `object-observation-${crypto.randomUUID()}`,
      };
    setObservationBusy(true);
    setObservationError("");
    try {
      await api("/object-sim/observations", {
        method: "POST",
        headers: { "Idempotency-Key": observationAttempt.current.key },
        body: bodyKey,
      });
      setObservation({ ...observation, title: "", note: "" });
      observationAttempt.current = null;
      observations.reload();
      setNotice("관찰을 실행 개정·선택 시점에 연결해 저장했습니다.");
    } catch (reason) {
      setObservationError(
        reason instanceof Error ? reason.message : String(reason),
      );
    } finally {
      setObservationBusy(false);
    }
  }

  const canRun =
    !!scenario?.name &&
    currentPreview &&
    !locked &&
    writable &&
    !!storageKey &&
    native?.status === "available" &&
    !(queue.data?.enabled && !queue.data.can_submit);
  const canCompare =
    !!baselineId &&
    !!executed &&
    baselineId !== executed.id &&
    !!execution?.manifest_hash;
  const templates = presets.data?.items
    .filter((item) => betaTemplateIds.includes(item.id))
    .sort(
      (a, b) => betaTemplateIds.indexOf(a.id) - betaTemplateIds.indexOf(b.id),
    );

  return (
    <section
      className="object-demo object-beta"
      aria-label="Object Sim 베타 시나리오 작성"
    >
      <div className="demo-hero">
        <div>
          <span className="demo-eyebrow">OBJECT SIM · PRIVATE BETA</span>
          <h2>나의 조건으로, 다음 장면을.</h2>
          <p>
            시작 템플릿을 복제하고 객체와 행동을 수정하세요. 저장한 개정을
            실제로 실행한 뒤 기준 결과와 비교하고 관찰을 남깁니다.
          </p>
        </div>
        <ol className="beta-steps">
          <li>복제 · 편집</li>
          <li>개정 저장 · 실행</li>
          <li>A/B · 관찰 저장</li>
        </ol>
      </div>
      {(presets.loading || identity.loading) && (
        <p className="demo-message" role="status">
          프로젝트의 템플릿과 접근 권한을 확인하는 중…
        </p>
      )}
      {(presets.error || identity.error) && (
        <div className="demo-message error" role="alert">
          {presets.error || identity.error}
          <button
            className="button secondary"
            onClick={() => {
              presets.reload();
              identity.reload();
            }}
          >
            다시 확인
          </button>
        </div>
      )}
      {identity.data && !principal && (
        <p className="demo-message error" role="alert">
          계정 식별 정보를 확인할 수 없어 작성안 복원과 실행을 잠갔습니다. API
          서버의 베타 버전을 확인해 주세요.
        </p>
      )}
      {templates?.length === 0 && (
        <p className="demo-message">
          이 프로젝트의 베타 템플릿이 준비되지 않았습니다. 고급 작성 화면에서
          지도·차량·시나리오 연결 상태를 확인하세요.{" "}
          <button className="button secondary" onClick={onAdvanced}>
            고급 작성
          </button>
        </p>
      )}
      <div className="beta-templates">
        {templates?.map((item) => (
          <article
            key={item.id}
            className={`demo-card ${draft?.templateId === item.id ? "selected" : ""}`}
          >
            <div className="demo-card-top">
              <span>{templateMarks[item.id]}</span>
              <span className={`demo-readiness ${item.ready ? "ready" : ""}`}>
                {item.ready ? "작성 가능" : "연결 필요"}
              </span>
            </div>
            <h3>{item.title}</h3>
            <p>{item.description}</p>
            {!item.ready && (
              <p className="demo-binding-reason">{item.reason}</p>
            )}
            <div className="demo-card-actions">
              <button
                className="button"
                disabled={!item.ready || locked || !writable || !storageKey}
                onClick={() => choose(item)}
              >
                <Copy size={14} />내 시나리오로 복제
              </button>
              {item.saved_execution_id && (
                <button
                  className="demo-text-button"
                  onClick={() => setShownId(item.saved_execution_id!)}
                >
                  <Play size={14} />
                  기준 보기
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      {draft && template && (
        <div className="demo-editor" aria-label="내 시나리오 편집">
          <div className="demo-editor-heading">
            <div>
              <span className="demo-eyebrow">{template.title}</span>
              <h3>내 시나리오</h3>
            </div>
            <button className="demo-text-button" onClick={onAdvanced}>
              고급 시나리오 작성 <ArrowRight size={14} />
            </button>
          </div>
          <div className="beta-draft-bar">
            <label className="demo-control">
              시나리오 이름
              <input
                aria-label="시나리오 이름"
                maxLength={200}
                value={draft.name}
                disabled={locked || !writable}
                onChange={(event) =>
                  update({ ...draft, name: event.target.value })
                }
              />
            </label>
            <div className={`beta-save-status ${currentSave ? "saved" : ""}`}>
              <span>
                {currentSave ? <Check size={15} /> : <Save size={15} />}
                {currentSave
                  ? `저장된 개정 r${attempt.current!.saved!.revision}`
                  : "편집 중 · 실행 전 저장 필요"}
              </span>
              <small>
                {executed
                  ? `마지막 실행: 개정 r${executed.saved.revision}${executed.signature !== signature ? " · 현재 작성안과 다름" : " · 현재 작성안과 같음"}`
                  : "아직 이 작성안을 실행하지 않았습니다."}
              </small>
            </div>
          </div>
          {sourceChanged && (
            <div className="demo-message error" role="alert">
              복원한 작성안의 기준 템플릿이 변경되었습니다. 기존 실행은 확인할
              수 있습니다. 새 기준에서 복제한 뒤 수정하세요.
              <button
                className="button secondary"
                disabled={locked}
                onClick={() => choose(template)}
              >
                현재 템플릿으로 다시 복제
              </button>
            </div>
          )}
          <div className="demo-edit-grid beta-edit-grid">
            <div className="demo-map-shell">
              {map && preview ? (
                <DemoMapCanvas
                  key={`${template.id}:${draft.bindingRevision}`}
                  map={map}
                  actors={preview.scenario.actors}
                  selected={actorId}
                  disabled={locked || !writable || previewing || sourceChanged}
                  onSelect={setActorId}
                  onMove={(id, pose) => editActor(id, { pose })}
                  onDestination={(id, destination) =>
                    editActor(id, { destination })
                  }
                  editMode={mapMode}
                  onError={setError}
                />
              ) : (
                <div className="demo-map-empty">
                  <LoaderCircle
                    size={25}
                    className={previewing ? "demo-spin" : ""}
                  />
                  <p>
                    {issues.length
                      ? "작성 오류를 수정해 주세요."
                      : "지도와 객체를 준비하고 있습니다."}
                  </p>
                </div>
              )}
              {previewing && (
                <span className="demo-map-loading" role="status">
                  경로와 입력 확인 중
                </span>
              )}
              {!!issues.length && preview && (
                <span className="demo-map-loading">
                  마지막 유효 장면 · 입력 오류 수정 필요
                </span>
              )}
            </div>
            <aside className="demo-controls beta-controls">
              <label className="demo-control">
                선택 객체
                <select
                  aria-label="베타 선택 객체"
                  value={actorId}
                  onChange={(event) => {
                    setActorId(event.target.value);
                    setMapMode("start");
                  }}
                >
                  {preview?.scenario.actors.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id} · {item.role === "ego" ? "자차" : "상대 차량"}
                    </option>
                  ))}
                </select>
              </label>
              {actor && (
                <>
                  <div className="beta-map-modes">
                    <button
                      className={`button ${mapMode === "start" ? "" : "secondary"}`}
                      aria-pressed={mapMode === "start"}
                      onClick={() => setMapMode("start")}
                    >
                      출발점 이동
                    </button>
                    <button
                      className={`button ${mapMode === "destination" ? "" : "secondary"}`}
                      aria-pressed={mapMode === "destination"}
                      disabled={actorMetadata?.route_editable === false}
                      onClick={() => setMapMode("destination")}
                    >
                      목적지 선택
                    </button>
                  </div>
                  <p className="demo-control-note">
                    {mapMode === "start"
                      ? "지도에서 차량을 드래그하면 차로에 맞춰집니다."
                      : "지도 차로를 클릭하면 선택한 차량의 목적지와 경로를 갱신합니다."}
                  </p>
                  <div className="beta-coordinates">
                    <span>
                      출발 {actor.pose.x.toFixed(1)}, {actor.pose.y.toFixed(1)}{" "}
                      m
                    </span>
                    <span>
                      목적지{" "}
                      {actor.destination
                        ? `${actor.destination.x.toFixed(1)}, ${actor.destination.y.toFixed(1)} m`
                        : "미지정"}
                    </span>
                  </div>
                  <label className="demo-control">
                    {actor.id} 속도 · m/s
                    <input
                      type="number"
                      aria-label={`${actor.id} 속도`}
                      min="0"
                      max="12"
                      step="0.5"
                      value={actor.speed_mps}
                      disabled={locked || !writable}
                      onChange={(event) => {
                        if (event.target.value !== "")
                          editActor(actor.id, {
                            speed_mps: Number(event.target.value),
                          });
                      }}
                    />
                  </label>
                  <button
                    className="demo-text-button"
                    disabled={locked || !draft.actorEdits[actor.id]}
                    onClick={() => {
                      const edits = { ...draft.actorEdits };
                      delete edits[actor.id];
                      update({ ...draft, actorEdits: edits });
                    }}
                  >
                    이 객체의 개별 수정 초기화
                  </button>
                </>
              )}
              <details open className="beta-parameters">
                <summary>장면 조건</summary>
                {template.parameters
                  .filter((parameter) => !parameter.key.endsWith("_speed_mps"))
                  .map((parameter) =>
                    parameter.type === "boolean" ? (
                      <label key={parameter.key} className="demo-toggle">
                        {parameter.label}
                        <input
                          type="checkbox"
                          checked={!!draft.parameters[parameter.key]}
                          disabled={locked || !writable}
                          onChange={(event) =>
                            update(
                              betaChangeParameter(
                                draft,
                                parameter.key,
                                event.target.checked,
                              ),
                            )
                          }
                        />
                      </label>
                    ) : (
                      <label key={parameter.key} className="demo-control">
                        <span>
                          {parameter.label} · {demoParameterUnit(parameter.key)}
                        </span>
                        <input
                          type="number"
                          aria-label={parameter.label}
                          min={parameter.min}
                          max={parameter.max}
                          step={parameter.step ?? 0.5}
                          value={Number(draft.parameters[parameter.key])}
                          disabled={locked || !writable}
                          onChange={(event) => {
                            if (event.target.value !== "")
                              update(
                                betaChangeParameter(
                                  draft,
                                  parameter.key,
                                  Number(event.target.value),
                                ),
                              );
                          }}
                        />
                      </label>
                    ),
                  )}
              </details>
              {!!actor?.behavior?.actions.length && (
                <details className="beta-actions" open>
                  <summary>{actor.id} 행동 시작 조건</summary>
                  {actor.behavior.actions.map((action, index) => (
                    <fieldset key={action.id} disabled={locked || !writable}>
                      <legend>
                        {behaviorNames[action.kind] ?? action.kind}
                      </legend>
                      <div className="beta-action-fields">
                        <label className="demo-control">
                          시작 조건
                          <select
                            aria-label={`${action.id} 시작 조건`}
                            value={action.trigger.kind}
                            onChange={(event) =>
                              actionChange(index, {
                                trigger: {
                                  ...action.trigger,
                                  kind: event.target
                                    .value as BehaviorAction["trigger"]["kind"],
                                  target_actor_id:
                                    event.target.value === "time"
                                      ? null
                                      : (action.trigger.target_actor_id ??
                                        preview!.scenario.actors.find(
                                          (item) => item.id !== actor.id,
                                        )?.id ??
                                        null),
                                },
                              })
                            }
                          >
                            {["time", "distance", "relative_speed", "ttc"].map(
                              (kind) => (
                                <option key={kind} value={kind}>
                                  {
                                    (
                                      {
                                        time: "시간 · 초",
                                        distance: "거리 · m",
                                        relative_speed: "상대 속도 · m/s",
                                        ttc: "TTC · 초",
                                      } as Record<string, string>
                                    )[kind]
                                  }
                                </option>
                              ),
                            )}
                            {![
                              "time",
                              "distance",
                              "relative_speed",
                              "ttc",
                            ].includes(action.trigger.kind) && (
                              <option value={action.trigger.kind}>
                                {action.trigger.kind} · 고급 조건
                              </option>
                            )}
                          </select>
                        </label>
                        <label className="demo-control">
                          조건값
                          <input
                            aria-label={`${action.id} 조건값`}
                            type="number"
                            step="0.1"
                            value={action.trigger.value}
                            onChange={(event) => {
                              if (event.target.value !== "")
                                actionChange(index, {
                                  trigger: {
                                    ...action.trigger,
                                    value: Number(event.target.value),
                                  },
                                });
                            }}
                          />
                        </label>
                        <label className="demo-control">
                          비교
                          <select
                            aria-label={`${action.id} 비교`}
                            value={action.trigger.operator}
                            onChange={(event) =>
                              actionChange(index, {
                                trigger: {
                                  ...action.trigger,
                                  operator: event.target.value as "gte" | "lte",
                                },
                              })
                            }
                          >
                            <option value="gte">이상</option>
                            <option value="lte">이하</option>
                          </select>
                        </label>
                        {action.trigger.kind !== "time" && (
                          <label className="demo-control">
                            기준 객체
                            <select
                              aria-label={`${action.id} 기준 객체`}
                              value={action.trigger.target_actor_id ?? ""}
                              onChange={(event) =>
                                actionChange(index, {
                                  trigger: {
                                    ...action.trigger,
                                    target_actor_id: event.target.value || null,
                                  },
                                })
                              }
                            >
                              <option value="">선택</option>
                              {preview!.scenario.actors
                                .filter((item) => item.id !== actor.id)
                                .map((item) => (
                                  <option key={item.id}>{item.id}</option>
                                ))}
                            </select>
                          </label>
                        )}
                        <label className="demo-control">
                          지속 시간 · 초
                          <input
                            aria-label={`${action.id} 지속 시간`}
                            type="number"
                            min="0"
                            max={Math.min(
                              action.timeout_s,
                              preview?.scenario.duration_s ?? 30,
                            )}
                            step=".5"
                            value={action.duration_s}
                            onChange={(event) => {
                              if (event.target.value !== "")
                                actionChange(index, {
                                  duration_s: Number(event.target.value),
                                });
                            }}
                          />
                        </label>
                        {action.speed_mps != null && (
                          <label className="demo-control">
                            행동 속도 · m/s
                            <input
                              type="number"
                              aria-label={`${action.id} 행동 속도`}
                              min="0"
                              max="12"
                              step=".5"
                              value={action.speed_mps}
                              onChange={(event) => {
                                if (event.target.value !== "")
                                  actionChange(index, {
                                    speed_mps: Number(event.target.value),
                                  });
                              }}
                            />
                          </label>
                        )}
                        {(action.target_lane_node_id ||
                          ["cut_in", "lane_change"].includes(action.kind)) && (
                          <label className="demo-control beta-wide">
                            목표 차로
                            <select
                              aria-label={`${action.id} 목표 차로`}
                              value={action.target_lane_node_id ?? ""}
                              onChange={(event) =>
                                actionChange(index, {
                                  target_lane_node_id:
                                    event.target.value || null,
                                })
                              }
                            >
                              <option value="">차로 선택</option>
                              {map?.nodes?.map((node) => (
                                <option key={node.id} value={node.id}>
                                  차로 {node.lane_id} · 도로 {node.road_id} ·{" "}
                                  {node.id}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                      </div>
                    </fieldset>
                  ))}
                </details>
              )}
              {actor && !actor.behavior?.actions.length && (
                <p className="demo-control-note">
                  이 차량은 지정 경로와 장면 조건으로 주행합니다. 행동 종류
                  변경은 고급 작성에서 제공합니다.
                </p>
              )}
            </aside>
          </div>
          {!!issues.length && (
            <div className="beta-validation demo-message error" role="alert">
              <strong>수정할 항목</strong>
              <ul>
                {issues.map((issue, index) => (
                  <li key={index}>
                    <button
                      className="demo-text-button"
                      onClick={() => {
                        if (issue.actor_id) setActorId(issue.actor_id);
                      }}
                    >
                      {issue.actor_id ? `${issue.actor_id} · ` : ""}
                      {issue.field}
                    </button>{" "}
                    — {issue.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="demo-run-bar">
            <div>
              <strong>
                {currentPreview
                  ? "작성 입력 확인됨 · 실제 배치는 실행에서 확인"
                  : sourceChanged
                    ? "기준 템플릿 변경 확인 필요"
                    : "작성 입력 확인 중"}
              </strong>
              <span>
                {baselineId
                  ? "기준 A 연결됨 · 새 실행을 B로 비교합니다."
                  : "첫 완료 실행을 기준 A로 고정합니다."}
              </span>
            </div>
            <div className="beta-run-actions">
              <button
                className="button secondary"
                disabled={
                  !currentPreview ||
                  !scenario?.name ||
                  locked ||
                  !writable ||
                  !storageKey
                }
                onClick={() => void save()}
              >
                <Save size={15} />
                개정 저장
              </button>
              <button
                className="button"
                disabled={!canRun}
                onClick={() => void run()}
              >
                {busy ? (
                  <LoaderCircle size={15} className="demo-spin" />
                ) : (
                  <Play size={15} />
                )}
                {busy
                  ? stage || "저장 중"
                  : attempt.current?.experiment &&
                      !attempt.current.execution &&
                      currentSave
                    ? "같은 제출 다시 확인"
                    : execution && demoTerminal(execution.status)
                      ? "저장 후 다시 실행"
                      : "저장 후 실제 실행"}
              </button>
            </div>
          </div>
          {queue.data?.enabled && (
            <p className="demo-control-note">
              내 실행 {queue.data.active_count}/{queue.data.active_limit} · 대기{" "}
              {queue.data.queued_count}/{queue.data.queue_limit}
              {!queue.data.can_submit
                ? " · 대기 작업을 취소하거나 완료 후 제출하세요."
                : " · 공유 CARLA 자원에서 순서대로 실행합니다."}
            </p>
          )}
          {native?.status !== "available" && (
            <p className="demo-control-note">
              실행 환경:{" "}
              {capabilities.loading
                ? "확인 중"
                : (native?.description ??
                  capabilities.error ??
                  "CARLA 연결 필요")}
              . 작성과 저장은 사용할 수 있습니다.
            </p>
          )}
          {!writable && (
            <p className="demo-control-note">
              작성·실행에는 Engineer 권한이 필요합니다.
            </p>
          )}
          <details className="demo-details">
            <summary>작성 범위와 기준 정보</summary>
            <p>
              객체 GT 기반 CARLA 시뮬레이션 · 지도는 작성 도식입니다. 카메라와
              실제 행동은 완료 실행에서 확인합니다.
            </p>
            <p>
              템플릿 binding r{draft.bindingRevision} · seed{" "}
              {preview?.scenario.seed ?? "—"} ·{" "}
              {preview?.scenario.duration_s ?? "—"}초 ·{" "}
              {native?.id ?? "실행 환경 미연결"} {native?.version}
            </p>
            {preview?.warnings.map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
          </details>
        </div>
      )}
      {error && (
        <div className="demo-message error" role="alert">
          {error}
          {executed && (
            <button
              className="button secondary"
              onClick={() => {
                setError("");
                setPollVersion((value) => value + 1);
              }}
            >
              상태 다시 확인
            </button>
          )}
        </div>
      )}
      {unresolvedSubmission && (
        <div className="demo-message error" role="alert">
          <div>
            <strong>제출 응답을 확인하지 못했습니다.</strong>
            <p>
              같은 요청 번호로 실행 접수 여부를 확인할 때까지 작성안을
              유지합니다. 새로고침 후에도 같은 제출을 복구할 수 있습니다.
            </p>
          </div>
          <button
            className="button secondary"
            disabled={busy || !writable}
            onClick={() => void run(true)}
          >
            {busy ? "확인 중…" : "같은 제출 접수 확인"}
          </button>
        </div>
      )}
      {notice && (
        <p className="demo-message" role="status">
          {notice}
        </p>
      )}
      {executed && (
        <section className="demo-execution" aria-label="내 시나리오 실행 상태">
          <div className="demo-execution-title">
            <span className={`demo-live-dot ${activeRun ? "active" : ""}`} />
            <div>
              <span className="demo-eyebrow">
                저장 개정 r{executed.saved.revision} ·{" "}
                {executed.saved.scenario.name}
              </span>
              <h3>
                {execution ? demoStatus(execution.status) : "실행 상태 확인 중"}
              </h3>
            </div>
          </div>
          <div className="demo-execution-actions">
            {activeRun &&
              writable &&
              (execution?.status === "queued" ||
                execution?.cancellation_supported) && (
                <button
                  className="button secondary"
                  disabled={busy || !!execution?.cancel_requested_at}
                  onClick={() => void cancel()}
                >
                  {execution?.cancel_requested_at
                    ? "취소 처리 중"
                    : "실행 취소"}
                </button>
              )}
            {execution?.manifest_hash && (
              <button
                className="button secondary"
                onClick={() => setShownId(executed.id)}
              >
                <Play size={15} />
                실제 결과 보기
              </button>
            )}
            {canCompare && (
              <button
                className="button"
                onClick={() => onCompare(baselineId!, executed.id)}
              >
                <GitCompareArrows size={15} />
                기준 A / 실행 B 비교
              </button>
            )}
            {baselineId && (
              <button
                className="demo-text-button"
                onClick={() => setShownId(baselineId)}
              >
                기준 A 재생
              </button>
            )}
            {execution?.status === "completed" &&
              execution.manifest_hash &&
              baselineId !== executed.id && (
                <button
                  className="demo-text-button"
                  onClick={() => {
                    setBaselineId(executed.id);
                    setNotice(
                      "이 실제 실행을 다음 비교의 기준 A로 고정했습니다.",
                    );
                  }}
                >
                  이 결과를 기준 A로 고정
                </button>
              )}
          </div>
          {execution?.error && (
            <p className="demo-message error">{execution.error}</p>
          )}
          {execution?.outcome_uncertain && (
            <p className="demo-message error">
              실행 결과가 불확실합니다. 아래 관찰에 확인한 내용을 남겨 주세요.
            </p>
          )}
        </section>
      )}
      {shownId && (
        <section className="demo-recording" aria-label="실제 실행 재생">
          <div className="demo-editor-heading">
            <div>
              <span className="demo-eyebrow">
                {shownId === executed?.id
                  ? "내가 제출한 저장 개정의 결과"
                  : "저장된 기준 결과 · 현재 작성안의 실행 결과가 아닙니다"}
              </span>
              <h3>실제 실행 재생</h3>
            </div>
            <button
              className="button secondary"
              onClick={() => setShownId(null)}
            >
              재생 닫기
            </button>
          </div>
          {displayed.loading && (
            <p className="demo-message" role="status">
              원본 결과를 불러오는 중…
            </p>
          )}
          {displayed.error && (
            <p className="demo-message error" role="alert">
              {displayed.error}
            </p>
          )}
          {displayed.data && <CitySceneViewer execution={displayed.data} />}
        </section>
      )}
      {executed && observationAvailable && (
        <ObjectBetaBehavior project={project} executionId={executed.id} />
      )}
      {executed && observationAvailable && (
        <section
          className="demo-editor beta-observations"
          aria-label="실행 관찰과 문제 제보"
        >
          <div className="demo-editor-heading">
            <div>
              <span className="demo-eyebrow">
                실행 개정 r{executed.saved.revision}에 연결
              </span>
              <h3>무엇을 관찰했나요?</h3>
            </div>
            <span className="demo-control-note">
              행동 발생과 자차 대응을 따로 기록합니다.
            </span>
          </div>
          <form onSubmit={(event) => void saveObservation(event)}>
            <div className="beta-observation-grid">
              <label className="demo-control">
                종류
                <select
                  aria-label="관찰 종류"
                  value={observation.kind}
                  onChange={(event) =>
                    setObservation({ ...observation, kind: event.target.value })
                  }
                >
                  <option value="observation">관찰</option>
                  <option value="issue">문제 제보</option>
                </select>
              </label>
              <label className="demo-control">
                실행 시점 · 초
                <input
                  type="number"
                  aria-label="관찰 시점"
                  min="0"
                  max={executed.saved.scenario.duration_s}
                  step="0.05"
                  value={observation.time_s}
                  onChange={(event) =>
                    setObservation({
                      ...observation,
                      time_s: Number(event.target.value),
                    })
                  }
                />
              </label>
              <label className="demo-control">
                행동 발생
                <select
                  aria-label="행동 발생"
                  value={observation.behavior_exercised}
                  onChange={(event) =>
                    setObservation({
                      ...observation,
                      behavior_exercised: event.target.value,
                    })
                  }
                >
                  <option value="uncertain">확인 필요</option>
                  <option value="observed">발생 확인</option>
                  <option value="not_observed">발생하지 않음</option>
                </select>
              </label>
              <label className="demo-control">
                자차 대응
                <select
                  aria-label="자차 대응"
                  value={observation.ego_outcome}
                  onChange={(event) =>
                    setObservation({
                      ...observation,
                      ego_outcome: event.target.value,
                    })
                  }
                >
                  <option value="uncertain">확인 필요</option>
                  <option value="arrived">목적지 도달</option>
                  <option value="stopped">정지</option>
                  <option value="collision">접촉 관찰</option>
                  <option value="other">기타</option>
                </select>
              </label>
              <label className="demo-control beta-wide">
                제목
                <input
                  aria-label="관찰 제목"
                  required
                  maxLength={180}
                  value={observation.title}
                  onChange={(event) =>
                    setObservation({
                      ...observation,
                      title: event.target.value,
                    })
                  }
                />
              </label>
              <label className="demo-control beta-wide">
                메모
                <textarea
                  aria-label="관찰 메모"
                  required
                  rows={3}
                  maxLength={4000}
                  value={observation.note}
                  onChange={(event) =>
                    setObservation({ ...observation, note: event.target.value })
                  }
                />
              </label>
            </div>
            <p className="demo-control-note">
              사용자가 확인한 관찰입니다. 시뮬레이션의 안전 통과 판정으로
              사용하지 않습니다.
            </p>
            {observationError && (
              <p className="demo-message error" role="alert">
                {observationError}
              </p>
            )}
            <button
              className="button"
              type="submit"
              disabled={
                observationBusy ||
                !writable ||
                !observation.title.trim() ||
                !observation.note.trim()
              }
            >
              {observationBusy ? "저장 중…" : "관찰 저장"}
            </button>
          </form>
          {observations.error && (
            <p className="demo-message error">{observations.error}</p>
          )}
          <ul className="beta-observation-list">
            {observations.data?.items.map((item) => (
              <li key={item.id}>
                <span>
                  {item.kind === "issue" ? "문제 제보" : "관찰"} ·{" "}
                  {item.time_s.toFixed(2)}초
                </span>
                <strong>{item.title}</strong>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}
