import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, ApiError, authHeaders, useResource } from "../../api";
import "./stunt-lab.css";

type Config = {
  preset: "kickflip" | "ramp";
  mass_kg: number;
  initial_speed_mps: number;
  max_thrust_n: number;
  impulse_budget_ns: number;
  thrust_scale: number;
  target_height_m: number;
  duration_s: number;
  dt_s: number;
  camera_fps: number;
  suction_force_n: number;
  map_name: string;
  vehicle_blueprint: string;
};
type Ref = { id: string; revision: number; sha256: string };
type Design = Ref & {
  name: string;
  project_id: string;
  seed: number;
  config: Config;
};
type Template = {
  id: string;
  name: string;
  seed?: number;
  config: Config;
  available: boolean;
  blocked_reason: string | null;
};
type Readiness = {
  available: boolean;
  configured: boolean;
  adapter_installed: boolean;
  physics_verified: boolean;
  worker_readiness: string;
  reason: string | null;
};
type Artifact = { name: string; download_url: string; sha256: string };
type Run = {
  id: string;
  status: string;
  project_id: string;
  adapter_id: string;
  experiment_kind: string;
  error?: string;
  outcome_uncertain?: boolean;
  failure_http_status?: number | null;
  cancellation_supported: boolean;
  artifacts: Artifact[];
  input_hash: string;
  summary?: { design_ref?: Ref; trace_sha256?: string };
};
type RunRow = { id: string; name: string; status: string; design_ref: Ref };
type Frame = {
  time_s: number;
  height_m?: number;
  position_m: number[];
  rotation_deg: number[];
  velocity_mps: number[];
  angular_velocity_rad_s: number[];
  force_n: number[];
  torque_nm: number[];
  remaining_impulse_ns: number;
  phase: string;
};
type Trace = {
  schema_version?: string;
  physical_validation?: string;
  frames: Frame[];
  summary: Record<string, unknown>;
  config?: Config;
  design_ref?: Ref;
  seed?: number;
};
type Preview = {
  experiment: unknown;
  preview_hash: string;
  worker_readiness: string;
  fidelity: string;
};
type Pending = {
  signature: string;
  key: string;
  preview?: Preview;
  experiment?: string;
};
const base = "/object-sim/stunt-lab";
const terminal = (status: string) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(status);
const post = (body: unknown, key?: string): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
  headers: key ? { "Idempotency-Key": key } : {},
});
const magnitude = (value: number[]) => Math.hypot(...value);
function failureMessage(error: unknown, status?: number | null) {
  const message = error instanceof Error ? error.message : String(error);
  const code = error instanceof ApiError ? error.status : status;
  if (
    (code === 500 || code === 503) &&
    (!message.trim() ||
      /^Request failed \(\d+\)$/.test(message) ||
      ["{}", "null"].includes(message))
  )
    return `서버가 정상 응답을 반환하지 않았습니다 (HTTP ${code}). API 연결과 서버 상태를 확인한 뒤 다시 확인해 주세요.`;
  if (/Failed to fetch|NetworkError|Load failed/i.test(message))
    return "서버 응답을 받지 못했습니다. 연결 상태를 확인한 뒤 다시 확인해 주세요.";
  return message;
}
function connectionFailure(error: unknown, status?: number | null) {
  const code = error instanceof ApiError ? error.status : status;
  return (
    (code != null && code >= 500) ||
    /Failed to fetch|NetworkError|Load failed/i.test(
      error instanceof Error ? error.message : String(error),
    )
  );
}
async function responseFailure(response: Response) {
  let message = `Request failed (${response.status})`;
  try {
    const body = await response.json();
    if (typeof body.detail === "string" && body.detail.trim())
      message = body.detail;
  } catch {
    /* An empty proxy response has no application error detail. */
  }
  return new ApiError(response.status, message);
}
const metric = (value: unknown, unit: string, digits = 2, divisor = 1) =>
  typeof value === "number" && Number.isFinite(value)
    ? `${(value / divisor).toFixed(digits)} ${unit}`
    : "확인되지 않음";
const verdictReasons: Record<string, string> = {
  demo_criteria_met: "회전·착지 속도·종료 시 안정 조건을 충족했습니다.",
  contact_unconfirmed: "접촉을 뒷받침하는 관측이 확인되지 않았습니다.",
  rotation_incomplete: "공중 1회전 조건을 충족하지 못했습니다.",
  impact_speed_exceeded: "착지 직전 수직속도가 허용 기준을 넘었습니다.",
  settled_window_not_met:
    "종료 시점까지 1초 이상 안정 상태를 유지하지 못했습니다.",
  execution_error: "실행 중 오류가 발생했습니다. 원본 근거를 확인하세요.",
};

