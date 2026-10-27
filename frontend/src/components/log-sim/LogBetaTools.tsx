import { useEffect, useRef, useState } from "react";
import { api, authHeaders, useResource } from "../../api";
import "./log-beta.css";
import { useLogBeta } from "./LogGuidedBeta";
export type ComparisonRef = { id: string; revision: number; sha256: string };
type Knobs = {
  cruise_speed_mps: number;
  time_headway_s: number;
  stop_buffer_m: number;
};
type Recipe = ComparisonRef & {
  name: string;
  profiles: {
    baseline: { controller: Knobs };
    candidate: { controller: Knobs };
  };
};
type Upload = {
  id: string;
  name: string;
  channels: {
    id: number;
    topic: string;
    schema_id: number;
    message_count: number;
  }[];
  schemas: { id: number; name: string }[];
};
type Inspection = {
  preview_hash: string;
  frame_count: number;
  duration_s: number;
  camera_available: boolean;
  world_frame: string;
};
const pickKnobs = (k: Knobs): Knobs => ({
  cruise_speed_mps: k.cruise_speed_mps,
  time_headway_s: k.time_headway_s,
  stop_buffer_m: k.stop_buffer_m,
});
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
export async function downloadLog(path: string, name: string) {
  const response = await fetch("/api/v1" + path, { headers: authHeaders() });
  if (!response.ok) throw new Error(`내보내기 실패 (${response.status})`);
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export default function LogBetaTools({
  project,
  pendingExecution = false,
  writable,
  onRegistered,
  onRecipe,
  onDirty,
}: {
  project: string;
  pendingExecution?: boolean;
  writable: boolean;
  onRegistered: (id: string) => void;
  onRecipe: (ref: ComparisonRef | null) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const beta = useLogBeta();
  const locked = !!beta?.task?.batch_id || pendingExecution;
  const [restoring, setRestoring] = useState(false);
  const uploads = useResource<{ items: Upload[] }>(
      `/log-sim/uploads?project_id=${encodeURIComponent(project)}`,
    ),
    recipes = useResource<{ items: Recipe[] }>(
      `/log-sim/comparison-settings?project_id=${encodeURIComponent(project)}`,
    );
  const packs = useResource<{
    items: {
      id: string;
      title: string;
      question: string;
      edit: string;
      metric: string;
      case: string;
      status: string;
      binding: null | { source_id: string; note: string };
    }[];
  }>(`/log-sim/packs?project_id=${encodeURIComponent(project)}`);
  const [upload, setUpload] = useState<Upload | null>(null),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const [title, setTitle] = useState("내 로그 조사"),
    [ego, setEgo] = useState(""),
    [objects, setObjects] = useState(""),
    [camera, setCamera] = useState(""),
    [first, setFirst] = useState(0),
    [count, setCount] = useState(12),
    [inspection, setInspection] = useState<{
      signature: string;
      value: Inspection;
    } | null>(null);
  const [recipe, setRecipe] = useState<Recipe | null>(null),
    [name, setName] = useState("내 A/B 비교"),
    [a, setA] = useState<Knobs>({
      cruise_speed_mps: 8,
      time_headway_s: 1.5,
      stop_buffer_m: 2,
    }),
    [b, setB] = useState<Knobs>({
      cruise_speed_mps: 8,
      time_headway_s: 3,
      stop_buffer_m: 2,
    }),
    [dirty, setDirty] = useState(false);
  const pending = useRef({ body: "", key: "" });
  const selection = {
    project_id: project,
    recording_id: upload?.id,
    title,
    ego_channel_id: Number(ego),
    objects_channel_id: Number(objects),
    camera_channel_id: camera === "" ? null : Number(camera),
    first_sample: first,
    sample_count: count,
  };
  const signature = JSON.stringify(selection),
    preview = inspection?.signature === signature ? inspection.value : null;
  useEffect(() => {
    onDirty(dirty || restoring || (!!beta?.task && !recipe));
  }, [dirty, restoring, beta?.task?.id, recipe, onDirty]);
  const pinned = beta?.task?.comparison_ref;
  useEffect(() => {
    if (!pinned) return;
    let active = true; setRestoring(true);
    api<Recipe>(`/log-sim/comparison-settings/${encodeURIComponent(pinned.id)}?${new URLSearchParams({ project_id: project, revision: String(pinned.revision) })}`).then(value => {
      if (!active) return;
      if (value.sha256 !== pinned.sha256) throw new Error("저장된 비교 설정 hash가 일치하지 않습니다.");
      setRecipe(value); setName(value.name); setA(pickKnobs(value.profiles.baseline.controller)); setB(pickKnobs(value.profiles.candidate.controller)); setDirty(false); onRecipe(pinned);
    }).catch(e => { if (active) { setError(errorText(e)); setDirty(true); onDirty(true); } }).finally(() => { if (active) setRestoring(false); });
    return () => { active = false; };
  }, [project, pinned?.id, pinned?.revision, pinned?.sha256]);
  function choose(value: Upload) {
    setUpload(value);
    setInspection(null);
    setTitle(value.name);
    setFirst(0);
    const schema = (id: number) => value.schemas.find((s) => s.id === id)?.name;
    const e = value.channels.find(
        (c) => schema(c.schema_id) === "axiom.PlanningEgo.v1",
      ),
      o = value.channels.find(
        (c) => schema(c.schema_id) === "axiom.ObjectTracks.v1",
      ),
      c = value.channels.find(
        (c) => schema(c.schema_id) === "foxglove.CompressedImage",
      );
    setEgo(e ? String(e.id) : "");
    setObjects(o ? String(o.id) : "");
    setCamera(c ? String(c.id) : "");
    setCount(Math.min(40, e?.message_count ?? 12));
  }
  async function run(label: string, task: () => Promise<void>) {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      await task();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  }
  async function uploadFile(file: File) {
    await run("upload", async () => {
      if (file.size > 128 * 1024 * 1024)
        throw new Error(
          "파일당 최대 128 MiB입니다. 원본에서 더 짧은 MCAP를 준비하세요.",
        );
      const value = await api<Upload>(
        `/log-sim/uploads?${new URLSearchParams({ project_id: project, name: file.name })}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
        },
      );
      choose(value);
      uploads.reload();
      setMessage("업로드 완료. 원본 채널과 분석 구간을 확인하세요.");
    });
  }
  async function saveRecipe() {
    await run("settings", async () => {
      const payload = JSON.stringify({
        project_id: project,
        id: recipe?.id ?? null,
        expected_revision: recipe?.revision ?? 0,
        name,
        baseline: a,
        candidate: b,
      });
      if (pending.current.body !== payload)
        pending.current = { body: payload, key: crypto.randomUUID() };
      const saved = await api<Recipe>("/log-sim/comparison-settings", {
        method: "POST",
        body: payload,
        headers: { "Idempotency-Key": pending.current.key },
      });
      setRecipe(saved);
      setDirty(false);
      onRecipe({
        id: saved.id,
        revision: saved.revision,
        sha256: saved.sha256,
      });
      if (beta?.task) await beta.progress({ comparison_ref: { id: saved.id, revision: saved.revision, sha256: saved.sha256 } });
      recipes.reload();
      setMessage(
        `비교 설정 r${saved.revision} 저장. 다음 배치에 이 설정이 적용됩니다.`,
      );
    });
  }
  async function chooseRecipe(id: string) {
    const value = recipes.data?.items.find((x) => `${x.id}@${x.revision}` === id) ?? null;
    setRecipe(value);
    setDirty(false);
    if (value) {
      setName(value.name);
      setA(pickKnobs(value.profiles.baseline.controller));
      setB(pickKnobs(value.profiles.candidate.controller));
      const ref = { id: value.id, revision: value.revision, sha256: value.sha256 };
      onRecipe(ref);
      if (beta?.task) await run("settings", () => beta.progress({ comparison_ref: ref }));
    } else {
      setName("내 A/B 비교");
      setA({ cruise_speed_mps: 8, time_headway_s: 1.5, stop_buffer_m: 2 });
      setB({ cruise_speed_mps: 8, time_headway_s: 3, stop_buffer_m: 2 });
      onRecipe(null);
    }
  }
  return (
    <section className="log-beta-tools" aria-label="Log Sim beta tools">
      <header><span>START WITH ONE QUESTION</span><h2>대표 로그에서 비교를 시작하세요.</h2><p>질문 하나를 선택하고 조건 변경부터 Case 검토까지 이어갑니다.</p></header>
      <div className="log-beta-packs" aria-label="Log Sim beta packs">
        {[...(packs.data?.items ?? [])].sort((a, b) => Number(b.id === "following-gap") - Number(a.id === "following-gap")).map(p => <article key={p.id} className={p.id === "following-gap" ? "recommended" : ""}>
          <h3>{p.title} {p.id === "following-gap" && <small>첫 추천</small>}</h3><p>{p.question}</p><small>변경: {p.edit}<br />관찰: {p.metric}</small><p>{p.case}</p>
          <button className="button secondary" aria-label={`Start Log Sim beta pack ${p.id}`} disabled={!writable || !!beta?.busy || !!beta?.loading || (!p.binding && p.id !== "input-errors")} onClick={() => void beta?.start(p.id)}>{beta?.tasks.some(t => t.pack_id === p.id && t.status !== "completed") ? "이어서 하기" : "시작"}</button>
          {!p.binding && p.id !== "input-errors" && <small>준비 필요 · 운영자가 이 프로젝트에 대표 원본을 연결해야 합니다.</small>}
          {p.id === "input-errors" && <small>작성한 정상·누락 오류 MCAP 예제로 진행합니다.</small>}
          {p.binding && <details><summary>원본 근거</summary><p>{p.binding.note}</p></details>}
        </article>)}
      </div>
      <details id="log-beta-upload" open={beta?.task?.pack_id === "input-errors" && !beta.task.source_id ? true : undefined}>
        <summary>사용자 MCAP 등록 · 보조 입력</summary>
        <div className="log-beta-form">
          <label>
            원본 MCAP · 최대 128 MiB
            <input
              aria-label="Upload Log Sim MCAP"
              type="file"
              accept=".mcap"
              disabled={!writable || !!busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void uploadFile(f);
              }}
            />
          </label>
          <label>
            이미 업로드한 원본
            <select
              aria-label="Log Sim uploaded recording"
              value={upload?.id ?? ""}
              onChange={(e) => {
                const v = uploads.data?.items.find(
                  (x) => x.id === e.target.value,
                );
                if (v) choose(v);
              }}
            >
              <option value="">원본 선택</option>
              {uploads.data?.items.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </label>
          <p>
            ego: axiom.PlanningEgo.v1 · 객체: axiom.ObjectTracks.v1 (world 좌표)
            · 카메라: foxglove.CompressedImage (선택). 원본 시각과 크기·좌표를
            임의 보완하지 않습니다.
          </p>
          <div className="log-beta-actions">
            <button
              className="button secondary"
              onClick={() =>
                void run("example", () =>
                  downloadLog(
                    `/log-sim/example.mcap?project_id=${encodeURIComponent(project)}`,
                    "authored-log-example.mcap",
                  ),
                )
              }
            >
              지원 MCAP 예제
            </button>
            <button
              className="button secondary"
              onClick={() =>
                void run("example", () =>
                  downloadLog(
                    `/log-sim/example.mcap?project_id=${encodeURIComponent(project)}&invalid=true`,
                    "authored-missing-objects.mcap",
                  ),
                )
              }
            >
              누락 오류 예제
            </button>
            <small>
              작성한 소프트웨어 입력 예제이며 실도로 측정이 아닙니다.
            </small>
          </div>
          {upload && (
            <>
              <label>
                조사 구간 이름
                <input
                  aria-label="Log Sim input title"
                  value={title}
                  maxLength={180}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              {(
                [
                  ["ego", ego, setEgo],
                  ["objects", objects, setObjects],
                  ["camera", camera, setCamera],
                ] as const
              ).map(([role, value, setter]) => (
                <label key={role}>
                  {role} 채널
                  <select
                    aria-label={`Log Sim ${role} channel`}
                    value={value}
                    onChange={(e) => setter(e.target.value)}
                  >
                    <option value="">
                      {role === "camera" ? "영상 없이 분석" : "지원 채널 선택"}
                    </option>
                    {upload.channels.map((c) => (
                      <option key={c.id} value={String(c.id)}>
                        {c.topic} ·{" "}
                        {upload.schemas.find((s) => s.id === c.schema_id)
                          ?.name ?? "미지원 schema"}{" "}
                        · {c.message_count} 표본
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <label>
                첫 ego 표본 번호 (0부터)
                <input
                  aria-label="Log Sim first sample"
                  type="number"
                  min={0}
                  value={first}
                  onChange={(e) => setFirst(Number(e.target.value))}
                />
              </label>
              <label>
                연속 ego 표본 수 (2–512)
                <input
                  aria-label="Log Sim sample count"
                  type="number"
                  min={2}
                  max={512}
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                />
              </label>
              <div className="log-beta-actions">
                <button
                  className="button secondary"
                  disabled={!!busy || ego === "" || objects === ""}
                  onClick={() =>
                    void run("inspect", async () =>
                      setInspection({
                        signature,
                        value: await api<Inspection>(
                          "/log-sim/inputs/inspect",
                          { method: "POST", body: signature },
                        ),
                      }),
                    )
                  }
                >
                  입력 점검
                </button>
                <button
                  className="button"
                  disabled={!preview || !!busy || !writable}
                  onClick={() =>
                    void run("register", async () => {
                      const source = await api<{ id: string }>("/log-sim/inputs", {
                        method: "POST",
                        body: JSON.stringify({
                          ...selection,
                          preview_hash: preview!.preview_hash,
                        }),
                      });
                      if (beta?.task?.pack_id === "input-errors" && !beta.task.source_id) await beta.progress({ source_id: source.id });
                      onRegistered(source.id);
                      setMessage(
                        "구간 등록 완료. 아래 라이브러리에서 새 구간을 선택하세요.",
                      );
                    })
                  }
                >
                  분석 구간 등록
                </button>
              </div>
              {preview && (
                <p role="status">
                  점검 완료 · {preview.frame_count}표본 ·{" "}
                  {preview.duration_s.toFixed(2)}초 · 좌표 {preview.world_frame}{" "}
                  ·{" "}
                  {preview.camera_available
                    ? "원본 영상 채널 선택됨"
                    : "영상 없이 BEV·신호 분석"}
                </p>
              )}
            </>
          )}
        </div>
      </details>
      <details open id="log-beta-settings">
        <summary>A/B 조건 편집과 저장</summary>
        {locked && <p>{pendingExecution ? "이전 실행 요청의 접수 여부를 먼저 확인하세요. 요청 당시 조건을 유지합니다." : `이 업무는 아래 보존 결과의 설정 r${pinned?.revision}에 고정됐습니다. 새로운 조건은 다른 업무에서 시작하세요.`}</p>}
        {restoring && <p role="status">업무에 저장된 정확한 설정 개정을 복원하고 있습니다…</p>}
        <fieldset className="log-beta-editor" disabled={locked || restoring || !!beta?.busy || !!beta?.loading || !!beta?.linkPending}>
        <div className="log-beta-form">
          <label>
            저장된 비교 설정
            <select
              aria-label="Log Sim comparison settings"
              value={recipe ? `${recipe.id}@${recipe.revision}` : ""}
              onChange={(e) => void chooseRecipe(e.target.value)}
            >
              <option value="">기본 설정 / 새 비교</option>
              {recipe && !recipes.data?.items.some(r => r.id === recipe.id && r.revision === recipe.revision) && <option value={`${recipe.id}@${recipe.revision}`}>{recipe.name} · 고정 r{recipe.revision}</option>}
              {recipes.data?.items.map((r) => (
                <option key={`${r.id}@${r.revision}`} value={`${r.id}@${r.revision}`}>
                  {r.name} · r{r.revision}
                </option>
              ))}
            </select>
          </label>
          <label>
            설정 이름
            <input
              aria-label="Log Sim settings name"
              value={name}
              maxLength={180}
              onChange={(e) => {
                setName(e.target.value);
                setDirty(true);
              }}
            />
          </label>
          <div className="log-beta-knobs">
            {(
              [
                ["A", a, setA],
                ["B", b, setB],
              ] as const
            ).map(([side, knobs, setter]) => (
              <fieldset key={side}>
                <legend>
                  {side} {side === "A" ? "기준" : "후보"}
                </legend>
                {(
                  [
                    ["cruise_speed_mps", "목표 속도 m/s", 0, 50],
                    ["time_headway_s", "차간 시간 s", 0.1, 10],
                    ["stop_buffer_m", "정지 여유 m", 0, 30],
                  ] as const
                ).map(([key, label, min, max]) => (
                  <label key={key}>
                    {label}
                    <input
                      aria-label={`Log Sim ${side} ${key}`}
                      type="number"
                      min={min}
                      max={max}
                      step="0.1"
                      value={knobs[key]}
                      onChange={(e) => {
                        setter({ ...knobs, [key]: Number(e.target.value) });
                        setDirty(true);
                      }}
                    />
                  </label>
                ))}
              </fieldset>
            ))}
          </div>
          <p>
            변경:{" "}
            {(["cruise_speed_mps", "time_headway_s", "stop_buffer_m"] as const)
              .filter((k) => a[k] !== b[k])
              .map((k) => `${k} ${a[k]} → ${b[k]}`)
              .join(" · ") || "A/B 동일 설정"}
            <br />
            같은 원본·seed·제어기 버전 사용. 요청은 차량에 적용되지 않습니다.
          </p>
          <button
            className="button"
            disabled={!writable || !!busy || !name.trim()}
            onClick={() => void saveRecipe()}
          >
            비교 설정 저장
          </button>
          <small>
            {dirty
              ? "변경 내용을 저장해야 배치에 적용됩니다."
              : recipe
                ? `적용 설정: ${recipe.name} r${recipe.revision}`
                : "기본 A/B: 차간 시간 1.5 → 3초"}
          </small>
        </div>
        </fieldset>
      </details>
      {busy && (
        <p role="status">
          {busy === "upload" ? "원본 업로드·색인 중…" : "처리 중…"}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {[error, uploads.error, recipes.error, packs.error]
        .filter(Boolean)
        .map((e, i) => (
          <p key={i} role="alert" className="log-sim-error">
            {e}
          </p>
        ))}
    </section>
  );
}
export function LogBatchExport({
  project,
  batch,
}: {
  project: string;
  batch: string;
}) {
  const [error, setError] = useState("");
  return (
    <div className="log-beta-actions">
      {(["json", "csv"] as const).map((format) => (
        <button
          className="button secondary"
          key={format}
          onClick={() =>
            void downloadLog(
              `/log-sim/batches/${encodeURIComponent(batch)}/export?${new URLSearchParams({ project_id: project, format })}`,
              `log-comparison.${format}`,
            ).catch((e) => setError(errorText(e)))
          }
        >
          {format.toUpperCase()} 내보내기
        </button>
      ))}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
