import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowRight,
  Copy,
  Download,
  ListOrdered,
  Plus,
  RefreshCw,
  Save,
  X,
} from "lucide-react";
import { api, ApiError, authHeaders, useResource } from "../../api";
import { hilErrorMessage } from "./demo-contract";
import type { HilTest, HilTestRef } from "./beta-contract";
import {
  hilBatchAcceptSnapshot,
  hilBatchActionNotice,
  hilBatchActive,
  hilBatchAttemptUncertain,
  hilBatchCounts,
  hilBatchDraftError,
  hilBatchLastAttempt,
  hilBatchMove,
  hilBatchMutationKey,
  hilBatchPreviewMatches,
  hilBatchRefKey,
  hilBatchRowState,
  hilBatchRequestRejected,
  hilBatchSaveBody,
  hilBatchStatus,
  hilBatchTestDescription,
  restoreHilBatchMutation,
} from "./batch-contract";
import type {
  HilBatch,
  HilBatchAttempt,
  HilBatchDraft,
  HilBatchMutation,
  HilBatchPreview,
  HilBatchResult,
} from "./batch-contract";
import "./hil-batches.css";
import { hilSceneTitle } from "./scene-profile";

const errorText = (value: unknown) =>
  value instanceof Error ? value.message : String(value);
const query = (project: string) => new URLSearchParams({ project_id: project });
const emptyDraft = (): HilBatchDraft => ({ name: "", note: "", tests: [] });
function rememberSelection(id: string | null) {
  const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
  if (id) params.set("batch", id);
  else params.delete("batch");
  history.replaceState(
    null,
    "",
    `${location.pathname}${location.search}#hil-sim?${params}`,
  );
}
function TestLabel({ test }: { test: HilTest }) {
  const details = hilBatchTestDescription(test);
  return (
    <div className="hil-batch-test-label">
      <strong>
        {test.name} <span>r{test.revision}</span>
      </strong>
      <small>
        {details.fault} · {details.interval}
      </small>
      <small>{details.profiles}</small>
      <small>
        장면 · {hilSceneTitle(test.scene_profile)}
        {test.scene_profile ? ` · r${test.scene_profile.revision}` : ""}
      </small>
    </div>
  );
}
function ResultSummary({
  result,
  test,
  open,
  disabled,
}: {
  result: HilBatchResult;
  test: HilTest;
  open: (timeS: number) => void;
  disabled: boolean;
}) {
  return (
    <div className="hil-batch-result-summary">
      <span>보존된 응답 로그 요약</span>
      <table>
        <thead>
          <tr>
            <th>결과</th>
            <th>첫 열화</th>
            <th>첫 추적 복구</th>
            <th>최대 입력 지연</th>
            <th>최대 제동</th>
          </tr>
        </thead>
        <tbody>
          {(["baseline", "faulted"] as const).map((side) => {
            const value = result.sides[side];
            const label = side === "baseline" ? "A" : "B";
            return (
              <tr key={side}>
                <th>
                  {label}
                  <small>
                    {test.dut_profiles
                      ? `${test.dut_profiles[side].name} r${test.dut_profiles[side].revision}`
                      : side === "baseline"
                        ? "정상"
                        : "고장"}
                  </small>
                </th>
                <td>
                  {value.first_degraded_s === null ? (
                    "관측 없음"
                  ) : (
                    <button
                      disabled={disabled}
                      onClick={() => open(value.first_degraded_s!)}
                    >
                      {value.first_degraded_s.toFixed(2)} s
                    </button>
                  )}
                </td>
                <td>
                  {value.first_recovered_s === null ? (
                    "관측 없음"
                  ) : (
                    <button
                      disabled={disabled}
                      onClick={() => open(value.first_recovered_s!)}
                    >
                      {value.first_recovered_s.toFixed(2)} s
                    </button>
                  )}
                </td>
                <td>{value.peak_input_age_ms.toFixed(0)} ms</td>
                <td>{(value.peak_brake * 100).toFixed(0)}%</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <small>
        열화 후 tracking으로 돌아온 첫 응답 시점 · A{" "}
        {result.sides.baseline.response_count} / B{" "}
        {result.sides.faulted.response_count}개 응답 · 사용자 조사용, 안전 판정
        아님
      </small>
    </div>
  );
}
export default function HilBatches({
  project,
  principal,
  writable,
  tests,
  reloadTests,
  blockedReason,
  onOpenResult,
}: {
  project: string;
  principal?: string;
  writable: boolean;
  tests: HilTest[];
  reloadTests: () => void;
  blockedReason: string;
  onOpenResult: (ref: HilTestRef, executionId: string, timeS?: number) => void;
}) {
  const library = useResource<{
    items: HilBatch[];
    next_offset: number | null;
  }>(`/hil-sim/batches?${query(project)}&limit=100`);
  const [selectedId, setSelectedId] = useState<string | null>(() =>
    new URLSearchParams(location.hash.split("?")[1] ?? "").get("batch"),
  );
  const [batch, setBatch] = useState<HilBatch | null>(null);
  const [draft, setDraft] = useState(emptyDraft);
  const [preview, setPreview] = useState<HilBatchPreview | null>(null);
  const [pending, setPending] = useState<HilBatchMutation | null>(null);
  const [testId, setTestId] = useState(""),
    [testRevision, setTestRevision] = useState(1);
  const [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0),
    [loading, setLoading] = useState(false);
  const [actionNotice, setActionNotice] = useState<{
    id: string;
    action: HilBatchMutation["action"];
  } | null>(null);
  const shownNotice =
    actionNotice && batch?.id === actionNotice.id
      ? hilBatchActionNotice(actionNotice.action, batch.status)
      : notice;
  const performing = useRef(false),
    restored = useRef(false),
    mounted = useRef(true);
  const locked = !!busy || !!pending;
  const counts = batch ? hilBatchCounts(batch) : null;
  const validPreview =
    !!batch && !!preview && hilBatchPreviewMatches(preview, batch);
  const chosenTest = tests.find((test) => test.id === testId);
  const draftError = hilBatchDraftError(draft);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!principal || restored.current) return;
    restored.current = true;
    const raw = sessionStorage.getItem(hilBatchMutationKey(project, principal));
    const value = restoreHilBatchMutation(raw, project, principal);
    setPending(value);
    if (raw && !value)
      setError(
        "이전 순차 실행 요청을 복원하지 못했습니다. 저장 목록과 원본 실행 기록을 확인하세요.",
      );
  }, [principal, project]);
  useEffect(() => {
    if (!selectedId) {
      setBatch(null);
      setLoading(false);
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true);
    const poll = async () => {
      try {
        const value = hilBatchAcceptSnapshot(
          null,
          await api<HilBatch>(
            `/hil-sim/batches/${encodeURIComponent(selectedId)}?${query(project)}`,
          ),
          project,
        );
        if (!active) return;
        setBatch((current) => hilBatchAcceptSnapshot(current, value, project));
        if (hilBatchActive(value)) timer = setTimeout(() => void poll(), 2000);
      } catch (reason) {
        if (active) {
          setError(errorText(reason));
          timer = setTimeout(() => void poll(), 5000);
        }
      } finally {
        if (active) setLoading(false);
      }
    };
    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [selectedId, project, refresh]);

  async function perform(stage: string, action: () => Promise<void>) {
    if (performing.current) return;
    performing.current = true;
    setBusy(stage);
    setError("");
    setNotice("");
    setActionNotice(null);
    try {
      await action();
    } catch (reason) {
      if (mounted.current) setError(errorText(reason));
    } finally {
      performing.current = false;
      if (mounted.current) setBusy("");
    }
  }
  function remember(value: HilBatchMutation | null) {
    if (!principal)
      throw new Error("계정 식별 정보를 확인한 뒤 다시 시도하세요.");
    const key = hilBatchMutationKey(project, principal);
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    else sessionStorage.removeItem(key);
    if (mounted.current) setPending(value);
  }
  async function sendMutation(value: HilBatchMutation) {
    remember(value);
    try {
      const result = hilBatchAcceptSnapshot(
        null,
        await api<HilBatch>(value.path, {
          method: "POST",
          headers: { "Idempotency-Key": value.key },
          body: value.body,
        }),
        project,
      );
      remember(null);
      if (!mounted.current) return;
      setBatch((current) => hilBatchAcceptSnapshot(current, result, project));
      setSelectedId(result.id);
      rememberSelection(result.id);
      setPreview(null);
      setRefresh((current) => current + 1);
      library.reload();
      setActionNotice({ id: result.id, action: value.action });
    } catch (reason) {
      // A definite client rejection created no new action; unknown network/server outcomes keep the exact request key.
      if (
        reason instanceof ApiError &&
        hilBatchRequestRejected(reason.status)
      ) {
        remember(null);
        setPreview(null);
        setRefresh((current) => current + 1);
      }
      throw reason;
    }
  }
  async function mutate(action: HilBatchMutation["action"]) {
    if (
      !principal ||
      !writable ||
      locked ||
      (blockedReason && ["start", "resume"].includes(action))
    )
      return;
    if (action === "save" && draftError) return;
    if (action !== "save" && !batch) return;
    if (["start", "resume"].includes(action) && !validPreview) return;
    const body =
      action === "save"
        ? hilBatchSaveBody(project, draft)
        : {
            project_id: project,
            expected_revision: batch!.revision,
            ...(["start", "resume"].includes(action)
              ? { preview_hash: preview!.preview_hash }
              : {}),
          };
    await perform(
      action === "save"
        ? "실행 목록 저장"
        : action === "cancel"
          ? "중단 요청"
          : "순차 실행 접수",
      () =>
        sendMutation({
          version: 1,
          project,
          principal,
          action,
          path:
            action === "save"
              ? "/hil-sim/batches"
              : `/hil-sim/batches/${encodeURIComponent(batch!.id)}/${action}`,
          body: JSON.stringify(body),
          key: `hil-batch-${action}-${crypto.randomUUID()}`,
        }),
    );
  }
  async function prepare() {
    if (!batch || locked || !writable || blockedReason) return;
    await perform("엔진 준비 확인", async () => {
      const value = await api<HilBatchPreview>(
        `/hil-sim/batches/${encodeURIComponent(batch.id)}/preview`,
        {
          method: "POST",
          body: JSON.stringify({
            project_id: project,
            expected_revision: batch.revision,
          }),
        },
      );
      if (!hilBatchPreviewMatches(value, batch))
        throw new Error(
          "확인한 시험 개정과 순차 실행 목록이 다릅니다. 목록 상태를 새로고침하세요.",
        );
      setPreview(value);
      setNotice(
        "고정 시험의 엔진 준비를 확인했습니다. 아직 실행을 제출하지 않았습니다.",
      );
    });
  }
  function open(id: string | null) {
    setSelectedId(id);
    setBatch(null);
    setPreview(null);
    setError("");
    setNotice("");
    setActionNotice(null);
    rememberSelection(id);
    setRefresh((current) => current + 1);
  }
  async function addTest() {
    if (!chosenTest || draft.tests.length >= 5 || locked) return;
    await perform("시험 개정 추가", async () => {
      const value = await api<HilTest>(
        `/hil-sim/tests/${encodeURIComponent(chosenTest.id)}?${query(project)}&revision=${testRevision}`,
      );
      if (
        value.project_id !== project ||
        value.id !== chosenTest.id ||
        value.revision !== testRevision
      )
        throw new Error("요청한 시험 개정과 응답이 다릅니다.");
      if (
        draft.tests.some(
          (item) => hilBatchRefKey(item) === hilBatchRefKey(value),
        )
      )
        throw new Error(
          "이 시험 개정은 이미 목록에 있습니다. 다른 개정을 선택할 수 있습니다.",
        );
      if (mounted.current)
        setDraft((current) => ({
          ...current,
          tests: [...current.tests, value],
        }));
    });
  }
  async function exportBatch() {
    if (!batch) return;
    await perform("순차 실행 내보내기", async () => {
      const response = await fetch(
        `/api/v1/hil-sim/batches/${encodeURIComponent(batch.id)}/export?${query(project)}`,
        { headers: authHeaders() },
      );
      if (!response.ok) throw new Error(`내보내기 실패 (${response.status})`);
      const url = URL.createObjectURL(await response.blob()),
        anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${batch.id}.json`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("고정 시험·실행 시도·관찰을 포함한 JSON을 내보냈습니다.");
    });
  }
  const resultButton = (ref: HilTestRef, attempt: HilBatchAttempt) =>
    attempt.execution_id && (
      <button
        type="button"
        disabled={!!blockedReason || !!busy}
        onClick={() => onOpenResult(ref, attempt.execution_id!)}
      >
        {attempt.execution?.status === "completed" ||
        attempt.status === "completed"
          ? "A/B · 관찰 열기"
          : "실행 기록 · 관찰"}
        <ArrowRight size={13} />
      </button>
    );

  return (
    <section className="hil-batches" aria-label="HIL 순차 실행">
      <div className="hil-batch-library">
        <label>
          저장 순차 실행
          <select
            aria-label="저장된 HIL 순차 실행"
            value={selectedId ?? ""}
            disabled={locked}
            onChange={(event) => open(event.target.value || null)}
          >
            <option value="">새 실행 목록</option>
            {library.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} ·{" "}
                {hilBatchStatus(
                  batch?.id === item.id ? batch.status : item.status,
                )}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={locked}
          onClick={() => {
            open(null);
            setDraft(emptyDraft());
          }}
        >
          <Plus size={14} />새 목록
        </button>
        <button
          aria-label="순차 실행 상태 새로고침"
          disabled={!!busy}
          onClick={() => {
            library.reload();
            reloadTests();
            setRefresh((current) => current + 1);
          }}
        >
          <RefreshCw size={14} />
          상태 확인
        </button>
      </div>
      {library.error && (
        <p className="hil-error" role="alert">
          {library.error}
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
      {shownNotice && (
        <p className="hil-beta-notice" role="status">
          {shownNotice}
        </p>
      )}
      {blockedReason && <p className="hil-beta-recovery">{blockedReason}</p>}
      {pending && !busy && (
        <div className="hil-beta-recovery" role="status">
          <strong>이전 요청의 접수 결과를 확인해야 합니다.</strong>
          <span>새 요청을 만들지 않고 같은 요청으로 확인합니다.</span>
          <button
            disabled={!!busy || !writable}
            onClick={() =>
              void perform("이전 접수 확인", () => sendMutation(pending))
            }
          >
            같은 요청으로 접수 확인
          </button>
        </div>
      )}
      {loading && !batch && <p role="status">순차 실행 기록을 읽는 중…</p>}
      {!selectedId && (
        <div className="hil-batch-authoring">
          <aside className="hil-batch-form">
            <span className="hil-eyebrow">PIN 3–5 CONDITIONS</span>
            <h2>하나씩 실행할 시험 선택</h2>
            <p>
              저장된 개정과 순서를 고정합니다. 시험이나 DUT 프로필의 최신
              개정으로 자동 변경되지 않습니다.
            </p>
            <label>
              순차 실행 이름
              <input
                aria-label="HIL 순차 실행 이름"
                value={draft.name}
                maxLength={180}
                disabled={locked || !writable}
                onChange={(event) =>
                  setDraft({ ...draft, name: event.target.value })
                }
              />
            </label>
            <label>
              목적
              <textarea
                aria-label="HIL 순차 실행 목적"
                rows={3}
                maxLength={2000}
                value={draft.note}
                disabled={locked || !writable}
                onChange={(event) =>
                  setDraft({ ...draft, note: event.target.value })
                }
              />
            </label>
            <label>
              저장 시험
              <select
                aria-label="순차 실행에 추가할 시험"
                value={testId}
                disabled={locked || !writable}
                onChange={(event) => {
                  setTestId(event.target.value);
                  setTestRevision(
                    tests.find((test) => test.id === event.target.value)
                      ?.revision ?? 1,
                  );
                }}
              >
                <option value="">시험 선택</option>
                {tests.map((test) => (
                  <option key={test.id} value={test.id}>
                    {test.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="hil-batch-add">
              <label>
                고정 개정
                <select
                  aria-label="순차 실행 시험 개정"
                  value={testRevision}
                  disabled={locked || !chosenTest || !writable}
                  onChange={(event) =>
                    setTestRevision(Number(event.target.value))
                  }
                >
                  {Array.from(
                    { length: chosenTest?.revision ?? 1 },
                    (_, index) => index + 1,
                  )
                    .reverse()
                    .map((revision) => (
                      <option key={revision} value={revision}>
                        r{revision}
                      </option>
                    ))}
                </select>
              </label>
              <button
                disabled={
                  locked || !chosenTest || !writable || draft.tests.length >= 5
                }
                onClick={() => void addTest()}
              >
                <Plus size={14} />
                목록에 추가
              </button>
            </div>
            <small>같은 시험의 서로 다른 개정도 비교할 수 있습니다.</small>
            <button
              className="hil-primary"
              disabled={locked || !writable || !principal || !!draftError}
              onClick={() => void mutate("save")}
            >
              <Save size={15} />
              {busy === "실행 목록 저장" ? "저장 중…" : "이 순서로 목록 저장"}
            </button>
            <small>
              {draftError ||
                "목록 저장은 엔진 없이 가능합니다. 실행 전 별도로 엔진을 확인합니다."}
            </small>
          </aside>
          <div className="hil-batch-order">
            <header>
              <h2>실행 순서</h2>
              <span>{draft.tests.length} / 5개 선택</span>
            </header>
            {draft.tests.length === 0 && (
              <div className="hil-batch-empty">
                <ListOrdered size={35} />
                <h3>고장 조건을 순서대로 쌓으세요.</h3>
                <p>
                  예: 250 ms 기준 정책 → 500 ms 허용 정책 → 입력 단절
                  <br />각 조건의 A/B 영상과 관찰을 별도로 보존합니다.
                </p>
              </div>
            )}
            {draft.tests.map((test, index) => (
              <article
                className="hil-batch-draft-row"
                key={hilBatchRefKey(test)}
              >
                <span className="hil-batch-number">{index + 1}</span>
                <TestLabel test={test} />
                <div className="hil-batch-order-actions">
                  <button
                    aria-label={`${index + 1}번 시험 위로`}
                    disabled={locked || index === 0 || !writable}
                    onClick={() =>
                      setDraft({
                        ...draft,
                        tests: hilBatchMove(draft.tests, index, -1),
                      })
                    }
                  >
                    <ArrowUp size={14} />
                  </button>
                  <button
                    aria-label={`${index + 1}번 시험 아래로`}
                    disabled={
                      locked || index === draft.tests.length - 1 || !writable
                    }
                    onClick={() =>
                      setDraft({
                        ...draft,
                        tests: hilBatchMove(draft.tests, index, 1),
                      })
                    }
                  >
                    <ArrowDown size={14} />
                  </button>
                  <button
                    aria-label={`${index + 1}번 시험 제거`}
                    disabled={locked || !writable}
                    onClick={() =>
                      setDraft({
                        ...draft,
                        tests: draft.tests.filter((_, i) => i !== index),
                      })
                    }
                  >
                    <X size={14} />
                  </button>
                </div>
              </article>
            ))}
          </div>
        </div>
      )}
      {batch && counts && (
        <>
          <header className="hil-batch-heading">
            <div>
              <span className="hil-eyebrow">
                SEQUENTIAL · SOFTWARE DUT / CARLA
              </span>
              <h2>{batch.name}</h2>
              <p>{batch.note || "저장된 시험을 지정 순서대로 실행합니다."}</p>
            </div>
            <div>
              <button
                disabled={locked}
                onClick={() => {
                  setDraft({
                    name: `${batch.name} · 복사`,
                    note: batch.note,
                    tests: batch.items.map((item) => item.test),
                  });
                  open(null);
                }}
              >
                <Copy size={14} />
                목록 복제
              </button>
              <button disabled={!!busy} onClick={() => void exportBatch()}>
                <Download size={14} />
                JSON 내보내기
              </button>
            </div>
          </header>
          <div className="hil-batch-summary">
            <div>
              <span className={`hil-batch-status ${batch.status}`}>
                {hilBatchStatus(batch.status)}
              </span>
              <strong>
                {counts.completed}
                <small> / {counts.total} 조건 실행 완료</small>
              </strong>
              <progress
                aria-label="HIL 순차 실행 완료 조건"
                value={counts.completed}
                max={counts.total}
              />
            </div>
            <dl>
              <div>
                <dt>미완료</dt>
                <dd>{counts.total - counts.completed}</dd>
              </div>
              <div>
                <dt>현재 실패·중단</dt>
                <dd>{counts.failed}</dd>
              </div>
              <div>
                <dt>누적 실행 시도</dt>
                <dd>{counts.attempts}</dd>
              </div>
            </dl>
            <p>
              실행 완료는 안전 통과 판정이 아닙니다. 각 실제 A/B 결과에서 반응과
              관찰을 확인하세요.
            </p>
          </div>
          {batch.error && (
            <p className="hil-error" role="alert">
              {hilErrorMessage(batch.error)}
            </p>
          )}
          {counts.uncertain > 0 && (
            <p className="hil-error">
              실행 결과가 불확실합니다. 원본 실행 기록을 확인하세요. 불확실한
              실행을 자동으로 다시 제출하지 않습니다.
            </p>
          )}
          <div className="hil-batch-controls">
            {(batch.can_start || batch.can_resume) && (
              <>
                <button
                  disabled={locked || !writable || !!blockedReason}
                  onClick={() => void prepare()}
                >
                  {busy === "엔진 준비 확인"
                    ? "엔진 준비 확인 중…"
                    : "고정 목록 · 엔진 준비 확인"}
                </button>
                <button
                  className="hil-primary"
                  disabled={
                    locked || !writable || !!blockedReason || !validPreview
                  }
                  onClick={() =>
                    void mutate(batch.can_start ? "start" : "resume")
                  }
                >
                  {batch.can_start
                    ? "이 순서로 실행 시작"
                    : "미완료 조건 다시 실행 후 이어가기"}
                  <ArrowRight size={14} />
                </button>
              </>
            )}
            {batch.can_cancel && (
              <button
                disabled={locked || !writable}
                onClick={() => void mutate("cancel")}
              >
                순차 실행 중단
              </button>
            )}
            {busy && <span role="status">{busy}</span>}
            {batch.action_reason && (
              <p>{hilErrorMessage(batch.action_reason)}</p>
            )}
          </div>
          {validPreview && (
            <div className="hil-batch-preview" role="status">
              <strong>엔진 준비 확인 완료 · {preview!.job_count}개 조건</strong>
              <span>
                확인 시점의 상태이며 시작 시 고정 입력을 다시 확인합니다.
              </span>
              <details>
                <summary>확인한 입력·실행 경계</summary>
                <code>{preview!.preview_hash}</code>
                {preview!.items.map((item) => (
                  <p key={item.index}>
                    {item.index + 1}번 · {item.execution_fidelity} ·{" "}
                    {item.resource_id}
                  </p>
                ))}
              </details>
            </div>
          )}
          <div className="hil-batch-rows">
            {batch.items.map((item) => {
              const attempt = hilBatchLastAttempt(item);
              const state = hilBatchRowState(batch, item);
              return (
                <article
                  className={`hil-batch-row ${state.status}`}
                  key={item.index}
                >
                  <div className="hil-batch-row-main">
                    <span className="hil-batch-number">{item.index + 1}</span>
                    <TestLabel test={item.test} />
                    <div className="hil-batch-row-outcome">
                      <span className={`hil-batch-status ${state.status}`}>
                        {state.summaryPending
                          ? "실행 완료 · 요약 처리 중"
                          : hilBatchStatus(state.status)}
                      </span>
                      <small>
                        {attempt
                          ? `${attempt.attempt}번째 시도`
                          : "아직 제출하지 않음"}
                      </small>
                      {attempt && resultButton(item.test_ref, attempt)}
                    </div>
                  </div>
                  {attempt?.error && (
                    <p className="hil-error">
                      {hilErrorMessage(attempt.error)}
                    </p>
                  )}
                  {state.uncertain && (
                    <p className="hil-error">
                      실행 결과가 불확실합니다. 원본 실행 기록을 확인하세요.
                    </p>
                  )}
                  {attempt?.result && !state.uncertain && (
                    <ResultSummary
                      result={attempt.result}
                      test={item.test}
                      disabled={!!blockedReason || !!busy}
                      open={(timeS) =>
                        onOpenResult(
                          item.test_ref,
                          attempt.execution_id!,
                          timeS,
                        )
                      }
                    />
                  )}
                  <details>
                    <summary>
                      고정 입력 · {item.attempts.length}개 시도 기록
                    </summary>
                    <p>
                      {item.test_ref.id} · r{item.test_ref.revision}
                    </p>
                    <code>{item.test_ref.sha256}</code>
                    {item.attempts.map((record) => (
                      <div className="hil-batch-attempt" key={record.attempt}>
                        <span>
                          {record.attempt}번째 · {hilBatchStatus(record.status)}
                          {hilBatchAttemptUncertain(record)
                            ? " · 결과 불확실"
                            : ""}
                        </span>
                        <code>{record.execution_id ?? "실행 접수 전"}</code>
                        {record.error && <p>{hilErrorMessage(record.error)}</p>}
                        {resultButton(item.test_ref, record)}
                      </div>
                    ))}
                  </details>
                </article>
              );
            })}
          </div>
          <details className="hil-batch-provenance">
            <summary>목록 식별 정보</summary>
            <p>
              {batch.id} · 상태 개정 {batch.revision}
            </p>
            <small>고정 정의 SHA-256</small>
            <code>{batch.definition_sha256}</code>
            <small>현재 상태 SHA-256</small>
            <code>{batch.sha256}</code>
          </details>
        </>
      )}
      {!writable && (
        <p className="hil-beta-readonly">
          조회 전용 · 저장과 실행에는 Engineer 권한이 필요합니다.
        </p>
      )}
    </section>
  );
}
