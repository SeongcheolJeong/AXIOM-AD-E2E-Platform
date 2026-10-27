import { useEffect, useRef, useState } from "react";
import { api, useResource } from "../../api";
import {
  hilProfileError,
  hilProfileRef,
  parseHilDutProfile,
} from "./dut-profile";
import type {
  HilDutParameters,
  HilDutProfile,
  HilDutProfileRef,
  HilProfileSelections,
} from "./dut-profile";

export type HilProfileList = {
  items: HilDutProfile[];
  next_offset: number | null;
};
const defaults: HilDutParameters = {
  max_object_age_ms: 250,
  recovery_fresh_steps: 3,
};
const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

export function HilProfilePicker({
  project,
  profiles,
  selected,
  label,
  disabled,
  onChange,
}: {
  project: string;
  profiles: HilDutProfile[];
  selected: HilDutProfileRef | null;
  label: string;
  disabled: boolean;
  onChange: (ref: HilDutProfileRef) => void;
}) {
  const chosen = useResource<HilDutProfile>(
    selected
      ? `/hil-sim/dut-profiles/${encodeURIComponent(selected.id)}?${new URLSearchParams({ project_id: project, revision: String(selected.revision) })}`
      : null,
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const latest = profiles.find((item) => item.id === selected?.id);
  const requestVersion = useRef(0);
  const selectionCallback = useRef(onChange);
  selectionCallback.current = onChange;
  useEffect(() => {
    ++requestVersion.current;
    return () => {
      ++requestVersion.current;
    };
  }, [project, selected?.id]);
  async function revision(value: number) {
    if (!selected) return;
    const request = ++requestVersion.current;
    setBusy(true);
    setError("");
    try {
      const record = parseHilDutProfile(
        await api(
          `/hil-sim/dut-profiles/${encodeURIComponent(selected.id)}?${new URLSearchParams({ project_id: project, revision: String(value) })}`,
        ),
        project,
      );
      if (request === requestVersion.current)
        selectionCallback.current(hilProfileRef(record));
    } catch (reason) {
      if (request === requestVersion.current) setError(errorMessage(reason));
    } finally {
      if (request === requestVersion.current) setBusy(false);
    }
  }
  const exact =
    chosen.data && chosen.data.sha256 === selected?.sha256 ? chosen.data : null;
  return (
    <div className="hil-profile-picker">
      <label>
        {label}
        <select
          aria-label={`${label} 선택`}
          disabled={disabled || busy}
          value={selected?.id ?? ""}
          onChange={(event) => {
            const profile = profiles.find(
              (item) => item.id === event.target.value,
            );
            if (profile) onChange(hilProfileRef(profile));
          }}
        >
          <option value="">저장 프로필 선택</option>
          {selected && !latest && (
            <option value={selected.id}>{exact?.name ?? selected.id}</option>
          )}
          {profiles.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      {selected && (
        <label>
          고정 개정
          <select
            aria-label={`${label} 개정`}
            disabled={disabled || busy}
            value={selected.revision}
            onChange={(event) => void revision(Number(event.target.value))}
          >
            {Array.from(
              { length: Math.max(selected.revision, latest?.revision ?? 1) },
              (_, index) => index + 1,
            )
              .reverse()
              .map((value) => (
                <option key={value} value={value}>
                  r{value}
                </option>
              ))}
          </select>
        </label>
      )}
      {exact && (
        <small>
          {exact.parameters.max_object_age_ms} ms 초과 시 정지 · 정상 입력{" "}
          {exact.parameters.recovery_fresh_steps}개 후 회복
        </small>
      )}
      {(error || chosen.error || (chosen.data && !exact)) && (
        <p className="hil-error">
          {error || chosen.error || "선택한 프로필 개정의 hash가 다릅니다."}
        </p>
      )}
    </div>
  );
}

export function HilProfileComparisonEditor({
  project,
  profiles,
  selections,
  disabled,
  onChange,
  onPick,
  onManage,
}: {
  project: string;
  profiles: HilDutProfile[];
  selections: HilProfileSelections | undefined;
  disabled: boolean;
  onChange: (profiles: HilProfileSelections | undefined) => void;
  onPick: (side: "baseline" | "faulted", ref: HilDutProfileRef) => void;
  onManage: () => void;
}) {
  return (
    <section className="hil-profile-comparison">
      <label>
        비교 방식
        <select
          aria-label="HIL 비교 방식"
          value={selections ? "profiles" : "legacy"}
          disabled={disabled}
          onChange={(event) =>
            onChange(
              event.target.value === "profiles"
                ? { baseline: null, faulted: null }
                : undefined,
            )
          }
        >
          <option value="legacy">정상 입력 A / 고장 입력 B</option>
          <option value="profiles">같은 고장 · DUT 프로필 A/B</option>
        </select>
      </label>
      {selections && (
        <>
          <HilProfilePicker
            project={project}
            profiles={profiles}
            label="A DUT 프로필"
            selected={selections.baseline}
            disabled={disabled}
            onChange={(baseline) => onPick("baseline", baseline)}
          />
          <HilProfilePicker
            project={project}
            profiles={profiles}
            label="B DUT 프로필"
            selected={selections.faulted}
            disabled={disabled}
            onChange={(faulted) => onPick("faulted", faulted)}
          />
          <small>
            A/B에 같은 고장 일정과 초기 장면을 적용합니다. 차량 반응 이후 객체
            입력은 달라질 수 있습니다.
          </small>
          <button type="button" onClick={onManage}>
            DUT 프로필 관리
          </button>
        </>
      )}
    </section>
  );
}

export default function HilDutProfiles({
  project,
  records,
  reload,
  writable,
  locked,
}: {
  project: string;
  records: HilProfileList | null;
  reload: () => void;
  writable: boolean;
  locked: boolean;
}) {
  const [saved, setSaved] = useState<HilDutProfile | null>(null),
    [name, setName] = useState("새 DUT 프로필"),
    [note, setNote] = useState(""),
    [parameters, setParameters] = useState<HilDutParameters>({ ...defaults }),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const attempt = useRef({ body: "", key: "" }),
    submitting = useRef(false);
  const validation = hilProfileError(parameters),
    disabled = busy || locked;
  const dirty =
    !saved ||
    name.trim() !== saved.name ||
    note.trim() !== saved.note ||
    JSON.stringify(parameters) !== JSON.stringify(saved.parameters);
  function select(profile: HilDutProfile | null) {
    setSaved(profile);
    setName(profile?.name ?? "새 DUT 프로필");
    setNote(profile?.note ?? "");
    setParameters(profile ? { ...profile.parameters } : { ...defaults });
    setError("");
    setNotice("");
  }
  async function save() {
    if (
      !writable ||
      disabled ||
      submitting.current ||
      validation ||
      !name.trim()
    )
      return;
    submitting.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const body = JSON.stringify({
        project_id: project,
        ...(saved ? { id: saved.id } : {}),
        expected_revision: saved?.revision ?? 0,
        name: name.trim(),
        note: note.trim(),
        parameters,
      });
      if (attempt.current.body !== body)
        attempt.current = { body, key: `hil-profile-${crypto.randomUUID()}` };
      const profile = parseHilDutProfile(
        await api("/hil-sim/dut-profiles", {
          method: "POST",
          headers: { "Idempotency-Key": attempt.current.key },
          body,
        }),
        project,
      );
      select(profile);
      reload();
      setNotice(
        `프로필 r${profile.revision} 저장 완료. 시험에서 사용할 개정을 직접 선택하세요.`,
      );
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <div className="hil-beta-form">
      <h2>DUT 프로필</h2>
      <p>정지 판단과 회복 조건을 이름으로 저장합니다.</p>
      <label>
        저장 프로필
        <select
          aria-label="관리할 DUT 프로필"
          disabled={disabled}
          value={saved?.id ?? ""}
          onChange={(event) =>
            select(
              records?.items.find((item) => item.id === event.target.value) ??
                null,
            )
          }
        >
          <option value="">새 프로필</option>
          {records?.items.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.name} · r{profile.revision}
            </option>
          ))}
        </select>
      </label>
      {saved && <small>현재 편집 r{saved.revision} · 저장하면 새 개정</small>}
      <button disabled={disabled} onClick={() => select(null)}>
        새 프로필 작성
      </button>
      <label>
        프로필 이름
        <input
          aria-label="DUT 프로필 이름"
          maxLength={180}
          disabled={disabled || !writable}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        설명
        <textarea
          aria-label="DUT 프로필 설명"
          rows={2}
          maxLength={4000}
          disabled={disabled || !writable}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      <label>
        입력 age 임계값 · ms
        <input
          aria-label="DUT 입력 age 임계값"
          type="number"
          min={50}
          max={1000}
          step={50}
          disabled={disabled || !writable}
          value={
            Number.isFinite(parameters.max_object_age_ms)
              ? parameters.max_object_age_ms
              : ""
          }
          onChange={(event) =>
            setParameters({
              ...parameters,
              max_object_age_ms:
                event.target.value === "" ? NaN : Number(event.target.value),
            })
          }
        />
      </label>
      <label>
        회복에 필요한 정상 입력 · 개
        <input
          aria-label="DUT 회복 정상 입력 수"
          type="number"
          min={1}
          max={10}
          step={1}
          disabled={disabled || !writable}
          value={
            Number.isFinite(parameters.recovery_fresh_steps)
              ? parameters.recovery_fresh_steps
              : ""
          }
          onChange={(event) =>
            setParameters({
              ...parameters,
              recovery_fresh_steps:
                event.target.value === "" ? NaN : Number(event.target.value),
            })
          }
        />
      </label>
      {(validation || error) && (
        <p className="hil-error" role="alert">
          {validation || error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <button
        className="hil-primary"
        disabled={
          disabled || !writable || !dirty || !!validation || !name.trim()
        }
        onClick={() => void save()}
      >
        {busy ? "저장 중…" : saved ? "프로필 새 개정 저장" : "프로필 저장"}
      </button>
      <small>수정해도 기존 시험과 결과에 고정된 개정은 바뀌지 않습니다.</small>
    </div>
  );
}
