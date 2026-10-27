import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Edges, Html, Line, OrbitControls } from "@react-three/drei";
import { api, authHeaders, useResource } from "../api";
import { Badge, PageHeading } from "../components/ui";
import {
  actorColor,
  actorReferences,
  appendWaypoint,
  createActor,
  defaultAction,
  laneCenter,
  laneChangeWarnings,
  startingLane,
  waypointIssue,
} from "../scenario-authoring";
import type {
  Action,
  Actor,
  Pose,
  Scenario,
  TimedPose,
  Trigger,
} from "../scenario-authoring";
import "../scenario-v2.css";

type RecordV2 = {
  id: string;
  project_id: string;
  revision: number;
  current_revision: number;
  scenario: Scenario;
  manifest_hash: string;
};
type SweepJob = {
  configuration_hash: string;
  selection: { actor_id: string; field: string; value: number }[];
  seed: number;
  scenario: Scenario;
};
type Sweep = {
  jobs: SweepJob[];
  job_count: number;
  configured_seconds: number;
  preview_hash: string;
  seed_note: string;
};
type ExecutionPreview = {
  preview_hash: string;
  execution_fidelity: string;
  job_count: number;
  prepared: unknown;
};
const templateLabels: Record<string, string> = {
  lane_follow: "차로 유지",
  cut_in: "끼어들기",
  emergency_brake: "긴급 제동",
  pedestrian_crossing: "보행자 횡단",
  merge: "차로 합류",
  lead_stop: "선행차 정지",
  traffic_interaction: "다중 객체 교통 장면",
};
type Placement = {
  kind: "actor" | "route" | "path";
  actionIndex?: number;
} | null;
type Bounds = { minX: number; maxX: number; minY: number; maxY: number };

function CameraFit({ bounds, top }: { bounds: Bounds; top: boolean }) {
  const { camera, size } = useThree();
  useEffect(() => {
    const centerX = (bounds.minX + bounds.maxX) / 2,
      centerZ = -(bounds.minY + bounds.maxY) / 2;
    const span = Math.max(
      16,
      (bounds.maxX - bounds.minX) / Math.max(0.4, size.width / size.height),
      bounds.maxY - bounds.minY,
    );
    camera.position.set(
      centerX,
      span * 1.5,
      centerZ + (top ? 0.01 : span * 0.85),
    );
    camera.lookAt(centerX, 0, centerZ);
    camera.near = 0.1;
    camera.far = Math.max(5000, span * 10);
    camera.updateProjectionMatrix();
  }, [camera, size, bounds, top]);
  return null;
}

function ActorMesh({
  actor,
  selected,
  select,
}: {
  actor: Actor;
  selected: boolean;
  select: () => void;
}) {
  const {
      length_m: length,
      width_m: width,
      height_m: height,
    } = actor.dimensions,
    color = actorColor(actor.id);
  return (
    <group
      position={[actor.pose.x, 0, -actor.pose.y]}
      rotation={[0, actor.pose.heading_rad, 0]}
      onClick={(event) => {
        event.stopPropagation();
        select();
      }}
    >
      <mesh position={[0, height / 2, 0]}>
        <boxGeometry args={[length, height, width]} />
        <meshStandardMaterial
          color={color}
          transparent
          opacity={selected ? 0.16 : 0.06}
        />
        <Edges color={selected ? "#d79e38" : color} />
      </mesh>
      {actor.kind === "vehicle" ? (
        <>
          <mesh position={[0, height * 0.36, 0]}>
            <boxGeometry args={[length, height * 0.5, width]} />
            <meshStandardMaterial color={color} />
          </mesh>
          <mesh position={[-length * 0.08, height * 0.77, 0]}>
            <boxGeometry args={[length * 0.53, height * 0.4, width * 0.83]} />
            <meshStandardMaterial color="#b9d3d5" />
          </mesh>
          {[-1, 1].flatMap((x) =>
            [-1, 1].map((z) => (
              <mesh
                key={`${x}:${z}`}
                position={[x * length * 0.3, height * 0.17, z * width * 0.48]}
                rotation={[Math.PI / 2, 0, 0]}
              >
                <cylinderGeometry
                  args={[height * 0.17, height * 0.17, width * 0.13, 12]}
                />
                <meshStandardMaterial color="#253230" />
              </mesh>
            )),
          )}
        </>
      ) : (
        <>
          <mesh
            position={[0, height * 0.55, 0]}
            scale={[length, height * 0.42, width]}
          >
            <sphereGeometry args={[0.5, 12, 10]} />
            <meshStandardMaterial color={color} />
          </mesh>
          <mesh
            position={[0, height * 0.89, 0]}
            scale={[length * 0.6, height * 0.21, width * 0.6]}
          >
            <sphereGeometry args={[0.5, 12, 10]} />
            <meshStandardMaterial color="#ccbca9" />
          </mesh>
          {[-1, 1].map((side) => (
            <mesh key={side} position={[0, height * 0.2, side * width * 0.2]}>
              <boxGeometry args={[length * 0.27, height * 0.4, width * 0.25]} />
              <meshStandardMaterial color="#36494b" />
            </mesh>
          ))}
        </>
      )}
      <Line
        points={[
          [0, height + 0.12, 0],
          [length / 2 + 1, height + 0.12, 0],
          [length / 2 + 0.6, height + 0.12, -0.3],
          [length / 2 + 1, height + 0.12, 0],
          [length / 2 + 0.6, height + 0.12, 0.3],
        ]}
        color={color}
        lineWidth={2}
      />
    </group>
  );
}