function ResultSummary({
  summary,
  label,
}: {
  summary: Record<string, unknown>;
  label: string;
}) {
  const passed = summary.demo_landing_passed === true;
  const known = typeof summary.demo_landing_passed === "boolean";
  const contact =
    summary.contact && typeof summary.contact === "object"
      ? (summary.contact as { source?: string; time_s?: number })
      : null;
  const contactLabel =
    contact?.source === "native_ground_rays_and_zero_thrust_support"
      ? "지면 ray 관측 + 무추력 상태의 지지 응답"
      : contact?.source === "native_collision_callback"
        ? "엔진 충돌 이벤트"
        : "접촉 근거 확인되지 않음";
  return (
    <div className="stunt-result-card">
      <div
        className={`stunt-verdict ${passed ? "passed" : known ? "failed" : "unknown"}`}
      >
        <span>{label}</span>
        <strong>
          {passed
            ? "데모 착지 기준 통과"
            : known
              ? "데모 착지 기준 미통과"
              : "착지 판정 확인 필요"}
        </strong>
      </div>
      <p>
        {typeof summary.reason === "string"
          ? (verdictReasons[summary.reason] ?? summary.reason)
          : "판정 상세는 원본 기록을 확인하세요."}
      </p>
      <div className="stunt-summary">
        {[
          [
            "공중 1회전",
            summary.rotation_complete === true
              ? "완료"
              : summary.rotation_complete === false
                ? "미완료"
                : "확인되지 않음",
          ],
          ["누적 회전", metric(summary.accumulated_roll_deg, "°", 1)],
          ["최고 상승 높이", metric(summary.max_height_m, "m")],
          [
            "착지 직전 수직속도",
            metric(summary.impact_vertical_speed_mps, "m/s"),
          ],
          ["종료 시 안정 구간", metric(summary.settled_window_s, "s")],
          ["사용 충격량", metric(summary.impulse_used_ns, "kN·s", 1, 1000)],
        ].map(([name, value]) => (
          <div key={name}>
            <span>{name}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <p className="stunt-contact">
        <strong>접촉 근거</strong> {contactLabel}
        {contact && typeof contact.time_s === "number"
          ? ` · ${contact.time_s.toFixed(2)} s`
          : ""}
      </p>
      <details className="stunt-raw-result">
        <summary>{label} · 원본 판정 JSON</summary>
        <pre>{JSON.stringify(summary, null, 2)}</pre>
      </details>
    </div>
  );
}
function bookmark(values: Record<string, string | null>) {
  const q = new URLSearchParams(location.hash.split("?")[1] ?? "");
  for (const [key, value] of Object.entries(values))
    value === null || value === "" ? q.delete(key) : q.set(key, value);
  q.set("mode", "stunt");
  history.replaceState(null, "", `#object-sim?${q}`);
}
function pending(project: string): Pending | null {
  try {
    return JSON.parse(
      sessionStorage.getItem(`stunt.pending.${project}`) ?? "null",
    );
  } catch {
    return null;
  }
}
function useVideo(
  url: string | undefined,
  onFailure: (error: unknown) => void,
) {
  const [source, setSource] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    setSource("");
    setError("");
    if (!url) return;
    const controller = new AbortController();
    let objectUrl = "";
    fetch(url, { headers: authHeaders(), signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw await responseFailure(response);
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setError(failureMessage(error));
          onFailure(error);
        }
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url, onFailure]);
  return { source, error };
}

function RecordedVideo({
  run,
  camera,
  time,
  onTime,
  playing,
  onPlaybackChange,
  onFailure,
  label,
}: {
  run: Run | null;
  camera: string;
  time: number;
  onTime?: (time: number) => void;
  playing: boolean;
  onPlaybackChange?: (playing: boolean) => void;
  onFailure: (error: unknown) => void;
  label: string;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const videoTimeReady = useRef(false);
  const [playbackError, setPlaybackError] = useState("");
  const artifact = run?.artifacts.find((item) => item.name === camera);
  const manifestArtifact = run?.artifacts.find(
    (item) => item.name === "camera-manifest.json",
  );
  const manifest = useResource<{
    frames: { camera: string; index: number; time_s: number }[];
  }>(manifestArtifact?.download_url.replace(/^\/api\/v1/, "") ?? null);
  const firstFrame = manifest.data?.frames.find(
    (item) => item.camera === camera.replace(/\.mp4$/, "") && item.index === 0,
  );
  const lastFrameTime =
    manifest.data?.frames
      .filter((item) => item.camera === camera.replace(/\.mp4$/, ""))
      .at(-1)?.time_s ?? Number.POSITIVE_INFINITY;
  const offset = firstFrame?.time_s ?? 0;
  const timingKnown = !manifestArtifact || Number.isFinite(firstFrame?.time_s);
  const syncError =
    (manifest.error
      ? failureMessage(manifest.error, manifest.errorStatus)
      : "") ||
    (manifest.data && !timingKnown
      ? "카메라 원본 시작 시간을 확인하지 못했습니다."
      : "");
  const localTime = Math.max(0, time - offset);
  const { source, error } = useVideo(artifact?.download_url, onFailure);
  useEffect(() => {
    if (manifest.error)
      onFailure(new ApiError(manifest.errorStatus ?? 0, manifest.error));
  }, [manifest.error, manifest.errorStatus, onFailure]);
  useEffect(() => {
    setPlaybackError("");
    videoTimeReady.current = false;
  }, [source]);
  useEffect(() => {
    if (video.current && Math.abs(video.current.currentTime - localTime) > 0.08)
      video.current.currentTime = localTime;
  }, [localTime]);
  useEffect(() => {
    if (!video.current || onPlaybackChange) return;
    if (playing)
      void video.current
        .play()
        .catch(() => setPlaybackError("비교 영상 재생을 시작하지 못했습니다."));
    else video.current.pause();
  }, [playing, source, onPlaybackChange]);
  return (
    <figure className="stunt-video">
      <figcaption>
        <strong>{label}</strong>
        <span>
          {run
            ? `${run.id.slice(-8)} · ${camera === "external.mp4" ? "외부 카메라" : "차량 카메라"}`
            : "실제 엔진 기록 대기"}
        </span>
      </figcaption>
      {source && timingKnown ? (
        <video
          ref={video}
          aria-label={label}
          src={source}
          controls={!!onPlaybackChange}
          muted
          playsInline
          preload="metadata"
          onLoadedMetadata={() => {
            if (video.current) {
              video.current.currentTime = localTime;
              videoTimeReady.current = true;
            }
          }}
          onTimeUpdate={(event) => {
            if (videoTimeReady.current)
              onTime?.(
                Math.min(
                  lastFrameTime,
                  event.currentTarget.currentTime + offset,
                ),
              );
          }}
          onPlay={() => onPlaybackChange?.(true)}
          onPause={() => onPlaybackChange?.(false)}
          onEnded={() => onPlaybackChange?.(false)}
          onError={() => {
            const message =
              "저장 영상을 브라우저에서 재생하지 못했습니다. 다시 확인하거나 원본 파일을 다운로드해 확인하세요.";
            setPlaybackError(message);
            onFailure(new Error(message));
          }}
        />
      ) : (
        <div className="stunt-video-empty">
          <span className="stunt-orbit" aria-hidden="true">
            ↻
          </span>
          <h3>
            {error || syncError
              ? "영상을 읽지 못했습니다"
              : artifact
                ? "저장 영상 불러오는 중…"
                : "저장된 실행 영상이 없습니다"}
          </h3>
          <p>
            {error ||
              syncError ||
              (run
                ? "영상이 생성되지 않은 실행도 상태와 실패 근거를 보존합니다."
                : "설정을 저장하고 준비 검사를 통과한 CARLA 실행을 제출하세요.")}
          </p>
        </div>
      )}
      {source && timingKnown && manifestArtifact && (
        <p className="stunt-video-time">
          원본 시작 {offset.toFixed(3)} s · 영상 경과 + 시작 시간으로 관측과
          정렬
        </p>
      )}
      {playbackError && <p role="alert">{playbackError}</p>}
    </figure>
  );
}

function Telemetry({
  trace,
  compare,
  time,
}: {
  trace: Trace;
  compare: Trace | null;
  time: number;
}) {
  const chart = useMemo(() => {
    const stride = Math.max(1, Math.ceil(trace.frames.length / 1400));
    return trace.frames
      .filter(
        (_, index) => index % stride === 0 || index === trace.frames.length - 1,
      )
      .map((frame) => ({
        time: frame.time_s,
        altitude: frame.height_m ?? frame.position_m[2],
        roll: frame.rotation_deg[0],
        pitch: frame.rotation_deg[1],
        angular: magnitude(frame.angular_velocity_rad_s),
        thrust: magnitude(frame.force_n) / 1000,
        budget: frame.remaining_impulse_ns / 1000,
      }));
  }, [trace]);
  const comparison = useMemo(() => {
    if (!compare) return [];
    const stride = Math.max(1, Math.ceil(compare.frames.length / 1400));
    return compare.frames
      .filter(
        (_, index) =>
          index % stride === 0 || index === compare.frames.length - 1,
      )
      .map((frame) => ({
        time: frame.time_s,
        altitudeB: frame.height_m ?? frame.position_m[2],
      }));
  }, [compare]);
  return (
    <div className="stunt-charts">
      {[
        {
          title: "위치 · 고도",
          unit: "m",
          lines: [["altitude", "A 고도", "#54dcc4"]],
          other: true,
        },
        {
          title: "차체 자세",
          unit: "°",
          lines: [
            ["roll", "Roll", "#54dcc4"],
            ["pitch", "Pitch", "#f9bb68"],
          ],
        },
        {
          title: "각속도",
          unit: "rad/s",
          lines: [["angular", "각속도 크기", "#9caeff"]],
        },
        {
          title: "추력 · 남은 충격량",
          unit: "kN / kN·s",
          lines: [
            ["thrust", "추력 kN", "#54dcc4"],
            ["budget", "예산 kN·s", "#f9bb68"],
          ],
        },
      ].map((panel) => (
        <section className="stunt-chart" key={panel.title}>
          <h3>
            {panel.title}
            <small>{panel.unit}</small>
          </h3>
          <div>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={chart}
                margin={{ left: 0, right: 15, top: 10, bottom: 5 }}
              >
                <CartesianGrid stroke="#28394d" strokeDasharray="3 3" />
                <XAxis
                  dataKey="time"
                  type="number"
                  domain={[0, "dataMax"]}
                  unit="s"
                  tickFormatter={(value) => Number(value).toFixed(1)}
                />
                <YAxis
                  width={55}
                  tickFormatter={(value) =>
                    Number(Number(value).toPrecision(3)).toString()
                  }
                />
                <Tooltip
                  labelFormatter={(value) => `${Number(value).toFixed(2)} s`}
                  formatter={(value) =>
                    typeof value === "number" ? value.toFixed(2) : String(value)
                  }
                  contentStyle={{
                    background: "#102130",
                    border: "1px solid #3b5369",
                  }}
                />
                <Legend />
                <ReferenceLine
                  x={time}
                  stroke="#e0ebf2"
                  strokeDasharray="4 4"
                />
                {panel.lines.map(([key, name, color]) => (
                  <Line
                    key={key}
                    dataKey={key}
                    name={name}
                    stroke={color}
                    dot={false}
                    isAnimationActive={false}
                  />
                ))}
                {panel.other && compare && (
                  <Line
                    data={comparison}
                    dataKey="altitudeB"
                    name="B 고도"
                    stroke="#f9bb68"
                    dot={false}
                    isAnimationActive={false}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      ))}
    </div>
  );
}

const fields: [keyof Config, string, string][] = [
  ["mass_kg", "차량 질량", "kg"],
  ["initial_speed_mps", "진입 속도", "m/s"],
  ["max_thrust_n", "최대 추력", "N"],
  ["impulse_budget_ns", "총 충격량 예산", "N·s"],
  ["thrust_scale", "추력 배율", "×"],
  ["target_height_m", "목표 고도", "m"],
  ["duration_s", "실행 시간", "s"],
  ["suction_force_n", "흡착력", "N"],
];

export default function StuntWorkspace({ project }: { project: string }) {
  const initial = useRef(
    new URLSearchParams(location.hash.split("?")[1] ?? ""),
  );
  const query = `project_id=${encodeURIComponent(project)}`;
  const templates = useResource<{
    items: Template[];
    config_schema: {
      properties: Record<string, { minimum?: number; maximum?: number }>;
    };
  }>(`${base}/templates?${query}`);
  const designs = useResource<{ items: Design[] }>(`${base}/designs?${query}`);
  const executions = useResource<{ items: RunRow[] }>(
    `${base}/executions?${query}`,
  );
  const readiness = useResource<Readiness>(`${base}/readiness?${query}`);
  const [config, setConfig] = useState<Config | null>(null),
    [name, setName] = useState("Kickflip · 기준 시험"),
    [seed, setSeed] = useState(42);
  const [saved, setSaved] = useState<Design | null>(null),
    [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState(""),
    [readinessStale, setReadinessStale] = useState(false),
    [busy, setBusy] = useState(false),
    [stage, setStage] = useState("");
  const [runId, setRunId] = useState(initial.current.get("execution") ?? ""),
    [compareId, setCompareId] = useState(initial.current.get("compare") ?? "");
  const [run, setRun] = useState<Run | null>(null),
    [otherRun, setOtherRun] = useState<Run | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null),
    [compareTrace, setCompareTrace] = useState<Trace | null>(null);
  const [camera, setCamera] = useState("external.mp4"),
    [playing, setPlaying] = useState(false),
    [time, setTime] = useState(
      Math.max(0, Number(initial.current.get("time")) || 0),
    ),
    [retry, setRetry] = useState(0);
  const saveAttempt = useRef<{ signature: string; key: string } | null>(null),
    attempt = useRef<Pending | null>(pending(project)),
    initialized = useRef(false);
  const dirty =
    !saved ||
    JSON.stringify(config) !== JSON.stringify(saved.config) ||
    name !== saved.name ||
    seed !== saved.seed;
  const active = !!runId && (!run || !terminal(run.status));
  const ramp = config?.preset === "ramp";
  const readinessUnconfirmed =
    readinessStale || readiness.loading || !!readiness.error;
  const canRun = !!readiness.data?.available && !ramp && !readinessUnconfirmed;
  const reportFailure = useCallback((failure: unknown) => {
    setError(failureMessage(failure));
    if (connectionFailure(failure)) setReadinessStale(true);
  }, []);
  useEffect(() => {
    if (readiness.data && !readiness.loading && !readiness.error)
      setReadinessStale(false);
  }, [readiness.data, readiness.loading, readiness.error]);
  useEffect(() => {
    if (
      [templates, designs, executions, readiness].some(
        (resource) =>
          resource.error &&
          connectionFailure(resource.error, resource.errorStatus),
      )
    )
      setReadinessStale(true);
  }, [
    templates.error,
    templates.errorStatus,
    designs.error,
    designs.errorStatus,
    executions.error,
    executions.errorStatus,
    readiness.error,
    readiness.errorStatus,
  ]);
  function refreshReadiness() {
    setReadinessStale(true);
    readiness.reload();
  }
  function retryAll() {
    setError("");
    setRetry((value) => value + 1);
    refreshReadiness();
    if (templates.error) templates.reload();
    if (designs.error) designs.reload();
    if (executions.error) executions.reload();
  }
  function selectDesign(design: Design) {
    setSaved(design);
    setConfig(design.config);
    setName(design.name);
    setSeed(design.seed);
    setPreview(null);
    bookmark({ project, design: design.id, revision: String(design.revision) });
  }
  useEffect(() => {
    if (initialized.current || !templates.data || !designs.data) return;
    let live = true;
    const id = initial.current.get("design"),
      revision = initial.current.get("revision");
    if (id) {
      api<Design>(
        `${base}/designs/${encodeURIComponent(id)}?${query}${revision ? `&revision=${encodeURIComponent(revision)}` : ""}`,
      )
        .then((design) => {
          if (!live) return;
          initialized.current = true;
          selectDesign(design);
        })
        .catch((error) => {
          if (live) reportFailure(error);
        });
    } else {
      initialized.current = true;
      const first = templates.data.items[0];
      setConfig(first.config);
      setName(first.name);
      setSeed(first.seed ?? 42);
    }
    return () => {
      live = false;
    };
  }, [templates.data, designs.data, retry]);
  useEffect(() => {
    setPreview(null);
  }, [config, name, seed]);
  async function loadTrace(value: Run): Promise<Trace | null> {
    if (
      value.project_id !== project ||
      value.experiment_kind !== "object_sim" ||
      value.adapter_id !== "carla-stunt"
    )
      throw new Error("선택한 프로젝트의 추력 차량 실행이 아닙니다.");
    const artifact = value.artifacts.find(
      (item) => item.name === "stunt-trace.json",
    );
    if (!artifact) {
      if (
        value.status === "completed" ||
        value.artifacts.some((item) => item.name.endsWith(".mp4"))
      )
        throw new Error("관측 기록이 없는 실행 영상은 표시할 수 없습니다.");
      return null;
    }
    const data = await api<Trace>(
      artifact.download_url.replace(/^\/api\/v1/, ""),
    );
    if (
      data.schema_version !== "carla-stunt-trace/1" ||
      data.physical_validation !== "not_established"
    )
      throw new Error(
        "추력 차량 기록의 버전 또는 검증 범위가 일치하지 않습니다.",
      );
    const expected = value.summary?.design_ref;
    if (
      !expected ||
      !data.design_ref ||
      ["id", "revision", "sha256"].some(
        (key) =>
          data.design_ref![key as keyof Ref] !== expected[key as keyof Ref],
      ) ||
      value.summary?.trace_sha256 !== artifact.sha256
    )
      throw new Error(
        "실행과 관측 기록의 개정·해시가 다릅니다. 영상과 그래프 표시를 차단했습니다.",
      );
    if (
      !Array.isArray(data.frames) ||
      data.frames.some(
        (row, index) =>
          !Number.isFinite(row.time_s) ||
          row.time_s < 0 ||
          (index > 0 && row.time_s <= data.frames[index - 1].time_s) ||
          !Number.isFinite(row.remaining_impulse_ns) ||
          [
            row.position_m,
            row.rotation_deg,
            row.velocity_mps,
            row.angular_velocity_rad_s,
            row.force_n,
            row.torque_nm,
          ].some(
            (vector) =>
              !Array.isArray(vector) ||
              vector.length !== 3 ||
              !vector.every(Number.isFinite),
          ),
      )
    )
      throw new Error("추력 차량 관측 기록 형식이 올바르지 않습니다.");
    return data;
  }
  useEffect(() => {
    setRun(null);
    setTrace(null);
    setPlaying(false);
    if (!runId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api<Run>(
          `/experiments/executions/${encodeURIComponent(runId)}`,
        );
        if (!live) return;
        if (
          next.project_id !== project ||
          next.adapter_id !== "carla-stunt" ||
          next.experiment_kind !== "object_sim"
        )
          throw new Error("선택한 프로젝트의 추력 차량 실행이 아닙니다.");
        if (terminal(next.status)) {
          const data = await loadTrace(next);
          if (live) {
            setRun(next);
            setTrace(data);
            executions.reload();
          }
        } else {
          setRun(next);
          timer = setTimeout(poll, 1200);
        }
      } catch (error) {
        if (live) reportFailure(error);
      }
    }
    void poll();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [runId, project, retry]);
  useEffect(() => {
    setOtherRun(null);
    setCompareTrace(null);
    if (!compareId) return;
    let live = true;
    api<Run>(`/experiments/executions/${encodeURIComponent(compareId)}`)
      .then(async (value) => {
        const data = await loadTrace(value);
        if (live) {
          setOtherRun(value);
          setCompareTrace(data);
        }
      })
      .catch((error) => {
        if (live) reportFailure(error);
      });
    return () => {
      live = false;
    };
  }, [compareId, project, retry]);
  useEffect(() => {
    const timer = setTimeout(
      () => bookmark({ time: time > 0 ? time.toFixed(3) : null }),
      200,
    );
    return () => clearTimeout(timer);
  }, [time]);
  const frame = useMemo(() => {
    if (!trace?.frames.length) return null;
    let low = 0,
      high = trace.frames.length - 1;
    while (low < high) {
      const mid = Math.ceil((low + high) / 2);
      if (trace.frames[mid].time_s <= time) low = mid;
      else high = mid - 1;
    }
    return trace.frames[low];
  }, [trace, time]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (error) {
      reportFailure(error);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    const body = {
      project_id: project,
      name,
      seed,
      config,
      ...(saved ? { id: saved.id, expected_revision: saved.revision } : {}),
    };
    const signature = JSON.stringify(body);
    if (saveAttempt.current?.signature !== signature)
      saveAttempt.current = { signature, key: crypto.randomUUID() };
    const value = await api<Design>(
      `${base}/designs`,
      post(body, saveAttempt.current.key),
    );
    selectDesign(value);
    saveAttempt.current = null;
    designs.reload();
    setStage(`개정 r${value.revision} 저장 완료`);
  }
  async function inspect() {
    if (!saved || dirty) throw new Error("현재 설정을 먼저 저장하세요.");
    const value = await api<Preview>(
      `${base}/designs/${encodeURIComponent(saved.id)}/preview`,
      post({
        project_id: project,
        revision: saved.revision,
        sha256: saved.sha256,
      }),
    );
    setPreview(value);
    refreshReadiness();
    setStage("실행 계획 생성 완료 · 엔진 물리 검증과 구분됩니다");
    return value;
  }
  async function execute() {
    if (!saved || dirty || !canRun)
      throw new Error("개정 저장과 엔진 준비 조건을 확인하세요.");
    if (attempt.current?.signature !== saved.sha256)
      attempt.current = { signature: saved.sha256, key: crypto.randomUUID() };
    const item = attempt.current;
    const persist = () =>
      sessionStorage.setItem(`stunt.pending.${project}`, JSON.stringify(item));
    persist();
    // Retain the same preview and key after a lost response so retry cannot create a second execution.
    item.preview ??= preview ?? (await inspect());
    persist();
    setStage("CARLA 실행 제출 중");
    item.experiment ??= (
      await api<{ id: string }>(
        "/experiments",
        post(item.preview.experiment, item.key),
      )
    ).id;
    persist();
    const job = await api<{ id: string }>(
      `/experiments/${encodeURIComponent(item.experiment)}/executions`,
      post({ preview_hash: item.preview.preview_hash }, item.key),
    );
    setRunId(job.id);
    setTime(0);
    bookmark({ execution: job.id, time: null });
    executions.reload();
    attempt.current = null;
    sessionStorage.removeItem(`stunt.pending.${project}`);
    setStage("실행 큐 접수 완료");
  }
  async function download(url: string, filename: string) {
    const response = await fetch(url, { headers: authHeaders() });
    if (!response.ok) throw await responseFailure(response);
    const source = URL.createObjectURL(await response.blob()),
      link = document.createElement("a");
    link.href = source;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(source), 1000);
  }
  function applyTemplate(template: Template) {
    setConfig(template.config);
    setName(template.name);
    setSeed(template.seed ?? 42);
    setSaved(null);
    setPreview(null);
    setStage("");
    setError("");
    bookmark({ design: null, revision: null });
  }
  const issue =
    error ||
    [templates, designs, executions, readiness]
      .filter((resource) => resource.error)
      .map((resource) =>
        failureMessage(resource.error, resource.errorStatus),
      )[0];
  if (!config || !templates.data)
    return (
      <section className="stunt-lab" aria-label="추력 차량 시험장">
        <h2>추력 차량 시험장</h2>
        <p role={issue ? "alert" : "status"}>
          {issue || "시험 설정과 준비 상태를 불러오는 중…"}
        </p>
        {issue && <button onClick={retryAll}>다시 확인</button>}
      </section>
    );
  return (
    <section className="stunt-lab" aria-label="추력 차량 시험장">
      <header className="stunt-heading">
        <div>
          <span className="stunt-eyebrow">OBJECT SIM / THRUST VEHICLE LAB</span>
          <h1>추력 차량 시험장</h1>
          <p>
            추력과 회전, 착지까지. 저장한 설정으로 실행하고 결과를 비교하세요.
          </p>
        </div>
        <span className="stunt-badge">가정한 차량 모델</span>
      </header>
      <p className="stunt-boundary">
        일반 스포츠카의 가정한 강체·추력 모델입니다. Tesla 성능 또는 실제
        차량·탑승자 안전 검증이 아닙니다.{" "}
        <code>physical_validation: not_established</code>
      </p>
      {issue && (
        <div className="stunt-error" role="alert">
          {issue}
          <button onClick={retryAll}>다시 확인</button>
        </div>
      )}
      <div className="stunt-presets">
        {templates.data.items.map((template) => (
          <button
            key={template.id}
            className={
              JSON.stringify(config) === JSON.stringify(template.config)
                ? "selected"
                : ""
            }
            onClick={() => applyTemplate(template)}
            disabled={busy}
          >
            <span>
              {template.config.preset === "ramp"
                ? "02 / RAMP"
                : template.id.includes("low")
                  ? "01B / LOW THRUST"
                  : "01 / KICKFLIP"}
            </span>
            <strong>{template.name}</strong>
            <small>
              {template.config.preset === "ramp"
                ? "충돌 맵 에셋 준비 필요"
                : template.id.includes("low")
                  ? "추력 부족 조건 비교"
                  : "상승 → 1회전 → 회전 제동 → 착지"}
            </small>
          </button>
        ))}
      </div>
      <div className="stunt-layout">
        <aside className="stunt-settings stunt-panel">
          <div className="stunt-panel-title">
            <h2>시험 설정</h2>
            <span>{dirty ? "미저장 편집안" : `저장 r${saved?.revision}`}</span>
          </div>
          <label>
            시험 이름
            <input
              aria-label="시험 이름"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={180}
            />
          </label>
          <div className="stunt-field-grid">
            {fields
              .filter(([key]) => key !== "suction_force_n" || ramp)
              .map(([key, label, unit]) => (
                <label key={key}>
                  {label}
                  <span className="stunt-input-unit">
                    <input
                      aria-label={label}
                      type="number"
                      value={config[key]}
                      step={key === "thrust_scale" ? 0.05 : "any"}
                      min={
                        templates.data?.config_schema?.properties[key]
                          ?.minimum ?? 0
                      }
                      max={
                        templates.data?.config_schema?.properties[key]?.maximum
                      }
                      onChange={(event) =>
                        setConfig({
                          ...config,
                          [key]: Number(event.target.value),
                        })
                      }
                    />
                    <span>{unit}</span>
                  </span>
                </label>
              ))}
          </div>
          <div className="stunt-field-grid">
            <label>
              물리 시간 간격
              <select
                aria-label="물리 시간 간격"
                value={config.dt_s}
                onChange={(event) =>
                  setConfig({ ...config, dt_s: Number(event.target.value) })
                }
              >
                <option value={0.01}>10 ms</option>
                <option value={0.005}>5 ms</option>
              </select>
            </label>
            <label>
              Seed
              <input
                aria-label="Stunt seed"
                type="number"
                min={0}
                max={2147483647}
                value={seed}
                onChange={(event) => setSeed(Number(event.target.value))}
              />
            </label>
          </div>
          <details>
            <summary>맵 · 차량 · 캡처</summary>
            <label>
              CARLA 맵
              <input
                aria-label="CARLA 맵"
                value={config.map_name}
                onChange={(event) =>
                  setConfig({ ...config, map_name: event.target.value })
                }
              />
            </label>
            <label>
              차량 blueprint
              <input
                aria-label="차량 blueprint"
                value={config.vehicle_blueprint}
                onChange={(event) =>
                  setConfig({
                    ...config,
                    vehicle_blueprint: event.target.value,
                  })
                }
              />
            </label>
            <p>
              카메라 {config.camera_fps} fps · 질량분포/추력원은 가정한 모델
              정의에 고정됩니다.
            </p>
          </details>
          <div className="stunt-actions">
            <button disabled={busy || !dirty} onClick={() => void action(save)}>
              개정 저장
            </button>
            <button
              disabled={busy || dirty}
              onClick={() =>
                void action(async () => {
                  await inspect();
                })
              }
            >
              실행 계획 확인
            </button>
            <button
              className="stunt-primary"
              disabled={busy || active || dirty || !canRun}
              onClick={() => void action(execute)}
            >
              CARLA 실행
            </button>
            {active && run?.cancellation_supported && (
              <button
                className="stunt-cancel"
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await api(
                      `/experiments/executions/${encodeURIComponent(runId)}/cancel`,
                      post({}),
                    );
                    setStage("취소 요청 접수");
                    setRetry((value) => value + 1);
                  })
                }
              >
                실행 취소
              </button>
            )}
          </div>
          <p className="stunt-status" role="status">
            {stage || "개정을 저장하면 설정과 seed가 고정됩니다."}
          </p>
          <div className={`stunt-readiness ${canRun ? "ready" : "blocked"}`}>
            <strong>
              {ramp
                ? "Ramp 실행 차단"
                : readinessUnconfirmed
                  ? "CARLA 현재 준비 상태 확인 필요"
                  : canRun
                    ? "CARLA 실행 준비됨"
                    : "CARLA 실행 준비 필요"}
            </strong>
            <p>
              {ramp
                ? "missing_collision_asset · 곡면·수직·돌출 램프 충돌 에셋이 아직 등록되지 않았습니다."
                : readinessUnconfirmed
                  ? "현재 서버 응답을 다시 확인해야 합니다. 이전 준비 상태만으로 새 실행을 제출할 수 없습니다."
                  : readiness.data?.reason ||
                    "실행 어댑터와 전용 엔진의 준비 상태를 확인합니다."}
            </p>
            <dl>
              <div>
                <dt>어댑터</dt>
                <dd>
                  {readiness.data?.adapter_installed
                    ? "설치됨"
                    : "미설치/확인 중"}
                </dd>
              </div>
              <div>
                <dt>엔진 설정</dt>
                <dd>{readiness.data?.configured ? "설정됨" : "미설정"}</dd>
              </div>
              <div>
                <dt>물리 응답 검증</dt>
                <dd>
                  {readinessUnconfirmed
                    ? "현재 상태 확인 필요"
                    : readiness.data?.physics_verified
                      ? "검증 근거 있음"
                      : "미검증"}
                </dd>
              </div>
            </dl>
            <button onClick={refreshReadiness} disabled={readiness.loading}>
              준비 상태 새로고침
            </button>
          </div>
          {preview && (
            <details className="stunt-preview" open>
              <summary>고정된 실행 계획</summary>
              <p>
                worker: {preview.worker_readiness}
                <br />
                fidelity: {preview.fidelity}
              </p>
              <code>{preview.preview_hash}</code>
            </details>
          )}
        </aside>
        <div className="stunt-evidence">
          <div className="stunt-panel stunt-run-picker">
            <label>
              저장 개정
              <select
                aria-label="Stunt 저장 개정"
                value={saved ? `${saved.id}:${saved.revision}` : ""}
                onChange={(event) => {
                  const value = designs.data?.items.find(
                    (item) =>
                      `${item.id}:${item.revision}` === event.target.value,
                  );
                  if (value) selectDesign(value);
                }}
              >
                <option value="">선택</option>
                {designs.data?.items.map((item) => (
                  <option
                    key={`${item.id}:${item.revision}`}
                    value={`${item.id}:${item.revision}`}
                  >
                    {item.name} · r{item.revision}
                  </option>
                ))}
              </select>
            </label>
            <label>
              실행 A
              <select
                aria-label="Stunt 실행 A"
                value={runId}
                onChange={(event) => {
                  setRunId(event.target.value);
                  setTime(0);
                  bookmark({ execution: event.target.value, time: null });
                }}
              >
                <option value="">실행 선택</option>
                {runId &&
                  !executions.data?.items.some((item) => item.id === runId) && (
                    <option value={runId}>{runId}</option>
                  )}
                {executions.data?.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · r{item.design_ref.revision} · {item.status}
                  </option>
                ))}
              </select>
            </label>
            <label>
              비교 B
              <select
                aria-label="Stunt 비교 B"
                value={compareId}
                onChange={(event) => {
                  setCompareId(event.target.value);
                  bookmark({ compare: event.target.value });
                }}
              >
                <option value="">비교 없음</option>
                {compareId &&
                  !executions.data?.items.some(
                    (item) => item.id === compareId,
                  ) && <option value={compareId}>{compareId}</option>}
                {executions.data?.items
                  .filter((item) => terminal(item.status))
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} · r{item.design_ref.revision} · {item.status}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          {run && (
            <div className="stunt-run-state">
              <span>
                실행 상태 <strong>{run.status}</strong>
              </span>
              <code>{run.id}</code>
              {run.error && <p role="alert">{run.error}</p>}
              {run.outcome_uncertain && (
                <p className="stunt-warning">
                  실행 결과 확인 필요 · 저장된 작업 상태입니다. 해당 작업의 당시
                  기록이며, 현재 엔진 준비 상태는 별도로 확인합니다.
                </p>
              )}
              {run.failure_http_status && (
                <span>실패 응답 {run.failure_http_status}</span>
              )}
            </div>
          )}
          {trace && (dirty || saved?.sha256 !== trace.design_ref?.sha256) && (
            <p className="stunt-warning">
              위 편집안과 다른 저장 결과입니다. 현재 편집 내용은 재생 영상에
              적용되지 않았습니다.
            </p>
          )}
          <div className="stunt-camera-tabs">
            <button
              aria-pressed={camera === "external.mp4"}
              onClick={() => setCamera("external.mp4")}
            >
              외부 카메라
            </button>
            <button
              aria-pressed={camera === "onboard.mp4"}
              onClick={() => setCamera("onboard.mp4")}
            >
              차량 카메라
            </button>
            <span>보존된 실행 기록 재생</span>
          </div>
          <div className={`stunt-videos ${otherRun ? "comparison" : ""}`}>
            <RecordedVideo
              key={`a:${runId}:${camera}:${retry}`}
              run={run}
              camera={camera}
              time={time}
              onTime={setTime}
              playing={playing}
              onPlaybackChange={setPlaying}
              onFailure={reportFailure}
              label="실행 A · 저장 영상"
            />
            {otherRun && (
              <RecordedVideo
                key={`b:${compareId}:${camera}:${retry}`}
                run={otherRun}
                camera={camera}
                time={time}
                playing={playing}
                onFailure={reportFailure}
                label="비교 B · 저장 영상"
              />
            )}
          </div>
          {trace?.frames.length ? (
            <>
              <div className="stunt-panel stunt-playback">
                <div>
                  <strong>{time.toFixed(3)} s</strong>
                  <span>
                    {frame?.phase} · 관측 {frame?.time_s.toFixed(3)} s
                  </span>
                </div>
                <input
                  type="range"
                  aria-label="Stunt 재생 시간"
                  min={0}
                  max={trace.frames.at(-1)?.time_s ?? 0}
                  step={trace.config?.dt_s ?? 0.01}
                  value={time}
                  onChange={(event) => setTime(Number(event.target.value))}
                />
                <div className="stunt-live-values">
                  <span>
                    상승 높이{" "}
                    <b>
                      {(frame?.height_m ?? frame?.position_m[2])?.toFixed(2)} m
                    </b>
                  </span>
                  <span>
                    Roll <b>{frame?.rotation_deg[0].toFixed(1)}°</b>
                  </span>
                  <span>
                    수직속도 <b>{frame?.velocity_mps[2].toFixed(2)} m/s</b>
                  </span>
                  <span>
                    남은 예산{" "}
                    <b>
                      {((frame?.remaining_impulse_ns ?? 0) / 1000).toFixed(1)}{" "}
                      kN·s
                    </b>
                  </span>
                </div>
                {otherRun && (
                  <p>
                    영상 B는 A의 동일 실행 경과 시간으로 이동합니다. 두 실행의
                    원본 시간과 결과는 각각 유지됩니다.
                  </p>
                )}
              </div>
              <Telemetry trace={trace} compare={compareTrace} time={time} />
              <section className="stunt-panel">
                <h2>실행 판정 · 원본 근거</h2>
                <p>
                  착지 기준 통과는 이 데모 모델 안의 판정입니다. 실제 차량의
                  안전성을 뜻하지 않습니다.
                </p>
                <div
                  className={`stunt-results ${compareTrace ? "comparison" : ""}`}
                >
                  <ResultSummary summary={trace.summary} label="실행 A" />
                  {compareTrace && (
                    <ResultSummary
                      summary={compareTrace.summary}
                      label="비교 B"
                    />
                  )}
                </div>
                {compareTrace?.config && trace.config && (
                  <>
                    <h3>A/B 설정 차이</h3>
                    <table>
                      <thead>
                        <tr>
                          <th>설정</th>
                          <th>A</th>
                          <th>B</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(Object.keys(trace.config) as (keyof Config)[])
                          .filter(
                            (key) =>
                              trace.config![key] !== compareTrace.config![key],
                          )
                          .map((key) => (
                            <tr key={key}>
                              <td>{key}</td>
                              <td>{String(trace.config![key])}</td>
                              <td>{String(compareTrace.config![key])}</td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </>
                )}
              </section>
            </>
          ) : (
            <div className="stunt-panel stunt-no-trace">
              <h2>관측 기록</h2>
              <p>
                {run && terminal(run.status)
                  ? "이 실행에 읽을 수 있는 stunt-trace.json 관측 기록이 없습니다. 상태와 원본 근거에서 실패 이유를 확인하세요."
                  : "실행이 끝나면 고도·자세·각속도·추력·예산을 영상 시간과 함께 확인할 수 있습니다."}
              </p>
            </div>
          )}
          {run && (
            <section className="stunt-panel stunt-downloads">
              <h2>실행 근거 내보내기</h2>
              <p>
                설정 개정·seed·어댑터·입력 해시와 실제 생성된 파일을 보존합니다.
              </p>
              <button
                onClick={() =>
                  void action(() =>
                    download(
                      `/api/v1/experiments/executions/${encodeURIComponent(runId)}/export`,
                      `${runId}.json`,
                    ),
                  )
                }
              >
                실행 근거 JSON
              </button>
              {run.artifacts.map((artifact) => (
                <button
                  key={artifact.name}
                  title={artifact.sha256}
                  onClick={() =>
                    void action(() =>
                      download(artifact.download_url, artifact.name),
                    )
                  }
                >
                  {artifact.name}
                </button>
              ))}
            </section>
          )}
        </div>
      </div>
    </section>
  );
}
