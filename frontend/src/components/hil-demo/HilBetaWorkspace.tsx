import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Copy, Download, Plus, Save } from "lucide-react";
import { api, authHeaders, isExternalViewer, useResource } from "../../api";
import HilPresenter from "./HilPresenter";
import HilFaultTimingEditor from "./HilFaultTimingEditor";
import HilDutProfiles, { HilProfileComparisonEditor } from "./HilDutProfiles";
import HilBatches from "./HilBatches";
import HilScenePicker from "./HilScenePicker";
import type { HilProfileList } from "./HilDutProfiles";
import {
  hilProfileSelectionError,
  hilUpdateProfileSelection,
} from "./dut-profile";
import { hilTimingError } from "./fault-timing";
import {
  HilPreviewChanged,
  hilEngineStatus,
  hilErrorMessage,
  hilParameterError,
  hilStatus,
  hilTerminal,
  isHilExecution,
} from "./demo-contract";
import type { HilExecution, HilPreset } from "./demo-contract";
import {
  executeHilTest,
  hilDraftFromPreset,
  hilDraftSignature,
  hilDuplicateDraft,
  hilObservationsPath,
  hilPendingKey,
  hilResultQuery,
  hilTestDraft,
  hilTestPreviewMatches,
  hilTestRef,
  hilTestSaveBody,
  hilTrackedResultQuery,
  restoreHilPending,
} from "./beta-contract";
import type {
  HilObservation,
  HilPending,
  HilTest,
  HilTestDraft,
  HilTestExecution,
  HilTestPreview,
} from "./beta-contract";
import "./hil-beta.css";

const message = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
function updateQuery(values: Record<string, string | null>) {
  const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
  query.set("mode", "beta");
  for (const [key, value] of Object.entries(values))
    if (value === null) query.delete(key);
    else query.set(key, value);
  history.replaceState(
    null,
    "",
    `${location.pathname}${location.search}#hil-sim?${query}`,
  );
}
const qs = (project: string, revision?: number) =>
  new URLSearchParams({
    project_id: project,
    ...(revision ? { revision: String(revision) } : {}),
  });