function Schematic({
  draft,
  selected,
  placement,
  select,
  update,
}: {
  draft: Scenario;
  selected: string;
  placement: Placement;
  select: (id: string) => void;
  update: (x: number, y: number) => void;
}) {
  const width = draft.map.lane_width_m,
    length = draft.map.length_m;
  const [fit, setFit] = useState<"map" | "actors" | "selected">("actors"),
    [fitVersion, setFitVersion] = useState(0),
    [top, setTop] = useState(false);
  const bounds = useMemo<Bounds>(() => {
    if (fit === "map")
      return { minX: -8, maxX: length + 8, minY: -2 * width - 8, maxY: 8 };
    const actors =
      fit === "selected"
        ? draft.actors.filter((actor) => actor.id === selected)
        : draft.actors;
    const points = actors.flatMap((actor) => [
      actor.pose,
      ...actor.route,
      ...actor.actions.flatMap((action) => action.path),
    ]);
    return {
      minX:
        Math.min(
          ...points.map((point) => point.x),
          ...(points.length ? [] : [0]),
        ) - 10,
      maxX:
        Math.max(
          ...points.map((point) => point.x),
          ...(points.length ? [] : [20]),
        ) + 10,
      minY: Math.min(-2 * width, ...points.map((point) => point.y)) - 6,
      maxY: Math.max(0, ...points.map((point) => point.y)) + 6,
    };
    // Refit only on an explicit view choice or a map change; editing does not chase the camera.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fit, fitVersion, length, width]);
  const target: [number, number, number] = [
    (bounds.minX + bounds.maxX) / 2,
    0,
    -(bounds.minY + bounds.maxY) / 2,
  ];
  const placementText =
    placement?.kind === "actor"
      ? "객체 위치"
      : placement?.kind === "route"
        ? "route waypoint"
        : placement
          ? `행동 ${(placement.actionIndex ?? 0) + 1} 경로 waypoint`
          : "";
  return (
    <div className="scenario-v2-scene">
      <div className="scenario-v2-scene-toolbar">
        <strong>자체 절차적 장면</strong>
        <div className="scenario-v2-buttons">
          {(["actors", "map", "selected"] as const).map((mode) => (
            <button
              key={mode}
              className={`button ${fit === mode ? "" : "secondary"}`}
              onClick={() => {
                setFit(mode);
                setFitVersion((version) => version + 1);
              }}
            >
              {mode === "map"
                ? "지도 전체 맞춤"
                : mode === "actors"
                  ? "모든 객체 맞춤"
                  : "선택 객체 맞춤"}
            </button>
          ))}
          <button className="button secondary" onClick={() => setTop(!top)}>
            {top ? "입체 보기" : "평면 보기"}
          </button>
        </div>
      </div>
      <div
        className={`scenario-v2-canvas ${placement ? "placing" : ""}`}
        aria-label="Authored object schematic"
        data-map-length={length}
        data-lane-width={width}
      >
        <Canvas camera={{ position: [25, 45, 42], fov: 48 }}>
          <color attach="background" args={["#e9efe9"]} />
          <ambientLight intensity={1.5} />
          <directionalLight position={[30, 45, -20]} intensity={2} />
          <CameraFit bounds={bounds} top={top} />
          <mesh
            rotation={[-Math.PI / 2, 0, 0]}
            position={[length / 2, -0.07, width]}
            onClick={(event) => {
              if (placement) {
                event.stopPropagation();
                update(event.point.x, -event.point.z);
              }
            }}
          >
            <planeGeometry args={[length + 40, Math.max(32, 2 * width + 24)]} />
            <meshStandardMaterial color="#cbd8c8" />
          </mesh>
          <mesh
            rotation={[-Math.PI / 2, 0, 0]}
            position={[length / 2, -0.02, width]}
            onClick={(event) => {
              if (placement) {
                event.stopPropagation();
                update(event.point.x, -event.point.z);
              }
            }}
          >
            <planeGeometry args={[length, width * 2]} />
            <meshStandardMaterial color="#4c5c57" />
          </mesh>
          {[0, width, width * 2].map((z, index) => (
            <Line
              key={z}
              points={[
                [0, 0.03, z],
                [length, 0.03, z],
              ]}
              color="#e5e8dd"
              lineWidth={1.5}
              dashed={index === 1}
              dashSize={3}
              gapSize={3}
            />
          ))}
          {[0, length].map((x) => (
            <Line
              key={x}
              points={[
                [x, 0.04, 0],
                [x, 0.04, width * 2],
              ]}
              color="#edf4ec"
              lineWidth={2}
            />
          ))}
          {[-1, -2].map((lane) => (
            <Html
              key={lane}
              position={[8, 0.15, -laneCenter(lane as -1 | -2, width)]}
              center
            >
              <span className="scenario-v2-map-label">차로 {lane}</span>
            </Html>
          ))}
          {[0, length / 4, length / 2, length * 0.75, length].map((x) => (
            <Html key={x} position={[x, 0.1, -2]} center>
              <span className="scenario-v2-map-label">x {x} m</span>
            </Html>
          ))}
          <Line
            points={[
              [0, 0.08, 0],
              [16, 0.08, 0],
              [14, 0.08, -1],
              [16, 0.08, 0],
              [14, 0.08, 1],
            ]}
            color="#b66646"
            lineWidth={2}
          />
          <Html position={[17, 0.15, 0]}>
            <span className="scenario-v2-axis-label">+x 전방</span>
          </Html>
          <Line
            points={[
              [0, 0.08, 0],
              [0, 0.08, -8],
              [-1, 0.08, -6],
              [0, 0.08, -8],
              [1, 0.08, -6],
            ]}
            color="#397c66"
            lineWidth={2}
          />
          <Html position={[0, 0.15, -9]}>
            <span className="scenario-v2-axis-label">+y 좌측</span>
          </Html>
          {draft.actors.map((actor) => (
            <group key={actor.id}>
              <ActorMesh
                actor={actor}
                selected={actor.id === selected}
                select={() => select(actor.id)}
              />
              <Html
                position={[
                  actor.pose.x,
                  actor.dimensions.height_m + 1,
                  -actor.pose.y,
                ]}
                center
              >
                <span
                  className={`scenario-v2-actor-label ${actor.id === selected ? "selected" : ""}`}
                  style={{ borderColor: actorColor(actor.id) }}
                >
                  {actor.id} · {actor.kind === "vehicle" ? "차량" : "보행자"}
                  {actor.role === "ego" ? " · ego" : ""}
                </span>
              </Html>
              {[
                actor.route,
                ...actor.actions
                  .filter((action) => action.kind === "follow_path")
                  .map((action) => action.path),
              ].map((path, index) => (
                <group key={index}>
                  {path.length > 1 && (
                    <Line
                      points={path.map((point) => [point.x, 0.15, -point.y])}
                      color={actorColor(actor.id)}
                      lineWidth={actor.id === selected ? 3 : 1.5}
                      dashed={index === 0}
                    />
                  )}
                  {actor.id === selected &&
                    path.map((point, pointIndex) => (
                      <group
                        key={pointIndex}
                        position={[point.x, 0.18, -point.y]}
                      >
                        <mesh>
                          <sphereGeometry args={[0.24, 10, 8]} />
                          <meshStandardMaterial color={actorColor(actor.id)} />
                        </mesh>
                        <Html center>
                          <span className="scenario-v2-point-label">
                            {index === 0 ? "R" : `P${index}`}·{pointIndex + 1}
                          </span>
                        </Html>
                      </group>
                    ))}
                </group>
              ))}
            </group>
          ))}
          <OrbitControls
            target={target}
            enableDamping={false}
            maxPolarAngle={Math.PI / 2 - 0.03}
          />
        </Canvas>
        <div className="scenario-v2-scene-note">
          직선 지도 {length} m · 차폭 {width} m · 차로 -1 / -2 · +x 전방 / +y
          좌측
          {placement
            ? ` · 장면을 클릭해 ${placementText} 배치`
            : " · 드래그 회전 / 우클릭 이동 / 스크롤 확대"}
        </div>
      </div>
      <p className="scenario-v2-source">
        설정 좌표와 경로를 표시한 작성 도식입니다. 센서 영상·실도로 재구성·실행
        궤적은 아닙니다. 현재 지원 지도는 높이·곡선·교차로가 없는
        직선도로입니다.
      </p>
    </div>
  );
}

export default function ScenarioV2Workspace({
  notify,
  embedded = false,
  onExecution,
  onProject,
}: {
  notify?: (message: string) => void;
  embedded?: boolean;
  onExecution?: (id: string, projectId: string) => void;
  onProject?: (projectId: string) => void;
}) {
  const projects = useResource<{ items: { id: string; name: string }[] }>(
    "/projects",
  );
  const templates = useResource<{
    items: { id: string; scenario: Scenario }[];
  }>("/scenario-v2/templates");
  const identity = useResource<{ role: string; mode: string }>("/team/whoami");
  const capabilities = useResource<{
    handlers: { id: string; version: string; status: string }[];
  }>("/experiments/capabilities");
  const [project, setProject] = useState(
    () =>
      new URLSearchParams(location.hash.split("?")[1] ?? "").get("project") ??
      "default",
  );
  const records = useResource<{ items: RecordV2[] }>(
    `/scenario-v2?project_id=${encodeURIComponent(project)}`,
  );
  const [draft, setDraft] = useState<Scenario | null>(null),
    [saved, setSaved] = useState<RecordV2 | null>(null),
    [selected, setSelected] = useState("ego");
  const [placement, setPlacement] = useState<Placement>(null),
    [snap, setSnap] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const [seedText, setSeedText] = useState("42"),
    [speedText, setSpeedText] = useState("10,12"),
    [importText, setImportText] = useState("");
  const [sweep, setSweep] = useState<Sweep | null>(null),
    [choice, setChoice] = useState(0),
    [preview, setPreview] = useState<ExecutionPreview | null>(null);
  const [executionId, setExecutionId] = useState("");
  const pending = useRef<{ body: string; key: string } | null>(null);
  const editable =
    !!identity.data &&
    (identity.data.mode !== "project" ||
      ["Engineer", "Admin"].includes(identity.data.role));
  const dirty =
    !!draft && JSON.stringify(draft) !== JSON.stringify(saved?.scenario);
  const actor = draft?.actors.find((item) => item.id === selected);
  const warnings = draft ? laneChangeWarnings(draft) : [];
  const executionWarnings = sweep?.jobs[choice]
    ? laneChangeWarnings(sweep.jobs[choice].scenario)
    : [];
  const pathIssues =
    draft?.actors
      .flatMap((item) =>
        item.actions
          .filter((action) => action.kind === "follow_path")
          .map((action, index) => {
            const issue = waypointIssue(action.path, draft.duration_s);
            return issue ? `${item.id} path ${index + 1}: ${issue}` : "";
          }),
      )
      .filter(Boolean) ?? [];
  const native = capabilities.data?.handlers.find(
    (item) => item.id === "esmini-object",
  );
  useEffect(() => {
    if (templates.data?.items.length && !draft)
      setDraft(structuredClone(templates.data.items[0].scenario));
  }, [templates.data, draft]);
  useEffect(() => {
    if (
      projects.data?.items.length &&
      !projects.data.items.some((item) => item.id === project)
    )
      setProject(projects.data.items[0].id);
  }, [projects.data, project]);
  function change(mutator: (value: Scenario) => void) {
    if (!editable) return;
    setDraft((old) => {
      if (!old) return old;
      const value = structuredClone(old);
      mutator(value);
      return value;
    });
    setSweep(null);
    setPreview(null);
  }
  function actorChange(mutator: (value: Actor) => void) {
    change((value) => {
      const target = value.actors.find((item) => item.id === selected);
      if (target) mutator(target);
    });
  }
  function selectActor(id: string) {
    setSelected(id);
    setPlacement(null);
  }
  function addActor(kind: Actor["kind"]) {
    if (!draft || !editable || draft.actors.length >= 8) return;
    const item = createActor(kind, draft.actors, draft.map, draft.duration_s);
    change((value) => {
      value.actors.push(item);
    });
    selectActor(item.id);
  }
  function place(x: number, y: number) {
    if (!draft || !actor || !placement || !editable) return;
    const point = {
      x: Math.round(x * 100) / 100,
      y: snap
        ? laneCenter(
            Math.abs(y + draft.map.lane_width_m / 2) <=
              Math.abs(y + draft.map.lane_width_m * 1.5)
              ? -1
              : -2,
            draft.map.lane_width_m,
          )
        : Math.round(y * 100) / 100,
      heading_rad: actor.pose.heading_rad,
    };
    try {
      if (placement.kind === "actor")
        actorChange((value) => {
          value.pose = point;
        });
      else if (placement.kind === "route") {
        if (actor.route.length >= 32)
          throw new Error("Route는 최대 32개 점을 지원합니다.");
        const waypoint = appendWaypoint(actor.route, point);
        actorChange((value) => {
          value.route.push(waypoint);
        });
      } else {
        const index = placement.actionIndex ?? 0;
        if (actor.actions[index].path.length >= 100)
          throw new Error("시간 지정 경로는 최대 100개 점을 지원합니다.");
        const waypoint = appendWaypoint(
          actor.actions[index].path,
          point,
          draft.duration_s,
        ) as TimedPose;
        actorChange((value) => {
          value.actions[index].path.push(waypoint);
        });
      }
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  }
  async function operation(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await work();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }
  function key(body: unknown) {
    const raw = JSON.stringify(body);
    if (pending.current?.body !== raw)
      pending.current = { body: raw, key: crypto.randomUUID() };
    return pending.current.key;
  }
  function load(value: RecordV2) {
    setSaved(value);
    setDraft(structuredClone(value.scenario));
    selectActor(value.scenario.actors[0].id);
    setSweep(null);
    setPreview(null);
    setExecutionId("");
  }
  async function save() {
    if (!draft || !editable) return;
    if (pathIssues.length) {
      setError(pathIssues.join(" "));
      return;
    }
    await operation(async () => {
      const body = saved
        ? { expected_revision: saved.current_revision, scenario: draft }
        : { project_id: project, scenario: draft };
      const value = await api<RecordV2>(
        saved ? `/scenario-v2/${saved.id}/revisions` : "/scenario-v2",
        {
          method: "POST",
          body: JSON.stringify(body),
          headers: { "Idempotency-Key": key(body) },
        },
      );
      pending.current = null;
      load(value);
      records.reload();
      setMessage(`Saved immutable revision ${value.revision}.`);
    });
  }
  async function exportXml(format: string) {
    if (!saved) return;
    await operation(async () => {
      const response = await fetch(
        `/api/v1/scenario-v2/${saved.id}/export?format=${format}&revision=${saved.revision}`,
        { headers: authHeaders() },
      );
      if (!response.ok) throw new Error((await response.json()).detail);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${saved.id}.${format === "openscenario" ? "xosc" : "xodr"}`;
      anchor.click();
      URL.revokeObjectURL(url);
    });
  }
  function experimentSpec() {
    if (!saved || !sweep?.jobs[choice])
      throw new Error("Preview the saved sweep and select a run first");
    const job = sweep.jobs[choice];
    return {
      project_id: saved.project_id,
      name: `${saved.scenario.name} · seed ${job.seed}`,
      kind: "object_sim",
      adapter_id: "esmini-object",
      adapter_version: native?.version,
      input: {
        scenario_id: saved.id,
        scenario_revision: saved.revision,
        configuration_hash: job.configuration_hash,
      },
      seed: job.seed,
    };
  }
  async function submit() {
    if (!preview) return;
    await operation(async () => {
      const spec = experimentSpec();
      const experiment = await api<{ id: string; preview_hash: string }>(
        "/experiments",
        {
          method: "POST",
          body: JSON.stringify(spec),
          headers: { "Idempotency-Key": key(spec) },
        },
      );
      if (experiment.preview_hash !== preview.preview_hash)
        throw new Error(
          "The execution preview changed; inspect it again before submitting",
        );
      const execution = await api<{ id: string }>(
        `/experiments/${experiment.id}/executions`,
        {
          method: "POST",
          body: JSON.stringify({ preview_hash: preview.preview_hash }),
          headers: { "Idempotency-Key": `run-${experiment.id}` },
        },
      );
      pending.current = null;
      setExecutionId(execution.id);
      setMessage(
        "선택한 native 시험을 큐에 제출했습니다. 결과 재생에서 실행 상태와 실제 반환된 궤적을 확인하세요.",
      );
      notify?.("Object Sim 시험을 제출했습니다");
      onExecution?.(execution.id, spec.project_id);
    });
  }
  return (
    <div className="scenario-v2-workspace">
      {!embedded && (
        <PageHeading
          eyebrow="Object Sim"
          title="시나리오 작성 · 실행"
          description="차량·보행자를 배치하고 행동을 정한 뒤, 저장한 개정을 esmini로 실행하고 반환된 궤적을 재생합니다."
        />
      )}
      <ol className="scenario-v2-steps" aria-label="Object Sim authoring steps">
        <li>
          <span>1</span>
          <strong>장면과 행동 작성</strong>
          <small>템플릿 · 객체 배치 · 속도 · 트리거</small>
        </li>
        <li className={saved && !dirty ? "ready" : ""}>
          <span>2</span>
          <strong>시나리오 개정 저장</strong>
          <small>
            {saved
              ? `r${saved.revision}${dirty ? " · 수정한 초안을 저장하세요" : " · 입력 고정됨"}`
              : "초안을 저장하면 실행할 수 있습니다"}
          </small>
        </li>
        <li className={preview ? "ready" : ""}>
          <span>3</span>
          <strong>실행 범위 확인 · 제출</strong>
          <small>
            {preview
              ? `${preview.job_count}건 준비됨 · 명시적으로 제출`
              : "구성 미리보기 → 선택 시험 준비 → 제출"}
          </small>
        </li>
      </ol>
      <div className="scenario-v2-warning">
        <strong>작성 화면은 3D 도식입니다.</strong> 실행 엔진은 esmini의
        authored object kinematics입니다. 차량·보행자 좌표와 관찰 지표를
        반환하며, 카메라·LiDAR·날씨 물리나 실도로 안전성을 검증하지 않습니다. 이
        계약의 CARLA 실행은 미지원입니다.
      </div>
      <div className="scenario-v2-toolbar">
        <button
          className="button secondary"
          disabled={busy || !editable || !templates.data?.items.length}
          onClick={() => {
            const template =
              templates.data?.items.find(
                (item) => item.id === draft?.template,
              ) ?? templates.data?.items[0];
            if (!template) return;
            setDraft(structuredClone(template.scenario));
            setSaved(null);
            selectActor(
              template.scenario.actors.find((actor) => actor.role === "ego")
                ?.id ?? template.scenario.actors[0].id,
            );
            setSweep(null);
            setPreview(null);
            setExecutionId("");
            setError("");
            setMessage(
              "템플릿에서 새 초안을 시작했습니다. 기존 저장 개정은 보존됩니다.",
            );
          }}
        >
          새 시나리오 초안
        </button>
        <label>
          Project
          <select
            aria-label="Scenario project"
            disabled={busy}
            value={project}
            onChange={(event) => {
              setProject(event.target.value);
              setSaved(null);
              setSweep(null);
              setPreview(null);
              setExecutionId("");
              onProject?.(event.target.value);
              setMessage(
                "작성 중인 내용은 보존했습니다. 선택한 프로젝트에 새 시나리오로 저장하세요. 이전 프로젝트의 개정과 결과는 변경하지 않습니다.",
              );
            }}
          >
            <option value={project}>
              {projects.data?.items.find((item) => item.id === project)?.name ??
                project}
            </option>
            {projects.data?.items
              .filter((item) => item.id !== project)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          장면 템플릿
          <select
            aria-label="Scenario template"
            disabled={busy || !editable}
            value={draft?.template ?? ""}
            onChange={(event) => {
              const template = templates.data?.items.find(
                (item) => item.id === event.target.value,
              );
              if (template) {
                setDraft(structuredClone(template.scenario));
                setSaved(null);
                selectActor(
                  template.scenario.actors.find((actor) => actor.role === "ego")
                    ?.id ?? template.scenario.actors[0].id,
                );
                setSweep(null);
                setPreview(null);
                setExecutionId("");
              }
            }}
          >
            <option value="">Custom behavior</option>
            {templates.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {templateLabels[item.id] ? `${templateLabels[item.id]} · ` : ""}
                {item.scenario.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          저장한 시나리오 개정
          <select
            aria-label="Saved behavior scenario"
            disabled={busy}
            value={saved?.id ?? ""}
            onChange={(event) => {
              const value = records.data?.items.find(
                (item) => item.id === event.target.value,
              );
              if (value) load(value);
            }}
          >
            <option value="">New authored scenario</option>
            {records.data?.items.filter((item) => item.scenario.schema_version === "scenario-v2/1").map((item) => (
              <option key={item.id} value={item.id}>
                {item.scenario.name} · r{item.revision}
              </option>
            ))}
          </select>
        </label>
        <Badge
          status={
            native?.status === "available" ? "completed" : "not_evaluated"
          }
          label={
            native?.status === "available"
              ? "esmini available"
              : "esmini not configured"
          }
        />
      </div>
      {(error ||
        records.error ||
        templates.error ||
        projects.error ||
        capabilities.error ||
        identity.error) && (
        <p role="alert" className="scenario-v2-error">
          {error ||
            records.error ||
            templates.error ||
            projects.error ||
            capabilities.error ||
            identity.error}
        </p>
      )}
      {capabilities.data && native?.status !== "available" && (
        <p className="scenario-v2-warning" role="status">
          현재 서버에 esmini 실행 명령이 설정되지 않았습니다. 시나리오
          작성·저장은 가능하지만 native 실행을 준비하거나 제출할 수 없습니다.
          다른 엔진의 시험 결과로 대체하지 않습니다.
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {draft && (
        <>
          <div className="scenario-v2-layout">
            <section className="scenario-v2-editor">
              <fieldset
                className="scenario-v2-edit-fields"
                disabled={!editable || busy}
              >
                <legend>장면 · 지도 · 객체 작성</legend>
                <div className="scenario-v2-fields">
                  <label>
                    Name
                    <input
                      aria-label="Behavior scenario name"
                      value={draft.name}
                      onChange={(event) =>
                        change((value) => {
                          value.name = event.target.value;
                        })
                      }
                    />
                  </label>
                  <label>
                    Duration (s)
                    <input
                      aria-label="Scenario duration"
                      type="number"
                      min="1"
                      max="30"
                      value={draft.duration_s}
                      onChange={(event) =>
                        change((value) => {
                          value.duration_s = Number(event.target.value);
                        })
                      }
                    />
                  </label>
                  <label>
                    Timestep (s)
                    <input
                      aria-label="Scenario timestep"
                      type="number"
                      min=".02"
                      max=".2"
                      step=".02"
                      value={draft.timestep_s}
                      onChange={(event) =>
                        change((value) => {
                          value.timestep_s = Number(event.target.value);
                        })
                      }
                    />
                  </label>
                  <label>
                    직선 지도 길이 (m)
                    <input
                      aria-label="Map length"
                      type="number"
                      min="100"
                      max="2000"
                      value={draft.map.length_m}
                      onChange={(event) =>
                        change((value) => {
                          value.map.length_m = Number(event.target.value);
                        })
                      }
                    />
                  </label>
                  <label>
                    차로 폭 (m)
                    <input
                      aria-label="Map lane width"
                      type="number"
                      min="2.5"
                      max="5"
                      step=".1"
                      value={draft.map.lane_width_m}
                      onChange={(event) =>
                        change((value) => {
                          value.map.lane_width_m = Number(event.target.value);
                        })
                      }
                    />
                  </label>
                </div>
                <p className="scenario-v2-help">
                  두 직선 차로 -1 / -2 · 길이 100–2,000m · 차폭 2.5–5m.
                  곡선·교차로·외부 지도는 미지원입니다.
                </p>
                <div
                  className="scenario-v2-actor-tabs"
                  aria-label="Scenario actors"
                >
                  {draft.actors.map((item) => (
                    <button
                      className={`button ${item.id === selected ? "" : "secondary"}`}
                      key={item.id}
                      aria-pressed={item.id === selected}
                      onClick={() => selectActor(item.id)}
                    >
                      <span
                        className="scenario-v2-color-dot"
                        style={{ background: actorColor(item.id) }}
                      />
                      {item.id}
                    </button>
                  ))}
                </div>
                <div className="scenario-v2-buttons">
                  <button
                    className="button secondary"
                    disabled={draft.actors.length >= 8}
                    onClick={() => addActor("vehicle")}
                  >
                    차량 추가
                  </button>
                  <button
                    className="button secondary"
                    disabled={draft.actors.length >= 8}
                    onClick={() => addActor("pedestrian")}
                  >
                    보행자 추가
                  </button>
                  <span className="scenario-v2-help">
                    {draft.actors.length} / 8 객체 · ego 정확히 1개
                  </span>
                </div>
                {actor && (
                  <>
                    <div className="scenario-v2-fields">
                      <label>
                        객체 종류
                        <select
                          aria-label="Actor kind"
                          value={actor.kind}
                          onChange={(event) =>
                            actorChange((value) => {
                              const kind = event.target.value as Actor["kind"];
                              value.kind = kind;
                              value.dimensions =
                                kind === "vehicle"
                                  ? {
                                      length_m: 4.5,
                                      width_m: 1.8,
                                      height_m: 1.5,
                                    }
                                  : {
                                      length_m: 0.5,
                                      width_m: 0.5,
                                      height_m: 1.75,
                                    };
                              if (kind === "pedestrian") {
                                value.speed_mps = 0;
                                value.route = [];
                                if (
                                  !value.actions.some(
                                    (action) => action.kind === "follow_path",
                                  ) &&
                                  value.actions.length < 16
                                )
                                  value.actions.push(
                                    defaultAction(
                                      "follow_path",
                                      value,
                                      draft.map,
                                      draft.duration_s,
                                    ),
                                  );
                              }
                            })
                          }
                        >
                          <option value="vehicle">Vehicle · 차량</option>
                          <option value="pedestrian">
                            Pedestrian · 보행자
                          </option>
                        </select>
                      </label>
                      <label>
                        역할
                        <select
                          aria-label="Actor role"
                          value={actor.role}
                          onChange={(event) =>
                            change((value) => {
                              if (event.target.value === "ego")
                                value.actors.forEach((item) => {
                                  item.role =
                                    item.id === selected ? "ego" : "other";
                                });
                            })
                          }
                        >
                          <option value="other" disabled={actor.role === "ego"}>
                            Other
                          </option>
                          <option value="ego">Ego</option>
                        </select>
                      </label>
                      <label>
                        x (m)
                        <input
                          aria-label="Actor x"
                          type="number"
                          value={actor.pose.x}
                          onChange={(event) =>
                            actorChange((value) => {
                              value.pose.x = Number(event.target.value);
                            })
                          }
                        />
                      </label>
                      <label>
                        y (m)
                        <input
                          aria-label="Actor y"
                          type="number"
                          value={actor.pose.y}
                          onChange={(event) =>
                            actorChange((value) => {
                              value.pose.y = Number(event.target.value);
                            })
                          }
                        />
                      </label>
                      <label>
                        Heading (rad)
                        <input
                          aria-label="Actor heading"
                          type="number"
                          step=".1"
                          value={actor.pose.heading_rad}
                          onChange={(event) =>
                            actorChange((value) => {
                              value.pose.heading_rad = Number(
                                event.target.value,
                              );
                            })
                          }
                        />
                      </label>
                      <label>
                        Speed (m/s)
                        <input
                          aria-label="Actor speed"
                          disabled={actor.kind === "pedestrian"}
                          type="number"
                          min="0"
                          max="60"
                          value={actor.speed_mps}
                          onChange={(event) =>
                            actorChange((value) => {
                              value.speed_mps = Number(event.target.value);
                            })
                          }
                        />
                      </label>
                      {(["length_m", "width_m", "height_m"] as const).map(
                        (field) => (
                          <label key={field}>
                            {field.replace("_m", "")} (m)
                            <input
                              aria-label={`Actor ${field}`}
                              type="number"
                              step=".1"
                              value={actor.dimensions[field]}
                              onChange={(event) =>
                                actorChange((value) => {
                                  value.dimensions[field] = Number(
                                    event.target.value,
                                  );
                                })
                              }
                            />
                          </label>
                        ),
                      )}
                    </div>
                    <p className="scenario-v2-help">
                      현재 시작 위치:{" "}
                      {startingLane(actor.pose.y, draft.map.lane_width_m)
                        ? `차로 ${startingLane(actor.pose.y, draft.map.lane_width_m)}`
                        : "차로 밖"}{" "}
                      · 종류 변경 시 기본 크기를 적용합니다. Ego를 바꾸면 이전
                      ego는 other가 됩니다.
                      {actor.kind === "pedestrian"
                        ? " 보행자는 초기 속도 0, route 없이 시간 지정 경로로 이동합니다."
                        : ""}
                    </p>
                    <div className="scenario-v2-buttons">
                      <button
                        className="button secondary"
                        onClick={() =>
                          setPlacement(
                            placement?.kind === "actor"
                              ? null
                              : { kind: "actor" },
                          )
                        }
                      >
                        {placement?.kind === "actor"
                          ? "3D 배치 마치기"
                          : "3D에서 선택 객체 이동"}
                      </button>
                      {([-1, -2] as const).map((lane) => (
                        <button
                          key={lane}
                          className="button secondary"
                          onClick={() =>
                            actorChange((value) => {
                              value.pose.y = laneCenter(
                                lane,
                                draft.map.lane_width_m,
                              );
                            })
                          }
                        >
                          차로 {lane} 중앙 배치
                        </button>
                      ))}
                      <label className="scenario-v2-checkbox">
                        <input
                          type="checkbox"
                          aria-label="Snap placement to lane"
                          checked={snap}
                          onChange={(event) => setSnap(event.target.checked)}
                        />
                        클릭 배치 시 차로 snap
                      </label>
                      {actor.role !== "ego" && (
                        <button
                          className="button secondary"
                          disabled={actorReferences(draft, actor.id) > 0}
                          onClick={() => {
                            change((value) => {
                              value.actors = value.actors.filter(
                                (item) => item.id !== selected,
                              );
                            });
                            selectActor(
                              draft.actors.find((item) => item.role === "ego")!
                                .id,
                            );
                          }}
                        >
                          Remove actor
                        </button>
                      )}
                    </div>
                    {actor.role !== "ego" &&
                      actorReferences(draft, actor.id) > 0 && (
                        <p className="scenario-v2-help">
                          이 객체를 참조하는 trigger·observer·sweep가{" "}
                          {actorReferences(draft, actor.id)}개입니다. 참조를
                          수정하면 삭제할 수 있습니다.
                        </p>
                      )}
                    <h3>객체 행동과 시작 조건</h3>
                    <ActionTimeline actor={actor} duration={draft.duration_s} />
                    <p className="scenario-v2-help">
                      작성 계획입니다. 거리·속도 trigger의 발생 시각은 실행 전
                      알 수 없습니다. 실제 행동은 결과에서 확인합니다.
                    </p>
                    {actor.actions.map((action, index) => (
                      <div className="scenario-v2-action" key={index}>
                        <div className="scenario-v2-fields">
                          <label>
                            Action
                            <select
                              aria-label={`Action ${index + 1} kind`}
                              value={action.kind}
                              onChange={(event) => {
                                setPlacement(null);
                                actorChange((value) => {
                                  value.actions[index] = defaultAction(
                                    event.target.value as Action["kind"],
                                    value,
                                    draft.map,
                                    draft.duration_s,
                                  );
                                });
                              }}
                            >
                              {[
                                "speed",
                                "stop",
                                "lane_change",
                                "follow_path",
                              ].map((kind) => (
                                <option key={kind}>{kind}</option>
                              ))}
                            </select>
                          </label>
                          <label>
                            Trigger
                            <select
                              aria-label={`Action ${index + 1} trigger`}
                              value={action.trigger.kind}
                              onChange={(event) =>
                                actorChange((value) => {
                                  const trigger = value.actions[index].trigger;
                                  trigger.kind = event.target
                                    .value as Trigger["kind"];
                                  trigger.actor_id =
                                    trigger.kind === "time" ? null : selected;
                                  trigger.target_actor_id =
                                    trigger.kind === "distance"
                                      ? (draft.actors.find(
                                          (item) => item.id !== selected,
                                        )?.id ?? selected)
                                      : null;
                                })
                              }
                            >
                              <option value="time">Time (s)</option>
                              <option value="distance">
                                Actor distance (m)
                              </option>
                              <option value="object_state">
                                Actor speed (m/s)
                              </option>
                            </select>
                          </label>
                          <label>
                            Trigger value
                            <input
                              aria-label={`Action ${index + 1} trigger value`}
                              type="number"
                              value={action.trigger.value}
                              onChange={(event) =>
                                actorChange((value) => {
                                  value.actions[index].trigger.value = Number(
                                    event.target.value,
                                  );
                                })
                              }
                            />
                          </label>
                          <label>
                            Rule
                            <select
                              aria-label={`Action ${index + 1} rule`}
                              value={action.trigger.rule}
                              onChange={(event) =>
                                actorChange((value) => {
                                  value.actions[index].trigger.rule = event
                                    .target.value as Trigger["rule"];
                                })
                              }
                            >
                              <option>greaterThan</option>
                              <option>lessThan</option>
                            </select>
                          </label>
                          {action.trigger.kind !== "time" && (
                            <label>
                              Trigger actor
                              <select
                                aria-label={`Action ${index + 1} trigger actor`}
                                value={action.trigger.actor_id ?? ""}
                                onChange={(event) =>
                                  actorChange((value) => {
                                    value.actions[index].trigger.actor_id =
                                      event.target.value;
                                  })
                                }
                              >
                                {draft.actors.map((item) => (
                                  <option key={item.id}>{item.id}</option>
                                ))}
                              </select>
                            </label>
                          )}
                          {action.trigger.kind === "distance" && (
                            <label>
                              Relative to
                              <select
                                aria-label={`Action ${index + 1} relative actor`}
                                value={action.trigger.target_actor_id ?? ""}
                                onChange={(event) =>
                                  actorChange((value) => {
                                    value.actions[
                                      index
                                    ].trigger.target_actor_id =
                                      event.target.value;
                                  })
                                }
                              >
                                {draft.actors.map((item) => (
                                  <option key={item.id}>{item.id}</option>
                                ))}
                              </select>
                            </label>
                          )}
                          {action.kind === "speed" && (
                            <label>
                              Target speed
                              <input
                                aria-label={`Action ${index + 1} target speed`}
                                type="number"
                                value={action.target_speed_mps ?? 0}
                                onChange={(event) =>
                                  actorChange((value) => {
                                    value.actions[index].target_speed_mps =
                                      Number(event.target.value);
                                  })
                                }
                              />
                            </label>
                          )}
                          {action.kind === "lane_change" && (
                            <label>
                              Target lane
                              <select
                                aria-label={`Action ${index + 1} target lane`}
                                value={action.target_lane ?? -1}
                                onChange={(event) =>
                                  actorChange((value) => {
                                    value.actions[index].target_lane = Number(
                                      event.target.value,
                                    );
                                  })
                                }
                              >
                                <option value={-1}>
                                  -1 · y=
                                  {laneCenter(-1, draft.map.lane_width_m)} m
                                </option>
                                <option value={-2}>
                                  -2 · y=
                                  {laneCenter(-2, draft.map.lane_width_m)} m
                                </option>
                              </select>
                            </label>
                          )}
                          <label>
                            Transition (s)
                            <input
                              aria-label={`Action ${index + 1} transition`}
                              type="number"
                              min=".1"
                              step=".1"
                              value={action.transition_s}
                              onChange={(event) =>
                                actorChange((value) => {
                                  value.actions[index].transition_s = Number(
                                    event.target.value,
                                  );
                                })
                              }
                            />
                          </label>
                        </div>
                        {action.kind === "follow_path" && (
                          <>
                            <p className="scenario-v2-help">
                              각 점의 time_s는 장면 시작부터의 절대 시각입니다.
                              2개 이상, 엄격히 증가하는 시각으로{" "}
                              {draft.duration_s}초 안에 작성하세요. Path 이동은
                              경로 시각을 사용합니다.
                            </p>
                            <WaypointEditor
                              label={`Action ${index + 1} timed path`}
                              points={action.path}
                              duration={draft.duration_s}
                              onChange={(points) =>
                                actorChange((value) => {
                                  value.actions[index].path =
                                    points as TimedPose[];
                                })
                              }
                            />
                            <button
                              className="button secondary"
                              onClick={() =>
                                setPlacement(
                                  placement?.kind === "path" &&
                                    placement.actionIndex === index
                                    ? null
                                    : { kind: "path", actionIndex: index },
                                )
                              }
                            >
                              {placement?.kind === "path" &&
                              placement.actionIndex === index
                                ? "경로 점 배치 마치기"
                                : `3D에서 행동 ${index + 1} 경로 점 추가`}
                            </button>
                          </>
                        )}
                        <button
                          className="button secondary"
                          disabled={!editable}
                          onClick={() => {
                            setPlacement(null);
                            actorChange((value) => {
                              value.actions.splice(index, 1);
                            });
                          }}
                        >
                          Remove action
                        </button>
                      </div>
                    ))}
                    <button
                      className="button secondary"
                      disabled={!editable || actor.actions.length >= 16}
                      onClick={() =>
                        actorChange((value) => {
                          value.actions.push(
                            defaultAction(
                              actor.kind === "pedestrian"
                                ? "follow_path"
                                : "speed",
                              value,
                              draft.map,
                              draft.duration_s,
                            ),
                          );
                        })
                      }
                    >
                      행동 추가
                    </button>
                    <details>
                      <summary>Route waypoint 편집</summary>
                      <p className="scenario-v2-help">
                        Route는 차량의 경로 선언입니다. 시간별 위치는
                        follow_path로 작성하세요.
                      </p>
                      {actor.kind === "pedestrian" ? (
                        <p className="scenario-v2-help">
                          보행자 이동은 시간 지정 경로에서 작성합니다.
                        </p>
                      ) : (
                        <>
                          <WaypointEditor
                            label="Actor route"
                            points={actor.route}
                            onChange={(points) =>
                              actorChange((value) => {
                                value.route = points;
                              })
                            }
                          />
                          <button
                            className="button secondary"
                            onClick={() =>
                              setPlacement(
                                placement?.kind === "route"
                                  ? null
                                  : { kind: "route" },
                              )
                            }
                          >
                            {placement?.kind === "route"
                              ? "Route 배치 마치기"
                              : "3D에서 route 점 추가"}
                          </button>
                        </>
                      )}
                    </details>
                    <details>
                      <summary>고급 JSON · observer criteria</summary>
                      <JsonField
                        label="Actor route JSON"
                        value={actor.route}
                        onSave={(value) => {
                          const poses = validatePoses(value, false);
                          actorChange((target) => {
                            target.route = poses as Pose[];
                          });
                        }}
                        onError={setError}
                      />
                      <JsonField
                        label="Observer criteria"
                        value={draft.observers}
                        onSave={(value) =>
                          change((target) => {
                            target.observers = value as Scenario["observers"];
                          })
                        }
                        onError={setError}
                      />
                    </details>
                  </>
                )}
              </fieldset>
            </section>
            <section className="scenario-v2-scene-column">
              <Schematic
                key={
                  saved
                    ? `${saved.id}:${saved.revision}`
                    : (draft.template ?? "custom")
                }
                draft={draft}
                selected={selected}
                placement={editable && !busy ? placement : null}
                select={selectActor}
                update={place}
              />
              <p className="scenario-v2-source">
                {saved
                  ? `Revision ${saved.revision} · ${saved.manifest_hash.slice(0, 16)}`
                  : "Unsaved authored draft"}
                {dirty ? " · draft changes need saving" : ""}
              </p>
              {warnings.length > 0 && (
                <div
                  className="scenario-v2-warning"
                  role="status"
                  aria-label="Lane change authoring warnings"
                >
                  <strong>차로 변경 설정 확인</strong>
                  {warnings.map((warning) => (
                    <p key={warning}>{warning}</p>
                  ))}
                  <small>
                    저장·제출은 허용됩니다. 이 warning은 실제 실행 행동 판정을
                    대신하지 않습니다.
                  </small>
                </div>
              )}
              {pathIssues.length > 0 && (
                <div className="scenario-v2-error" role="alert">
                  {pathIssues.map((issue) => (
                    <p key={issue}>{issue}</p>
                  ))}
                </div>
              )}
              <div className="scenario-v2-buttons">
                <button
                  className="button"
                  disabled={
                    busy || !editable || !dirty || pathIssues.length > 0
                  }
                  onClick={() => void save()}
                >
                  {saved ? "새 개정 저장" : "시나리오 저장"}
                </button>
                <button
                  className="button secondary"
                  disabled={busy || !saved}
                  onClick={() => void exportXml("openscenario")}
                >
                  Export OpenSCENARIO
                </button>
                <button
                  className="button secondary"
                  disabled={busy || !saved}
                  onClick={() => void exportXml("opendrive")}
                >
                  Export OpenDRIVE
                </button>
              </div>
            </section>
          </div>
          <section className="scenario-v2-execution">
            <h2>실행 범위 확인 · 선택 시험 제출</h2>
            <p>
              저장한 개정의 입력과 작업량을 확인한 뒤 시험 한 건을 선택합니다.
              미리보기만으로는 실행되지 않습니다. 이 템플릿은 결정적 행동이므로
              seed 변경을 독립 장면 다양성으로 계산하지 않습니다.
            </p>
            <div className="scenario-v2-fields">
              <label>
                Ego speed values (m/s)
                <input
                  aria-label="Sweep ego speeds"
                  disabled={busy || !editable}
                  value={speedText}
                  onChange={(event) => setSpeedText(event.target.value)}
                />
              </label>
              <label>
                Seeds
                <input
                  aria-label="Sweep seeds"
                  disabled={busy || !editable}
                  value={seedText}
                  onChange={(event) => {
                    setSeedText(event.target.value);
                    setSweep(null);
                    setPreview(null);
                  }}
                />
              </label>
            </div>
            <div className="scenario-v2-buttons">
              <button
                className="button secondary"
                disabled={busy || !editable}
                onClick={() => {
                  const values = speedText
                    .split(",")
                    .map((item) => Number(item.trim()));
                  if (
                    !values.length ||
                    values.some(
                      (value) =>
                        !Number.isFinite(value) || value < 0 || value > 60,
                    )
                  ) {
                    setError("Use finite speed values from 0 to 60");
                    return;
                  }
                  change((value) => {
                    value.sweep = [
                      {
                        actor_id: value.actors.find(
                          (item) => item.role === "ego",
                        )!.id,
                        field: "speed_mps",
                        values,
                      },
                    ];
                  });
                }}
              >
                속도 조합을 초안에 적용
              </button>
              <button
                className="button secondary"
                disabled={busy || !saved || dirty}
                onClick={() =>
                  void operation(async () => {
                    const value = await api<Sweep>(
                      `/scenario-v2/${saved!.id}/sweep-preview?revision=${saved!.revision}`,
                      {
                        method: "POST",
                        body: JSON.stringify({
                          seeds: seedText
                            .split(",")
                            .map((item) => Number(item.trim())),
                        }),
                      },
                    );
                    setSweep(value);
                    setChoice(0);
                    setPreview(null);
                  })
                }
              >
                1. 저장 개정의 실행 범위 확인
              </button>
            </div>
            {sweep && (
              <>
                {executionWarnings.length > 0 && (
                  <div
                    className="scenario-v2-warning"
                    role="status"
                    aria-label="Selected execution lane change warnings"
                  >
                    <strong>선택한 시험의 차로 변경 설정</strong>
                    {executionWarnings.map((warning) => (
                      <p key={warning}>{warning}</p>
                    ))}
                  </div>
                )}
                <p>
                  {sweep.job_count}개 구성 · {sweep.configured_seconds}초의 설정
                  작업량 · 미리보기에서 제출된 시험 0건.
                </p>
                <label>
                  실행할 구성 · seed
                  <select
                    aria-label="Selected sweep run"
                    value={choice}
                    onChange={(event) => {
                      setChoice(Number(event.target.value));
                      setPreview(null);
                    }}
                  >
                    {sweep.jobs.map((job, index) => (
                      <option
                        key={`${job.configuration_hash}-${job.seed}`}
                        value={index}
                      >
                        {job.selection
                          .map(
                            (axis) =>
                              `${axis.actor_id} ${axis.field}=${axis.value}`,
                          )
                          .join(", ") || "Base configuration"}{" "}
                        · seed {job.seed}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="scenario-v2-buttons">
                  <button
                    className="button secondary"
                    disabled={
                      busy || !editable || native?.status !== "available"
                    }
                    onClick={() =>
                      void operation(async () => {
                        setPreview(
                          await api<ExecutionPreview>("/experiments/preview", {
                            method: "POST",
                            body: JSON.stringify(experimentSpec()),
                          }),
                        );
                      })
                    }
                  >
                    2. 선택한 시험 준비
                  </button>
                  <button
                    className="button"
                    disabled={busy || !editable || !preview}
                    onClick={() => void submit()}
                  >
                    3. 선택한 native 시험 제출
                  </button>
                  {executionId && (
                    <button
                      className="button secondary"
                      onClick={() =>
                        onExecution
                          ? onExecution(executionId, project)
                          : (location.hash = `experiments?project=${encodeURIComponent(project)}&execution=${executionId}`)
                      }
                    >
                      제출 결과 재생 열기
                    </button>
                  )}
                </div>
                {preview && (
                  <p>
                    Prepared {preview.job_count} run ·{" "}
                    {preview.execution_fidelity} · hash{" "}
                    {preview.preview_hash.slice(0, 16)}. Execution completion
                    and observer verdict are reported separately.
                  </p>
                )}
              </>
            )}
          </section>
        </>
      )}
      <details className="scenario-v2-import">
        <summary>Import supported OpenSCENARIO XML</summary>
        <p>
          Only the lossless AXIOM exported subset is supported. Other elements
          or modified behavior are rejected.
        </p>
        <textarea
          aria-label="OpenSCENARIO XML import"
          rows={6}
          value={importText}
          onChange={(event) => setImportText(event.target.value)}
        />
        <button
          className="button secondary"
          disabled={busy || !editable || !importText}
          onClick={() =>
            void operation(async () => {
              const body = { project_id: project, xml: importText };
              const value = await api<RecordV2>("/scenario-v2/import", {
                method: "POST",
                body: JSON.stringify(body),
                headers: { "Idempotency-Key": key(body) },
              });
              pending.current = null;
              load(value);
              records.reload();
              setMessage(
                "Imported the pinned supported subset as a new authored scenario.",
              );
            })
          }
        >
          Import as new scenario
        </button>
      </details>
    </div>
  );
}

function ActionTimeline({
  actor,
  duration,
}: {
  actor: Actor;
  duration: number;
}) {
  return (
    <div className="scenario-v2-timeline" aria-label="Authored action timeline">
      <div className="scenario-v2-timeline-scale">
        <span>0 s</span>
        <span>{duration / 2} s</span>
        <span>{duration} s</span>
      </div>
      {!actor.actions.length && (
        <p className="scenario-v2-help">
          행동을 추가하면 작성 시간 조건이 표시됩니다.
        </p>
      )}
      {actor.actions.map((action, index) => {
        const timed = action.trigger.kind === "time",
          start =
            action.kind === "follow_path"
              ? (action.path[0]?.time_s ?? 0)
              : action.trigger.value;
        const end =
          action.kind === "follow_path"
            ? (action.path.at(-1)?.time_s ?? start)
            : start + action.transition_s;
        return (
          <div className="scenario-v2-timeline-row" key={index}>
            <span>
              {index + 1}. {action.kind}
              {action.kind === "lane_change"
                ? ` → ${action.target_lane}`
                : action.kind === "speed"
                  ? ` → ${action.target_speed_mps} m/s`
                  : ""}
            </span>
            <div className="scenario-v2-timeline-track">
              {timed ? (
                <span
                  className="scenario-v2-timeline-bar"
                  style={{
                    left: `${Math.min(98, Math.max(0, (start / duration) * 100))}%`,
                    width: `${Math.max(2, (Math.min(duration, end - start) / duration) * 100)}%`,
                    background: actorColor(actor.id),
                  }}
                  title={`작성 조건: t ${action.trigger.rule === "greaterThan" ? ">" : "<"} ${action.trigger.value}s`}
                />
              ) : (
                <small>
                  {action.trigger.kind === "distance"
                    ? "거리 조건"
                    : "속도 조건"}{" "}
                  · 시각 미정
                </small>
              )}
            </div>
            <small>
              {timed
                ? `t ${action.trigger.rule === "greaterThan" ? ">" : "<"} ${action.trigger.value}s`
                : `${action.trigger.actor_id} ${action.trigger.rule === "greaterThan" ? ">" : "<"} ${action.trigger.value}`}
            </small>
          </div>
        );
      })}
    </div>
  );
}

function WaypointEditor({
  label,
  points,
  duration,
  onChange,
}: {
  label: string;
  points: Pose[] | TimedPose[];
  duration?: number;
  onChange: (points: Pose[] | TimedPose[]) => void;
}) {
  const timed = duration !== undefined,
    issue = waypointIssue(points, duration),
    limit = timed ? 100 : 32;
  function edit(index: number, field: keyof TimedPose, value: number) {
    const next = structuredClone(points);
    (next[index] as TimedPose)[field] = value;
    onChange(next);
  }
  const last = points.at(-1),
    end =
      last && "time_s" in last && typeof last.time_s === "number"
        ? last.time_s
        : 0;
  return (
    <div className="scenario-v2-waypoints" aria-label={label}>
      <div className="scenario-v2-waypoint-table">
        <table>
          <thead>
            <tr>
              <th>#</th>
              {timed && <th>time_s</th>}
              <th>x (m)</th>
              <th>y (m)</th>
              <th>heading (rad)</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {points.map((point, index) => (
              <tr key={index}>
                <td>{index + 1}</td>
                {timed && (
                  <td>
                    <input
                      type="number"
                      min="0"
                      max={duration}
                      step=".1"
                      aria-label={`${label} waypoint ${index + 1} time_s`}
                      value={(point as TimedPose).time_s}
                      onChange={(event) =>
                        edit(index, "time_s", Number(event.target.value))
                      }
                    />
                  </td>
                )}
                {(["x", "y", "heading_rad"] as const).map((field) => (
                  <td key={field}>
                    <input
                      type="number"
                      step=".1"
                      aria-label={`${label} waypoint ${index + 1} ${field}`}
                      value={point[field]}
                      onChange={(event) =>
                        edit(index, field, Number(event.target.value))
                      }
                    />
                  </td>
                ))}
                <td>
                  <button
                    className="button secondary"
                    aria-label={`${label} remove waypoint ${index + 1}`}
                    onClick={() =>
                      onChange(points.filter((_, i) => i !== index))
                    }
                  >
                    삭제
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {issue && (
        <p className="scenario-v2-inline-warning" role="status">
          {issue}
        </p>
      )}
      <button
        className="button secondary"
        disabled={points.length >= limit || (timed && end >= duration!)}
        onClick={() => {
          const pose = {
            x: (last?.x ?? -1) + 1,
            y: last?.y ?? 0,
            heading_rad: last?.heading_rad ?? 0,
          };
          const next = appendWaypoint(points, pose, duration);
          onChange([...points, next]);
        }}
      >
        {label} · 점 추가
      </button>
    </div>
  );
}

function validatePoses(value: unknown, timed: boolean) {
  if (
    !Array.isArray(value) ||
    value.some(
      (entry) =>
        !entry ||
        typeof entry !== "object" ||
        ["x", "y", "heading_rad", ...(timed ? ["time_s"] : [])].some(
          (field) =>
            typeof entry[field] !== "number" || !Number.isFinite(entry[field]),
        ),
    )
  )
    throw new Error(
      "Each waypoint requires finite x, y, heading_rad and, for a timed path, time_s",
    );
  return value;
}

function JsonField({
  label,
  value,
  onSave,
  onError,
}: {
  label: string;
  value: unknown;
  onSave: (value: unknown) => void;
  onError: (message: string) => void;
}) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  useEffect(() => setText(JSON.stringify(value, null, 2)), [value]);
  return (
    <label>
      {label}
      <textarea
        aria-label={label}
        rows={5}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onBlur={() => {
          try {
            const parsed: unknown = JSON.parse(text);
            if (!Array.isArray(parsed)) throw new Error("Use a JSON array");
            onSave(parsed);
          } catch (error) {
            onError(
              `${label}: ${error instanceof Error ? error.message : String(error)}`,
            );
          }
        }}
      />
    </label>
  );
}