export default function HilBetaWorkspace({ project }: { project: string }) {
  const initial = useRef(
    new URLSearchParams(location.hash.split("?")[1] ?? ""),
  );
  const [work, setWork] = useState<"individual" | "batches">(
    initial.current.get("work") === "batches" ? "batches" : "individual",
  );
  const identity = useResource<{
    principal_id: string;
    mode: string;
    role: string;
    permissions?: { execute?: boolean };
  }>("/team/whoami");
  const presets = useResource<{ items: HilPreset[] }>(
    `/hil-sim/presets?${qs(project)}`,
  );
  const tests = useResource<{ items: HilTest[]; next_offset: number | null }>(
    `/hil-sim/tests?${qs(project)}&limit=100`,
  );
  const profiles = useResource<HilProfileList>(
    `/hil-sim/dut-profiles?${qs(project)}&limit=100`,
  );
  const capabilities = useResource<{
    handlers: { id: string; status: string; description?: string }[];
  }>("/experiments/capabilities");
  const retained = useResource<{
    items: {
      id: string;
      name: string;
      experiment_kind: string;
      status: string;
    }[];
    next_before_seq: number | null;
  }>(`/experiments/executions?${qs(project)}&limit=100`);
  const [saved, setSaved] = useState<HilTest | null>(null),
    [draft, setDraft] = useState<HilTestDraft | null>(null),
    [preview, setPreview] = useState<HilTestPreview | null>(null);
  const [tab, setTab] = useState<
      "settings" | "profiles" | "history" | "observations"
    >("settings"),
    [busy, setBusy] = useState(""),
    [notice, setNotice] = useState(""),
    [error, setError] = useState("");
  const [shownId, setShownId] = useState(initial.current.get("result")),
    [runId, setRunId] = useState(initial.current.get("run")),
    [run, setRun] = useState<HilExecution | null>(null),
    [pollVersion, setPollVersion] = useState(0);
  const [pending, setPending] = useState<HilPending | null>(null),
    [cursor, setCursor] = useState(0),
    [seekRequest, setSeekRequest] = useState<
      { timeS: number; nonce: number } | undefined
    >();
  const [existingId, setExistingId] = useState(""),
    [observation, setObservation] = useState({
      kind: "observation",
      side: "both",
      title: "",
      note: "",
    });
  const initialized = useRef(false),
    restored = useRef(false),
    performing = useRef(false),
    saveAttempt = useRef({ body: "", key: "" }),
    observationAttempt = useRef({ body: "", key: "" }),
    attachAttempt = useRef({ body: "", key: "" });
  const displayed = useResource<HilExecution>(
    shownId ? `/experiments/executions/${encodeURIComponent(shownId)}` : null,
  );
  const executions = useResource<{
    items: HilTestExecution[];
    next_offset: number | null;
  }>(
    saved
      ? `/hil-sim/tests/${encodeURIComponent(saved.id)}/executions?${qs(project, saved.revision)}&limit=100`
      : null,
  );
  const observations = useResource<{
    items: HilObservation[];
    next_offset: number | null;
  }>(hilObservationsPath(project, shownId, saved));
  const principal = identity.data?.principal_id;
  const writable =
    !isExternalViewer() &&
    !!identity.data &&
    (identity.data.mode !== "project" ||
      ["Engineer", "Admin"].includes(identity.data.role));
  const handler = capabilities.data?.handlers.find(
      (item) => item.id === "carla-software-dut",
    ),
    configured = handler?.status === "available";
  const engineStatus = hilEngineStatus(
    handler?.status,
    capabilities.loading,
    capabilities.error,
    !!preview && !!saved && hilTestPreviewMatches(preview, saved),
  );
  const preset = presets.data?.items.find(
      (item) => item.id === draft?.preset_id,
    ),
    parameter = preset?.parameters[0],
    value = parameter
      ? (draft?.parameters[parameter.key] ?? parameter.default)
      : 0;
  const dirty =
    !!draft &&
    (!saved ||
      hilDraftSignature(draft) !== hilDraftSignature(hilTestDraft(saved)));
  const parameterError = parameter ? hilParameterError(parameter, value) : "";
  const timingError =
    preset?.fault_timing && draft
      ? hilTimingError(preset.fault_timing, draft.parameters)
      : "";
  const profileError = hilProfileSelectionError(draft?.dut_profiles);
  const savedCurrent =
    !!saved &&
    !!draft &&
    !dirty &&
    !parameterError &&
    !timingError &&
    !profileError;
  const activeRun = !!runId && (!run || !hilTerminal(run.status));
  const locked = !!busy || !!pending || activeRun;
  const selectedExecution =
    displayed.data &&
    displayed.data.id === shownId &&
    isHilExecution(displayed.data, project)
      ? displayed.data
      : null;
  const linked =
    !!saved &&
    !!shownId &&
    !!executions.data?.items.some(
      (item) =>
        item.execution.id === shownId &&
        item.test_ref.revision === saved.revision &&
        item.test_ref.sha256 === saved.sha256,
    );
  const resultPreset =
    selectedExecution?.summary?.fault?.kind === "drop"
      ? "input_dropout"
      : "input_delay";
  const onCursor = useCallback((timeS: number) => setCursor(timeS), []);

  async function perform(stage: string, operation: () => Promise<void>) {
    if (performing.current) return;
    performing.current = true;
    setBusy(stage);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (reason) {
      setError(message(reason));
    } finally {
      performing.current = false;
      setBusy("");
    }
  }
  function remember(value: HilPending | null) {
    if (!principal)
      throw new Error("계정 식별 정보를 확인한 뒤 다시 시도하세요.");
    const key = hilPendingKey(project, principal);
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    else sessionStorage.removeItem(key);
    setPending(value);
  }
  async function openTest(id: string, revision?: number, resultId?: string) {
    const value = await api<HilTest>(
      `/hil-sim/tests/${encodeURIComponent(id)}?${qs(project, revision)}`,
    );
    if (value.project_id !== project)
      throw new Error("다른 프로젝트의 시험입니다.");
    setSaved(value);
    setDraft(hilTestDraft(value));
    setPreview(null);
    setError("");
    if (resultId) setShownId(resultId);
    updateQuery({
      project,
      test: value.id,
      revision: String(value.revision),
      ...(resultId ? { result: resultId } : {}),
    });
  }
  useEffect(() => {
    if (initialized.current || !presets.data) return;
    initialized.current = true;
    const id = initial.current.get("test"),
      revision = Number(initial.current.get("revision"));
    if (id)
      void perform("시험 복원", () =>
        openTest(id, revision > 0 ? revision : undefined),
      );
    else {
      const first = presets.data.items.find(
        (item) => item.binding_revision && item.binding_sha256,
      );
      if (first) setDraft(hilDraftFromPreset(first));
    }
  }, [presets.data]);
  useEffect(() => {
    if (!principal || restored.current) return;
    restored.current = true;
    try {
      const recovered = restoreHilPending(
        sessionStorage.getItem(hilPendingKey(project, principal)),
        project,
        principal,
      );
      if (recovered) {
        setPending(recovered);
        setNotice(
          "미완료 실행 접수·연결 요청을 복원했습니다. 같은 요청으로 확인하세요.",
        );
      }
    } catch {
      setError(
        "브라우저 복원 기록을 읽을 수 없습니다. 실행 이력에서 접수 상태를 확인하세요.",
      );
    }
  }, [principal, project]);
  useEffect(() => {
    if (!runId) return;
    let active = true,
      timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function poll() {
      try {
        const next = await api<HilExecution>(
          `/experiments/executions/${encodeURIComponent(runId!)}`,
          { signal: controller.signal },
        );
        if (!active) return;
        if (!isHilExecution(next, project))
          throw new Error("선택한 프로젝트의 HIL 실행이 아닙니다.");
        setRun(next);
        if (!hilTerminal(next.status))
          timer = setTimeout(() => void poll(), 2000);
        else executions.reload();
      } catch (reason) {
        if (active) setError(message(reason));
      }
    }
    void poll();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId, project, pollVersion]);
  function edit(values: Partial<HilTestDraft>) {
    if (locked || !draft) return;
    setDraft({ ...draft, ...values });
    setPreview(null);
    setNotice("");
  }
  function newTest(item: HilPreset) {
    if (locked) return;
    try {
      setDraft(hilDraftFromPreset(item));
      setSaved(null);
      setPreview(null);
      setTab("settings");
      setNotice("");
      setError("");
      updateQuery({ test: null, revision: null });
    } catch (reason) {
      setError(message(reason));
    }
  }
  function duplicate() {
    if (!saved || !draft || locked) return;
    setDraft(hilDuplicateDraft(saved, draft));
    setSaved(null);
    setPreview(null);
    setTab("settings");
    updateQuery({ test: null, revision: null });
  }
  async function save() {
    if (
      !draft ||
      !writable ||
      parameterError ||
      timingError ||
      profileError ||
      !draft.name.trim()
    )
      return;
    await perform("시험 저장", async () => {
      const body = JSON.stringify(hilTestSaveBody(project, draft, saved));
      if (saveAttempt.current.body !== body)
        saveAttempt.current = { body, key: `hil-test-${crypto.randomUUID()}` };
      const value = await api<HilTest>("/hil-sim/tests", {
        method: "POST",
        headers: { "Idempotency-Key": saveAttempt.current.key },
        body,
      });
      setSaved(value);
      setDraft(hilTestDraft(value));
      setPreview(null);
      tests.reload();
      updateQuery({
        project,
        test: value.id,
        revision: String(value.revision),
      });
      setNotice(`시험 r${value.revision} 저장 완료. 실행은 별도로 제출합니다.`);
    });
  }
  async function prepare() {
    if (!savedCurrent || !saved || !writable) return;
    await perform("저장 개정 실행 확인", async () => {
      const next = await api<HilTestPreview>(
        `/hil-sim/tests/${encodeURIComponent(saved.id)}/preview`,
        {
          method: "POST",
          body: JSON.stringify({
            project_id: project,
            test_ref: hilTestRef(saved),
          }),
        },
      );
      if (!hilTestPreviewMatches(next, saved))
        throw new Error("실행 확인 결과가 선택한 시험 개정과 다릅니다.");
      setPreview(next);
    });
  }
  function refreshConnectionSettings() {
    if (locked) return;
    setPreview(null);
    capabilities.reload();
    presets.reload();
  }
  async function finishPending(work: HilPending) {
    await perform("실행 접수·연결", async () => {
      remember(work);
      try {
        const job = await executeHilTest(api, work, setBusy, (submitted) => {
          remember({ ...submitted });
          setRunId(submitted.attempt.execution!.id);
          updateQuery({ run: submitted.attempt.execution!.id });
        });
        setRunId(job.id);
        updateQuery({ run: job.id });
        remember(null);
        await openTest(work.test_ref.id, work.test_ref.revision);
        executions.reload();
        setNotice(
          "실행을 저장 시험 개정에 연결했습니다. 완료 후 새 결과를 열 수 있습니다.",
        );
      } catch (reason) {
        if (reason instanceof HilPreviewChanged) {
          remember(null);
          setPreview(null);
        } else remember({ ...work });
        throw reason;
      }
    });
  }
  async function execute() {
    if (
      !saved ||
      !preview ||
      !principal ||
      !savedCurrent ||
      !hilTestPreviewMatches(preview, saved)
    )
      return;
    const key = `hil-beta-${crypto.randomUUID()}`;
    await finishPending({
      version: 1,
      project,
      principal,
      test_ref: hilTestRef(saved),
      preview,
      attempt: { key, signature: JSON.stringify(hilTestRef(saved)) },
      attachment_key: `${key}-attach`,
    });
  }
  async function attach() {
    if (!saved || !existingId || !writable) return;
    await perform("기존 결과 연결", async () => {
      const body = JSON.stringify({
        project_id: project,
        test_ref: hilTestRef(saved),
        execution_id: existingId,
      });
      if (attachAttempt.current.body !== body)
        attachAttempt.current = {
          body,
          key: `hil-attach-${crypto.randomUUID()}`,
        };
      await api(`/hil-sim/tests/${encodeURIComponent(saved.id)}/executions`, {
        method: "POST",
        headers: { "Idempotency-Key": attachAttempt.current.key },
        body,
      });
      executions.reload();
      setShownId(existingId);
      updateQuery({ result: existingId });
      setNotice(
        "고정 입력이 일치하는 기존 결과를 연결했습니다. 새 실행은 하지 않았습니다.",
      );
    });
  }
  async function saveObservation(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedExecution || !saved || !linked || !writable) return;
    await perform("관찰 저장", async () => {
      const body = JSON.stringify({
        project_id: project,
        test_ref: hilTestRef(saved),
        execution_id: selectedExecution.id,
        ...observation,
        title: observation.title.trim(),
        note: observation.note.trim(),
        time_s: cursor,
      });
      if (observationAttempt.current.body !== body)
        observationAttempt.current = {
          body,
          key: `hil-note-${crypto.randomUUID()}`,
        };
      await api("/hil-sim/observations", {
        method: "POST",
        headers: { "Idempotency-Key": observationAttempt.current.key },
        body,
      });
      observations.reload();
      setObservation({ ...observation, title: "", note: "" });
      setNotice("선택 결과와 응답 시점에 관찰을 연결했습니다.");
    });
  }
  async function exportTest() {
    if (!saved) return;
    await perform("시험 내보내기", async () => {
      const response = await fetch(
        `/api/v1/hil-sim/tests/${encodeURIComponent(saved.id)}/export?${qs(project, saved.revision)}`,
        { headers: authHeaders() },
      );
      if (!response.ok) throw new Error(`내보내기 실패 (${response.status})`);
      const url = URL.createObjectURL(await response.blob()),
        link = document.createElement("a");
      link.href = url;
      link.download = `${saved.id}-r${saved.revision}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("시험 개정·실행·관찰 JSON을 내보냈습니다.");
    });
  }
  const openResult = (id: string) => {
    setShownId(id);
    setCursor(0);
    setSeekRequest(undefined);
    updateQuery(hilResultQuery(id));
  };
  const terminalResult =
    !!selectedExecution && hilTerminal(selectedExecution.status);
  return (
    <section className="hil-beta" aria-label="HIL 베타 시험 관리">
      <header className="hil-beta-heading">
        <div>
          <span className="hil-eyebrow">SAVE. REPEAT. INVESTIGATE.</span>
          <h1>
            {work === "batches"
              ? "고정한 조건을 순서대로 실행하세요."
              : "고장 시험을 저장하고, 반응을 기록하세요."}
          </h1>
          <p>Software DUT · CARLA Simulation · 입력 지연 / 단절</p>
        </div>
        {work === "individual" && (
          <button disabled={!saved || !!busy} onClick={() => void exportTest()}>
            <Download size={15} />
            JSON 내보내기
          </button>
        )}
      </header>
      <nav className="hil-beta-work-tabs" aria-label="HIL 시험 실행 방식">
        <button
          className={work === "individual" ? "selected" : ""}
          onClick={() => {
            setWork("individual");
            updateQuery({ work: null });
          }}
        >
          개별 시험
        </button>
        <button
          className={work === "batches" ? "selected" : ""}
          onClick={() => {
            setWork("batches");
            updateQuery({ work: "batches" });
          }}
        >
          순차 실행
        </button>
        <small>Software DUT · 실제 보존 결과와 사용자 관찰</small>
      </nav>
      <div hidden={work !== "batches"}>
        <HilBatches
          project={project}
          principal={principal}
          writable={writable}
          tests={tests.data?.items ?? []}
          reloadTests={tests.reload}
          blockedReason={
            pending
              ? "개별 시험의 이전 실행 접수 여부를 먼저 확인하세요."
              : activeRun
                ? "개별 시험이 실행 중입니다. 현재 실행이 끝나면 순차 실행을 시작할 수 있습니다."
                : busy
                  ? "개별 시험 요청을 처리 중입니다."
                  : ""
          }
          onOpenResult={(ref, executionId, timeS) => {
            if (locked) return;
            setWork("individual");
            updateQuery({ work: null });
            void perform("순차 실행 결과 열기", async () => {
              await openTest(ref.id, ref.revision, executionId);
              setRunId(executionId);
              setRun((current) =>
                current?.id === executionId ? current : null,
              );
              setPollVersion((current) => current + 1);
              setTab("observations");
              setCursor(timeS ?? 0);
              setSeekRequest(
                timeS === undefined ? undefined : { timeS, nonce: Date.now() },
              );
              updateQuery(hilTrackedResultQuery(executionId, timeS ?? null));
            });
          }}
        />
      </div>
      <div hidden={work !== "individual"}>
        <div className="hil-beta-library">
          <label>
            저장 시험
            <select
              aria-label="저장된 HIL 시험"
              value={saved?.id ?? ""}
              disabled={locked}
              onChange={(event) => {
                if (event.target.value)
                  void perform("시험 열기", () => openTest(event.target.value));
              }}
            >
              <option value="">새 시험 작성 중</option>
              {tests.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · r{item.revision}
                </option>
              ))}
            </select>
          </label>
          {saved && (
            <select
              aria-label="HIL 시험 개정"
              value={saved.revision}
              disabled={locked}
              onChange={(event) =>
                void perform("시험 개정 열기", () =>
                  openTest(saved.id, Number(event.target.value)),
                )
              }
            >
              {Array.from(
                {
                  length: Math.max(
                    saved.revision,
                    tests.data?.items.find((item) => item.id === saved.id)
                      ?.revision ?? 1,
                  ),
                },
                (_, index) => index + 1,
              )
                .reverse()
                .map((revision) => (
                  <option key={revision} value={revision}>
                    r{revision}
                  </option>
                ))}
            </select>
          )}
          <button disabled={locked || !saved} onClick={duplicate}>
            <Copy size={14} />
            복제
          </button>
          {presets.data?.items.map((item) => (
            <button
              key={item.id}
              disabled={locked || !item.binding_revision}
              onClick={() => newTest(item)}
            >
              <Plus size={14} />
              {item.id === "input_delay" ? "지연 시험" : "단절 시험"}
            </button>
          ))}
        </div>
        {(tests.error || presets.error || identity.error) && (
          <p className="hil-error" role="alert">
            {tests.error || presets.error || identity.error}
          </p>
        )}
        {notice && (
          <p className="hil-beta-notice" role="status">
            {notice}
          </p>
        )}
        {error && (
          <div className="hil-error" role="alert">
            {hilErrorMessage(error)}
            {hilErrorMessage(error) !== error && (
              <details>
                <summary>원본 오류</summary>
                <pre>{error}</pre>
              </details>
            )}
          </div>
        )}
        {pending && (
          <div className="hil-beta-recovery" role="status">
            <strong>
              {pending.attempt.execution
                ? "접수된 실행의 시험 연결을 마쳐야 합니다."
                : "이전 실행 접수 여부를 확인해야 합니다."}
            </strong>
            <span>
              {pending.attempt.execution?.id ?? pending.test_ref.id} · r
              {pending.test_ref.revision}
            </span>
            <button
              disabled={!!busy || !writable}
              onClick={() => void finishPending(pending)}
            >
              {pending.attempt.execution
                ? "같은 실행 연결 재시도"
                : "같은 요청으로 접수 확인"}
            </button>
          </div>
        )}
        <div className="hil-beta-grid">
          <aside className="hil-beta-sidebar">
            <nav aria-label="HIL 시험 업무">
              <button
                className={tab === "profiles" ? "selected" : ""}
                onClick={() => setTab("profiles")}
              >
                DUT 프로필
              </button>
              <button
                className={tab === "settings" ? "selected" : ""}
                onClick={() => setTab("settings")}
              >
                시험 설정
              </button>
              <button
                className={tab === "history" ? "selected" : ""}
                onClick={() => setTab("history")}
              >
                실행 이력
              </button>
              <button
                className={tab === "observations" ? "selected" : ""}
                onClick={() => setTab("observations")}
              >
                관찰
              </button>
            </nav>
            {tab === "profiles" && (
              <>
                <HilDutProfiles
                  project={project}
                  records={profiles.data}
                  reload={profiles.reload}
                  writable={writable}
                  locked={locked}
                />
                {profiles.error && (
                  <p className="hil-error" role="alert">
                    {profiles.error}
                  </p>
                )}
              </>
            )}
            {tab === "settings" && draft && (
              <div className="hil-beta-form">
                <span className="hil-eyebrow">
                  {saved ? `${saved.id} · r${saved.revision}` : "NEW TEST"}
                </span>
                <label>
                  시험 이름
                  <input
                    aria-label="HIL 시험 이름"
                    maxLength={180}
                    value={draft.name}
                    disabled={locked || !writable}
                    onChange={(event) => edit({ name: event.target.value })}
                  />
                </label>
                <label>
                  시험 목적
                  <textarea
                    aria-label="HIL 시험 목적"
                    rows={3}
                    maxLength={2000}
                    value={draft.note}
                    disabled={locked || !writable}
                    onChange={(event) => edit({ note: event.target.value })}
                    placeholder="무엇을 바꾸고 어떤 반응을 확인하나요?"
                  />
                </label>
                <HilScenePicker
                  project={project}
                  selected={draft.scene_profile}
                  retained={saved?.scene_profile}
                  disabled={locked || !writable}
                  onChange={(scene_profile) => edit({ scene_profile })}
                />
                <HilProfileComparisonEditor
                  key={saved ? `${saved.id}:${saved.revision}` : "new-test"}
                  project={project}
                  profiles={profiles.data?.items ?? []}
                  selections={draft.dut_profiles}
                  disabled={locked || !writable}
                  onChange={(dut_profiles) => edit({ dut_profiles })}
                  onPick={(side, ref) => {
                    if (locked) return;
                    setDraft((current) =>
                      current?.dut_profiles
                        ? {
                            ...current,
                            dut_profiles: hilUpdateProfileSelection(
                              current.dut_profiles,
                              side,
                              ref,
                            ),
                          }
                        : current,
                    );
                    setPreview(null);
                    setNotice("");
                  }}
                  onManage={() => setTab("profiles")}
                />
                {profileError && (
                  <p className="hil-error" role="alert">
                    {profileError}
                  </p>
                )}
                {parameter && (
                  <>
                    <label>
                      {parameter.label} · {parameter.unit}
                      <input
                        aria-label={`베타 ${parameter.label}`}
                        type="number"
                        min={parameter.min}
                        max={parameter.max}
                        step={parameter.step}
                        value={Number.isFinite(value) ? value : ""}
                        disabled={locked || !writable}
                        onChange={(event) =>
                          edit({
                            parameters: {
                              ...draft.parameters,
                              [parameter.key]:
                                event.target.value === ""
                                  ? NaN
                                  : Number(event.target.value),
                            },
                          })
                        }
                      />
                    </label>
                    <input
                      aria-label="베타 고장 값 슬라이더"
                      type="range"
                      min={parameter.min}
                      max={parameter.max}
                      step={parameter.step}
                      value={Number.isFinite(value) ? value : parameter.default}
                      disabled={locked || !writable}
                      onChange={(event) =>
                        edit({
                          parameters: {
                            ...draft.parameters,
                            [parameter.key]: Number(event.target.value),
                          },
                        })
                      }
                    />
                    <small>
                      {parameter.min}–{parameter.max} {parameter.unit} ·{" "}
                      {parameter.step} 단위
                    </small>
                  </>
                )}
                {parameterError && (
                  <p className="hil-error">{parameterError}</p>
                )}
                {preset?.fault_timing && (
                  <HilFaultTimingEditor
                    timing={preset.fault_timing}
                    parameters={draft.parameters}
                    appliesToBoth={!!draft.dut_profiles}
                    disabled={locked || !writable}
                    onChange={(parameters) => edit({ parameters })}
                  />
                )}
                <p className="hil-beta-draft-state">
                  {dirty
                    ? "편집 중 · 실행 전 저장 필요"
                    : `저장 개정 r${saved?.revision} · 현재 작성안과 같음`}
                </p>
                <button
                  className="hil-primary"
                  disabled={
                    locked ||
                    !writable ||
                    !dirty ||
                    !draft.name.trim() ||
                    !!parameterError ||
                    !!timingError ||
                    !!profileError
                  }
                  onClick={() => void save()}
                >
                  <Save size={15} />
                  {busy === "시험 저장"
                    ? "저장 중…"
                    : saved
                      ? "새 개정 저장"
                      : "시험 저장"}
                </button>
                <div className="hil-beta-engine">
                  <strong>{engineStatus.title}</strong>
                  <p>{engineStatus.description}</p>
                  {capabilities.error && <p>{capabilities.error}</p>}
                  <button
                    disabled={locked || capabilities.loading}
                    onClick={refreshConnectionSettings}
                  >
                    연결 설정 새로고침
                  </button>
                </div>
                {preview && saved && hilTestPreviewMatches(preview, saved) ? (
                  <>
                    <small>
                      r{saved.revision} 고정 입력 확인됨 · A/B를 새로
                      실행합니다.
                    </small>
                    <button
                      className="hil-primary"
                      disabled={
                        locked || !writable || !savedCurrent || !configured
                      }
                      onClick={() => void execute()}
                    >
                      저장 개정 A/B 실행
                      <ArrowRight size={14} />
                    </button>
                  </>
                ) : (
                  <button
                    disabled={
                      locked || !writable || !savedCurrent || !configured
                    }
                    onClick={() => void prepare()}
                  >
                    {busy === "저장 개정 실행 확인"
                      ? "확인 중…"
                      : "저장 개정 실행 확인"}
                  </button>
                )}
                <details>
                  <summary>고정 조건 · source</summary>
                  <p>
                    프리셋 {draft.preset_id} · binding r
                    {draft.binding_ref.revision}
                    <br />
                    {draft.dut_profiles
                      ? "18초 · 같은 고장 / DUT 프로필 A·B"
                      : "18초 · 정상 A / 고장 B"}
                  </p>
                  {saved && (
                    <p>
                      시나리오 {saved.source.scenario_ref.id} · r
                      {saved.source.scenario_ref.revision}
                      <br />
                      {saved.source.adapter_version}
                      <br />
                      seed {saved.source.seed}
                    </p>
                  )}
                  <code>{draft.binding_ref.sha256}</code>
                </details>
              </div>
            )}
            {tab === "history" && (
              <div className="hil-beta-form">
                <h2>저장 개정의 실행</h2>
                <p>
                  {saved
                    ? `${saved.name} · r${saved.revision}`
                    : "시험을 먼저 저장하거나 여세요."}
                </p>
                {executions.loading && saved && (
                  <p role="status">실행 이력을 읽는 중…</p>
                )}
                {executions.error && (
                  <p className="hil-error" role="alert">
                    {executions.error}
                  </p>
                )}
                {executions.data?.items.map((item) => (
                  <button
                    className={`hil-beta-history-row ${shownId === item.execution.id ? "selected" : ""}`}
                    key={item.execution.id}
                    onClick={() => openResult(item.execution.id)}
                  >
                    <strong>{hilStatus(item.execution.status)}</strong>
                    <small>
                      {new Date(item.execution.created_at).toLocaleString(
                        "ko-KR",
                      )}{" "}
                      · r{item.test_ref.revision}
                    </small>
                    <code>{item.execution.id}</code>
                  </button>
                ))}
                {saved &&
                  !executions.loading &&
                  !executions.data?.items.length && (
                    <p>연결된 실행이 없습니다.</p>
                  )}
                {saved && (
                  <details open>
                    <summary>기존 결과 연결</summary>
                    <p>이 개정과 고정 입력이 일치하는 결과만 연결합니다.</p>
                    <select
                      aria-label="연결할 기존 HIL 실행"
                      value={existingId}
                      disabled={!!busy || !writable}
                      onChange={(event) => setExistingId(event.target.value)}
                    >
                      <option value="">기존 HIL 실행 선택</option>
                      {retained.data?.items
                        .filter((item) => item.experiment_kind === "hil_demo")
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name} · {hilStatus(item.status)} ·{" "}
                            {item.id.slice(-8)}
                          </option>
                        ))}
                    </select>
                    {retained.error && (
                      <p className="hil-error">{retained.error}</p>
                    )}
                    <button
                      disabled={!existingId || !!busy || !writable}
                      onClick={() => void attach()}
                    >
                      선택 결과 연결
                    </button>
                    {retained.data?.next_before_seq && (
                      <small>최근 100건의 실행 중 HIL 결과를 표시합니다.</small>
                    )}
                  </details>
                )}
              </div>
            )}
            {tab === "observations" && (
              <div className="hil-beta-form">
                <h2>무엇을 관찰했나요?</h2>
                <p>
                  {shownId
                    ? `${shownId.slice(-12)} · 응답 ${cursor.toFixed(2)} s`
                    : "먼저 실행 결과를 여세요."}
                </p>
                {!linked && (
                  <p className="hil-beta-draft-state">
                    관찰할 결과를 이 시험 개정의 실행 이력에 먼저 연결하세요.
                  </p>
                )}
                <form onSubmit={(event) => void saveObservation(event)}>
                  <label>
                    종류
                    <select
                      aria-label="HIL 관찰 종류"
                      value={observation.kind}
                      onChange={(event) =>
                        setObservation({
                          ...observation,
                          kind: event.target.value,
                        })
                      }
                    >
                      <option value="observation">관찰</option>
                      <option value="issue">문제 제보</option>
                    </select>
                  </label>
                  <label>
                    대상
                    <select
                      aria-label="HIL 관찰 대상"
                      value={observation.side}
                      onChange={(event) =>
                        setObservation({
                          ...observation,
                          side: event.target.value,
                        })
                      }
                    >
                      <option value="both">A/B 함께</option>
                      <option value="baseline">A 결과</option>
                      <option value="faulted">B 결과</option>
                    </select>
                  </label>
                  <label>
                    관찰 시점 · 응답 초
                    <input
                      aria-label="HIL 관찰 시점"
                      type="number"
                      min={0}
                      max={18}
                      step={0.05}
                      value={cursor}
                      onChange={(event) => {
                        const next = Number(event.target.value);
                        if (Number.isFinite(next)) {
                          setCursor(next);
                          setSeekRequest({ timeS: next, nonce: Date.now() });
                        }
                      }}
                    />
                  </label>
                  <label>
                    제목
                    <input
                      aria-label="HIL 관찰 제목"
                      value={observation.title}
                      maxLength={180}
                      onChange={(event) =>
                        setObservation({
                          ...observation,
                          title: event.target.value,
                        })
                      }
                    />
                  </label>
                  <label>
                    관찰 메모
                    <textarea
                      aria-label="HIL 관찰 메모"
                      rows={3}
                      maxLength={4000}
                      value={observation.note}
                      onChange={(event) =>
                        setObservation({
                          ...observation,
                          note: event.target.value,
                        })
                      }
                    />
                  </label>
                  <button
                    className="hil-primary"
                    type="submit"
                    disabled={
                      !!busy ||
                      !writable ||
                      !linked ||
                      !terminalResult ||
                      !observation.title.trim() ||
                      !observation.note.trim()
                    }
                  >
                    현재 결과·시점에 관찰 저장
                  </button>
                  <small>사용자 관찰이며 안전 통과 판정이 아닙니다.</small>
                </form>
                {observations.error && (
                  <p className="hil-error">{observations.error}</p>
                )}
                {observations.data?.items.map((item) => (
                  <button
                    className="hil-beta-history-row"
                    key={item.id}
                    onClick={() => {
                      setCursor(item.time_s);
                      setSeekRequest({ timeS: item.time_s, nonce: Date.now() });
                      updateQuery(
                        hilResultQuery(item.execution_id, item.time_s),
                      );
                    }}
                  >
                    <strong>{item.title}</strong>
                    <small>
                      {item.time_s.toFixed(2)} s ·{" "}
                      {item.side === "both"
                        ? "A/B"
                        : item.side === "baseline"
                          ? "A"
                          : "B"}{" "}
                      · {item.kind === "issue" ? "문제 제보" : "관찰"}
                    </small>
                    <p>{item.note}</p>
                    {Object.entries(item.samples).map(([side, sample]) => (
                      <small key={side}>
                        {side === "baseline" ? "A" : "B"}:{" "}
                        {sample.status === "observed"
                          ? `원본 응답 ${sample.time_s?.toFixed(2)} s`
                          : `표본 없음 · ${sample.reason ?? "미보존"}`}
                      </small>
                    ))}
                  </button>
                ))}
              </div>
            )}
            {!draft && !presets.loading && tab === "settings" && (
              <div className="hil-beta-form">
                <p>
                  연결된 프리셋이 없습니다. 운영자가 이 프로젝트의 시나리오
                  입력을 먼저 연결해야 합니다.
                </p>
              </div>
            )}
            {!writable && identity.data && (
              <p className="hil-beta-readonly">
                조회 전용 · 저장과 실행에는 Engineer 권한이 필요합니다.
              </p>
            )}
          </aside>
          <div className="hil-beta-result">
            {runId && (
              <div className="hil-run-status" role="status">
                <div>
                  <strong>
                    {run ? hilStatus(run.status) : "실행 상태 확인 중"}
                  </strong>
                  <small>{runId}</small>
                  {run?.error && <p>{hilErrorMessage(run.error)}</p>}
                  {run?.outcome_uncertain && (
                    <p className="hil-error">
                      실행 결과가 불확실합니다. 원본 실행 기록을 확인하세요.
                    </p>
                  )}
                </div>
                {run?.status === "completed" ? (
                  <button onClick={() => openResult(run.id)}>
                    새 결과 열기
                    <ArrowRight size={14} />
                  </button>
                ) : (
                  <button
                    disabled={!!busy}
                    onClick={() => setPollVersion((v) => v + 1)}
                  >
                    상태 확인
                  </button>
                )}
                {activeRun && writable && run?.cancellation_supported && (
                  <button
                    disabled={!!busy}
                    onClick={() =>
                      void perform("실행 취소", async () => {
                        await api(
                          `/experiments/executions/${encodeURIComponent(runId)}/cancel`,
                          { method: "POST" },
                        );
                        setPollVersion((v) => v + 1);
                      })
                    }
                  >
                    취소
                  </button>
                )}
              </div>
            )}
            {shownId && (
              <div className="hil-result-origin">
                <span>
                  선택한 실제 실행 · {shownId.slice(-12)} · 현재 작성안과 별도로
                  보존
                </span>
                <button onClick={() => setTab("observations")}>
                  이 시점 관찰
                </button>
              </div>
            )}
            {displayed.error && (
              <p className="hil-error" role="alert">
                {displayed.error}
              </p>
            )}
            {displayed.loading && shownId && (
              <p role="status">저장 결과를 읽는 중…</p>
            )}
            {selectedExecution?.status === "completed" && (
              <HilPresenter
                key={selectedExecution.id}
                execution={selectedExecution}
                presetId={resultPreset}
                onCursorChange={onCursor}
                seekRequest={seekRequest}
              />
            )}{" "}
            {selectedExecution && selectedExecution.status !== "completed" && (
              <div className="hil-empty">
                <h2>{hilStatus(selectedExecution.status)}</h2>
                {selectedExecution.outcome_uncertain && (
                  <p className="hil-error">
                    실행 결과가 불확실합니다. 원본 실행 기록을 확인하세요.
                  </p>
                )}
                <p>
                  {selectedExecution.error ??
                    "완료된 원본 영상이 없습니다. 실행 기록과 관찰을 보존할 수 있습니다."}
                </p>
                <a
                  href={`#viewers?project=${encodeURIComponent(project)}&execution=${encodeURIComponent(selectedExecution.id)}`}
                >
                  실행 기록 열기
                </a>
              </div>
            )}
            {!shownId && (
              <div className="hil-empty">
                <h2>저장 시험에서 결과 조사까지</h2>
                <p>
                  시험 이름·목적·고장 값을 저장하세요.
                  <br />
                  실행 이력에서 같은 입력의 기존 결과를 연결하거나
                  <br />
                  실행 환경이 준비되면 새 A/B를 제출할 수 있습니다.
                </p>
                <button onClick={() => setTab("history")}>
                  실행 이력 보기
                  <ArrowRight size={14} />
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
