import { useEffect, useMemo, useRef, useState } from "react";
import { api, authHeaders, useResource } from "../../api";
import CityMapCanvas from "./CityMapCanvas";
import CityBehaviorEditor from "./CityBehaviorEditor";
import {
  catalogRef,
  changedFields,
  closestLanePoint,
  newCityActor,
  newCityScenario,
  newCityRgbSensor,
  sameCityConfiguration,
  savedCityScenarioRoute,
  enableReactiveActor,
} from "./city-contract";
import type {
  CatalogItem,
  CatalogRef,
  CityActor,
  CityAsset,
  CityControllerKind,
  CityMap,
  CityMapRecord,
  CityPose,
  CityRecord,
  CityScenario,
  CityValidation,
} from "./city-contract";
import "./city-sim.css";
type Controller = {
  name: string;
  mode: CityControllerKind;
  status: string;
  description: string;
  adapter_id: string;
  adapter_version: string;
};
type Preview = {
  preview_hash: string;
  execution_fidelity: string;
  job_count: number;
  prepared: unknown;
};
const controllerLabels: Record<CityControllerKind, string> = {
  route_follow: "피드백 경로 추종",
  traffic: "교통 행동 · 간격/신호",
  autoware: "Autoware · GT planning/control",
  timed_replay: "시간 지정 궤적 재생",
  static: "고정 객체",
  reactive: "반응형 · 재계획/양보/우회",
};
export default function CityScenarioEditor({
  onProject,
  onExecution,
  notify,
  initialMap,
}: {
  initialMap?: CatalogRef;
  onProject?: (id: string) => void;
  onExecution?: (id: string, project: string) => void;
  notify?: (message: string) => void;
}) {
  const [project, setProject] = useState(
    () =>
      new URLSearchParams(location.hash.split("?")[1] ?? "").get("project") ??
      "default",
  );
  const projects = useResource<{ items: { id: string; name: string }[] }>(
      "/projects",
    ),
    identity = useResource<{ role: string; mode: string }>("/team/whoami");
  const maps = useResource<{ items: CatalogItem<CityMap>[] }>(
      `/maps?project_id=${encodeURIComponent(project)}&limit=1000`,
    ),
    assets = useResource<{ items: CatalogItem<CityAsset>[] }>(
      `/assets?project_id=${encodeURIComponent(project)}&limit=1000`,
    ),
    controllers = useResource<{ items: CatalogItem<Controller>[] }>(
      `/controllers?project_id=${encodeURIComponent(project)}&limit=1000`,
    );
  const records = useResource<{ items: CityRecord[] }>(
      `/scenario-v2?project_id=${encodeURIComponent(project)}`,
    ),
    capabilities = useResource<{
      handlers: {
        id: string;
        version: string;
        status: string;
        description?: string;
      }[];
    }>("/experiments/capabilities");
  const [mapId, setMapId] = useState(""),
    [draft, setDraft] = useState<CityScenario | null>(null),
    [saved, setSaved] = useState<CityRecord | null>(null),
    [selected, setSelected] = useState("ego"),
    [assetId, setAssetId] = useState(""),
    [assetSearch, setAssetSearch] = useState("");
  const [mode, setMode] = useState<"visual" | "yaml">("visual"),
    [yaml, setYaml] = useState(""),
    [yamlChanged, setYamlChanged] = useState(false),
    [validation, setValidation] = useState<CityValidation | null>(null),
    [preview, setPreview] = useState<Preview | null>(null);
  const [placement, setPlacement] = useState<
      "start" | "via" | "goal" | "destination" | "behavior" | null
    >(null),
    [signalId, setSignalId] = useState(""),
    [targetAction, setTargetAction] = useState(""),
    [via, setVia] = useState<CityPose[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const pending = useRef<{ body: string; key: string } | null>(null),
    originalYaml = useRef<string | null>(null);
  const initialResume = useRef(new URLSearchParams(location.hash.split("?")[1] ?? ""));
  const [resumeVersion, setResumeVersion] = useState(0);
  const [resumeLoading, setResumeLoading] = useState(false);
  const mapRevision =
    initialMap?.id === mapId && !draft
      ? initialMap.revision
      : draft?.map.id === mapId
        ? draft.map.revision
        : maps.data?.items.find((item) => item.id === mapId)?.revision;
  const mapResource = useResource<CityMapRecord>(
    mapId
      ? `/maps/${encodeURIComponent(mapId)}?project_id=${encodeURIComponent(project)}${mapRevision ? `&revision=${mapRevision}` : ""}`
      : null,
  );
  const map = useMemo(
    () =>
      mapResource.data
        ? { ...mapResource.data.definition, lanes: mapResource.data.lanes }
        : null,
    [mapResource.data],
  );
  const actor = draft?.actors.find((item) => item.id === selected),
    chosenAsset = assets.data?.items.find((item) => item.id === assetId),
    writable =
      !!identity.data &&
      (identity.data.mode !== "project" ||
        ["Engineer", "Admin"].includes(identity.data.role));
  const cityRecords =
    records.data?.items.filter((item) =>
      ["scenario-v2/2", "scenario-v2/3"].includes(item.scenario.schema_version),
    ) ?? [];
  const native = capabilities.data?.handlers.find(
    (item) => item.id === "carla-city-object",
  );
  const dirty =
    !!draft && (yamlChanged || !sameCityConfiguration(draft, saved?.scenario));
  const changes = useMemo(
    () => (draft && saved ? changedFields(saved.scenario, draft) : []),
    [draft, saved],
  );
  useEffect(() => {
    const scenarioId = initialResume.current.get("scenario");
    const revision = Number(initialResume.current.get("revision"));
    if (!scenarioId || initialResume.current.get("project") !== project) return;
    if (!Number.isSafeInteger(revision) || revision < 1) {
      setError("이어하기 링크의 저장 개정을 확인할 수 없습니다. 베타 작업공간에서 과제를 다시 여세요.");
      return;
    }
    let active = true;
    setResumeLoading(true);
    api<CityRecord>(`/scenario-v2/${encodeURIComponent(scenarioId)}?revision=${revision}`)
      .then(value => {
        if (!active) return;
        if (value.project_id !== project || value.id !== scenarioId || value.revision !== revision || !["scenario-v2/2", "scenario-v2/3"].includes(value.scenario.schema_version))
          throw new Error("이어하기의 프로젝트·개정과 저장된 도시 시나리오가 다릅니다.");
        reset(value);
        setNotice(`저장된 시나리오 개정 r${revision}에서 이어갑니다.`);
      })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (active) setResumeLoading(false); });
    return () => { active = false; };
  }, [project, resumeVersion]);
  useEffect(() => {
    if (initialMap) {
      setMapId(initialMap.id);
      maps.reload();
    }
  }, [initialMap?.id, initialMap?.revision]);
  useEffect(() => {
    if (!mapId && maps.data?.items[0]) setMapId(maps.data.items[0].id);
  }, [maps.data, mapId]);
  useEffect(() => {
    if (signalId && !map?.signals?.some((signal) => signal.id === signalId))
      setSignalId("");
  }, [map, signalId]);
  useEffect(() => {
    if (!assetId && assets.data?.items[0])
      setAssetId(
        assets.data.items.find(
          (item) => item.definition.blueprint_id === "vehicle.toyota.prius",
        )?.id ?? assets.data.items[0].id,
      );
  }, [assets.data, assetId]);
  function idempotency(body: unknown) {
    const text = JSON.stringify(body);
    if (pending.current?.body !== text)
      pending.current = { body: text, key: crypto.randomUUID() };
    return pending.current!.key;
  }
  async function perform(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }
  function edit(fn: (scenario: CityScenario) => void) {
    if (!writable || busy) return;
    originalYaml.current = null;
    setDraft((current) => {
      if (!current) return current;
      const next = structuredClone(current);
      fn(next);
      return next;
    });
    setValidation(null);
    setPreview(null);
    setYamlChanged(false);
  }
  function editActor(fn: (item: CityActor) => void) {
    edit((scenario) => {
      const item = scenario.actors.find((a) => a.id === selected);
      if (item) fn(item);
    });
  }
  function reset(value: CityRecord) {
    originalYaml.current = null;
    setSaved(value);
    setDraft(structuredClone(value.scenario));
    setMapId(value.scenario.map.id);
    setSelected(value.scenario.actors[0].id);
    setVia([]);
    setPlacement(null);
    setPreview(null);
    setValidation(null);
    setYamlChanged(false);
    setYaml("");
    setMode("visual");
  }
  async function validate(): Promise<CityValidation> {
    const text =
      mode === "yaml" && yamlChanged
        ? yaml
        : draft
          ? JSON.stringify(draft)
          : yaml;
    if (!text.trim()) throw new Error("시나리오를 만들거나 YAML을 입력하세요.");
    if (mode === "yaml" && yamlChanged) originalYaml.current = text;
    const value = await api<CityValidation>("/scenario-v2/yaml/validate", {
      method: "POST",
      body: JSON.stringify({ project_id: project, yaml: text }),
    });
    if (
      !["scenario-v2/2", "scenario-v2/3"].includes(
        value.scenario.schema_version,
      )
    )
      throw new Error(
        "이 편집기는 scenario-v2/2 및 /3 도시 계약을 사용합니다. /1은 기존 esmini 편집기에서 여세요.",
      );
    setDraft(value.scenario);
    setMapId(value.scenario.map.id);
    setSelected((id) =>
      value.scenario.actors.some((a) => a.id === id)
        ? id
        : value.scenario.actors[0].id,
    );
    setValidation(value);
    setYaml(value.canonical_yaml);
    setYamlChanged(false);
    setPreview(null);
    return value;
  }
  async function save() {
    await perform(async () => {
      const value = await validate();
      const sourceYaml = originalYaml.current ?? value.canonical_yaml;
      const body = saved
        ? {
            expected_revision: saved.revision,
            scenario: value.scenario,
            source_yaml: sourceYaml,
          }
        : { project_id: project, yaml: sourceYaml };
      const record = await api<CityRecord>(
        saved
          ? `/scenario-v2/${saved.id}/revisions`
          : "/scenario-v2/yaml/import",
        {
          method: "POST",
          body: JSON.stringify(body),
          headers: { "Idempotency-Key": idempotency(body) },
        },
      );
      reset(record);
      history.replaceState(null, "", `${location.pathname}${location.search}${savedCityScenarioRoute(record)}`);
      setValidation(value);
      setYaml(value.canonical_yaml);
      pending.current = null;
      records.reload();
      setNotice(
        `r${record.revision} 저장됨 · 원본 YAML과 정규화된 실행 설정이 함께 보존됩니다.`,
      );
    });
  }
  function spec() {
    if (!saved || !validation || dirty || !native)
      throw new Error("현재 초안을 검증·저장한 다음 실행을 준비하세요.");
    return {
      project_id: project,
      name: saved.scenario.name,
      kind: "object_sim",
      adapter_id: native.id,
      adapter_version: native.version,
      seed: saved.scenario.seed,
      input: {
        scenario_id: saved.id,
        scenario_revision: saved.revision,
        configuration_hash: validation.configuration_hash,
      },
    };
  }
  async function submit() {
    await perform(async () => {
      if (!preview) throw new Error("실행 준비 결과가 필요합니다.");
      const body = spec(),
        experiment = await api<{ id: string; preview_hash: string }>(
          "/experiments",
          {
            method: "POST",
            body: JSON.stringify(body),
            headers: { "Idempotency-Key": idempotency(body) },
          },
        );
      if (experiment.preview_hash !== preview.preview_hash) {
        setPreview(null);
        throw new Error(
          "실행 설정이 변경되었습니다. 실행 준비를 다시 확인하세요.",
        );
      }
      const execution = await api<{ id: string }>(
        `/experiments/${experiment.id}/executions`,
        {
          method: "POST",
          body: JSON.stringify({ preview_hash: preview.preview_hash }),
          headers: { "Idempotency-Key": `city-${experiment.id}` },
        },
      );
      pending.current = null;
      setNotice(`시험이 큐에 제출되었습니다: ${execution.id}`);
      notify?.("CARLA 도시 시험을 제출했습니다");
      onExecution?.(execution.id, project);
    });
  }
  async function pick(point: Pick<CityPose, "x" | "y">) {
    if (!map || !actor || !placement || !draft || !mapResource.data) return;
    const pose =
      actor.kind === "vehicle"
        ? closestLanePoint(map, point, 5)
        : { ...actor.pose, ...point };
    if (!pose) {
      setError(
        "선택 위치 5m 이내에 등록 차로가 없습니다. 확대 후 차로를 선택하세요.",
      );
      return;
    }
    if (placement === "behavior") {
      const node = map.nodes
        ?.filter((n) => Math.hypot(n.x - point.x, n.y - point.y) < 5)
        .sort(
          (a, b) =>
            Math.hypot(a.x - point.x, a.y - point.y) -
            Math.hypot(b.x - point.x, b.y - point.y),
        )[0];
      if (!node) {
        setError("선택 위치 5m 이내에 목표 차로 노드가 없습니다.");
        return;
      }
      editActor((a) => {
        const action = a.behavior?.actions.find((v) => v.id === targetAction);
        if (action) action.target_lane_node_id = node.id;
      });
      setPlacement(null);
      setNotice(
        `행동 ${targetAction} 목표 차로 ${node.id} 선택됨. 실행 가능 여부는 준비 단계에서 확인합니다.`,
      );
      return;
    }
    if (placement === "destination") {
      editActor((a) => {
        a.destination = pose;
      });
      setPlacement(null);
      setNotice(
        "독립 목적지를 고정했습니다. 기존 안내 경로는 유지하며 반응형 planner가 이 목적지를 사용합니다.",
      );
      return;
    }
    if (placement === "start") {
      editActor((item) => {
        item.pose = pose;
        item.route = [];
      });
      setVia([]);
      setPlacement(null);
      return;
    }
    if (placement === "via") {
      setVia((items) => [...items, pose]);
      setPlacement(null);
      setNotice(
        "경유점을 추가했습니다. 도착점을 선택하면 서버가 연결 차로를 찾습니다.",
      );
      return;
    }
    if (actor.kind !== "vehicle") {
      const route = [actor.pose, ...via, pose].map((point, index, points) => ({
        ...point,
        heading_rad:
          index < points.length - 1
            ? Math.atan2(
                points[index + 1].y - point.y,
                points[index + 1].x - point.x,
              )
            : points[Math.max(0, index - 1)].heading_rad,
      }));
      editActor((item) => {
        item.route = route;
        item.pose = { ...route[0] };
      });
      setPlacement(null);
      setNotice(
        "보행자 world 경로를 작성했습니다. 차량 차로 경로로 해석하지 않습니다.",
      );
      return;
    }
    await perform(async () => {
      const result = await api<{ route: CityPose[]; distance_m: number }>(
        `/maps/${encodeURIComponent(draft.map.id)}/route`,
        {
          method: "POST",
          body: JSON.stringify({
            project_id: project,
            revision: draft.map.revision,
            sha256: draft.map.sha256,
            start: actor.pose,
            goal: pose,
            via,
            max_snap_distance_m: 5,
          }),
        },
      );
      originalYaml.current = null;
      setDraft((current) =>
        current
          ? {
              ...current,
              actors: current.actors.map((item) =>
                item.id === actor.id ? { ...item, route: result.route } : item,
              ),
            }
          : current,
      );
      setValidation(null);
      setPreview(null);
      setYamlChanged(false);
      setPlacement(null);
      setNotice(
        `등록 차로 경로 ${result.distance_m.toFixed(1)}m · ${result.route.length}점. 실제 추종 결과는 실행 후 확인합니다.`,
      );
    });
  }
  async function exportYaml() {
    await perform(async () => {
      if (!saved) return;
      const response = await fetch(
        `/api/v1/scenario-v2/${saved.id}/export?format=yaml&revision=${saved.revision}`,
        { headers: authHeaders() },
      );
      if (!response.ok)
        throw new Error(`YAML export failed (${response.status})`);
      const url = URL.createObjectURL(await response.blob()),
        anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${saved.id}-r${saved.revision}.yaml`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  return (
    <div className="city-workspace" aria-label="CARLA city scenario authoring">
      <div className="city-intro">
        <div>
          <span className="city-eyebrow">
            CITY WORKSPACE · SCENARIO{" "}
            {draft?.schema_version === "scenario-v2/3"
              ? "V2/3 · REACTIVE"
              : "V2/2"}
          </span>
          <h2>도시 위에 배치하고, 경로를 정의하고, 실제 결과를 확인하세요.</h2>
          <p>
            지도·asset·controller 개정을 고정한 YAML을 기존 실행 큐로 보냅니다.
            지도 도식과 CARLA 렌더 영상, 목표 경로와 실제 피드백 추종을
            구분합니다.
          </p>
        </div>
        <span
          className={`city-engine-status ${native?.status === "available" ? "available" : ""}`}
        >
          {native?.status === "available"
            ? "CARLA 실행 프로필 설정됨"
            : "CARLA 실행 환경 미설정"}
        </span>
      </div>
      <div className="city-toolbar">
        {resumeLoading && <p role="status">과제에 연결된 저장 개정을 불러오고 있습니다…</p>}
        {!resumeLoading && initialResume.current.get("scenario") && !saved && <button className="button secondary" onClick={() => setResumeVersion(value => value + 1)}>저장 개정 다시 불러오기</button>}
        <label>
          프로젝트
          <select
            aria-label="City scenario project"
            disabled={busy}
            value={project}
            onChange={(event) => {
              originalYaml.current = null;
              setProject(event.target.value);
              setMapId("");
              setAssetId("");
              setDraft(null);
              setSaved(null);
              setValidation(null);
              setPreview(null);
              setYaml("");
              setYamlChanged(false);
              onProject?.(event.target.value);
            }}
          >
            {projects.data?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          저장된 도시 시나리오
          <select
            aria-label="Saved city scenario"
            disabled={busy}
            value={saved?.id ?? ""}
            onChange={(event) => {
              const item = cityRecords.find((r) => r.id === event.target.value);
              if (item) reset(item);
            }}
          >
            <option value="">새 초안</option>
            {cityRecords.map((item) => (
              <option key={item.id} value={item.id}>
                {item.scenario.name} · r{item.revision}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => {
            maps.reload();
            assets.reload();
            controllers.reload();
            records.reload();
            capabilities.reload();
          }}
        >
          카탈로그 새로고침
        </button>
      </div>
      {(error ||
        maps.error ||
        assets.error ||
        controllers.error ||
        mapResource.error) && (
        <p role="alert" className="expansion-error">
          {error ||
            maps.error ||
            assets.error ||
            controllers.error ||
            mapResource.error}
        </p>
      )}
      {notice && (
        <p role="status" className="expansion-success">
          {notice}
        </p>
      )}
      <div className="city-toolbar">
        <label>
          등록 지도
          <select
            aria-label="City map catalog"
            value={mapId}
            disabled={busy || !!draft}
            onChange={(event) => setMapId(event.target.value)}
          >
            <option value="">지도 선택</option>
            {maps.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.definition.name ?? item.id} · r{item.revision}
              </option>
            ))}
          </select>
        </label>
        <label>
          Asset 검색
          <input
            aria-label="Search city assets"
            placeholder="이름 · blueprint · 차량/보행자"
            value={assetSearch}
            onChange={(e) => setAssetSearch(e.target.value)}
          />
        </label>
        <label>
          Asset
          <select
            aria-label="City asset catalog"
            value={assetId}
            disabled={busy || !writable}
            onChange={(event) => setAssetId(event.target.value)}
          >
            {(["vehicle", "pedestrian", "static"] as const).map((kind) => (
              <optgroup
                key={kind}
                label={
                  {
                    vehicle: "차량",
                    pedestrian: "보행자",
                    static: "고정 asset",
                  }[kind]
                }
              >
                {assets.data?.items
                  .filter(
                    (item) =>
                      item.definition.actor_kind === kind &&
                      (item.id === assetId ||
                        !assetSearch ||
                        `${item.definition.name ?? ""} ${item.definition.blueprint_id ?? ""} ${item.definition.actor_kind}`
                          .toLowerCase()
                          .includes(assetSearch.toLowerCase())),
                  )
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.definition.name ??
                        item.definition.blueprint_id ??
                        item.id}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
        </label>
        <button
          className="button"
          disabled={
            busy ||
            !writable ||
            !map ||
            !mapResource.data ||
            !assets.data?.items.some(
              (a) => a.id === assetId && a.definition.actor_kind === "vehicle",
            )
          }
          onClick={() => {
            const asset = assets.data!.items.find((a) => a.id === assetId)!;
            originalYaml.current = null;
            setDraft(
              newCityScenario(
                { ...mapResource.data!, definition: map! },
                asset,
              ),
            );
            setSaved(null);
            setSelected("ego");
            setVia([]);
            setValidation(null);
            setPreview(null);
            setYamlChanged(false);
            setMode("visual");
          }}
        >
          새 도시 시나리오
        </button>
        {draft && (
          <button
            className="button secondary"
            disabled={busy || !writable}
            onClick={() => {
              originalYaml.current = null;
              setDraft(null);
              setSaved(null);
              setPreview(null);
              setValidation(null);
              setYaml("");
              setYamlChanged(false);
            }}
          >
            다른 지도 선택
          </button>
        )}
      </div>
      {chosenAsset && (
        <details className="city-panel">
          <summary>
            선택 asset · {chosenAsset.definition.blueprint_id} · r
            {chosenAsset.revision}
          </summary>
          <p>
            분류 {chosenAsset.definition.actor_kind} ·{" "}
            {chosenAsset.definition.dimensions
              ? `등록 크기 ${JSON.stringify(chosenAsset.definition.dimensions)}`
              : "등록된 실측 크기 없음. 편집 도식은 기본 크기를 쓰며 실행 Viewer는 네이티브 bounding box를 사용합니다."}
          </p>
          <p>
            허용 속성{" "}
            <code>
              {JSON.stringify(chosenAsset.definition.attributes ?? {})}
            </code>
          </p>
          <p>
            권리 / 출처{" "}
            <code>{JSON.stringify(chosenAsset.definition.rights ?? {})}</code>
          </p>
          <code>{chosenAsset.sha256}</code>
        </details>
      )}
      {!maps.loading && !maps.data?.items.length && (
        <div className="city-empty">
          <h3>등록된 네이티브 지도가 없습니다</h3>
          <p>
            CARLA에서 추출한 OpenDRIVE·차로 그래프·spawn point를 지도 카탈로그에
            등록하면 이 화면에서 경로를 작성할 수 있습니다. 예제 도로를 실제
            도시 지도처럼 표시하지 않습니다.
          </p>
        </div>
      )}
      <div
        className="city-mode-switch"
        role="group"
        aria-label="City scenario edit mode"
      >
        <button
          className={`button ${mode === "visual" ? "" : "secondary"}`}
          disabled={busy || yamlChanged}
          aria-pressed={mode === "visual"}
          onClick={() => setMode("visual")}
        >
          지도 · 객체 편집
        </button>
        <button
          className={`button ${mode === "yaml" ? "" : "secondary"}`}
          disabled={busy}
          aria-pressed={mode === "yaml"}
          onClick={() => {
            setMode("yaml");
            if (draft && !yamlChanged) {
              setYaml(JSON.stringify(draft, null, 2));
              if (writable)
                void perform(async () => {
                  await validate();
                });
            }
          }}
        >
          YAML 편집 · 가져오기
        </button>
      </div>
      {mode === "yaml" ? (
        <section className="city-panel">
          <h3>하나의 시나리오 · YAML과 지도 편집</h3>
          <p>
            YAML을 수정하면 먼저 검증·적용하세요. 참조 개정과 hash를 포함하며,
            중복 키·미지원 필드·순환 참조는 서버에서 거절합니다.
          </p>
          <textarea
            className="city-yaml"
            aria-label="City scenario YAML"
            rows={22}
            spellCheck={false}
            value={yaml}
            disabled={busy || !writable}
            onChange={(event) => {
              setYaml(event.target.value);
              setYamlChanged(true);
              setValidation(null);
              setPreview(null);
            }}
          />
          <button
            className="button secondary"
            disabled={busy || !writable || !yaml.trim()}
            onClick={() =>
              void perform(async () => {
                await validate();
                setNotice(
                  "YAML 검증 완료. 정규화된 시나리오를 지도에서 열 수 있습니다.",
                );
              })
            }
          >
            YAML 검증 · 적용
          </button>
        </section>
      ) : (
        draft && (
          <div className="city-editor-layout">
            <section className="city-panel city-actor-editor">
              <div className="city-field-grid">
                <label>
                  시나리오 이름
                  <input
                    aria-label="City scenario name"
                    value={draft.name}
                    disabled={busy || !writable}
                    onChange={(event) =>
                      edit((s) => {
                        s.name = event.target.value;
                      })
                    }
                  />
                </label>
                <label>
                  실행 시간 · s
                  <input
                    type="number"
                    min={1}
                    max={120}
                    value={draft.duration_s}
                    disabled={busy || !writable}
                    onChange={(event) =>
                      edit((s) => {
                        s.duration_s = Number(event.target.value);
                      })
                    }
                  />
                </label>
                <label>
                  Seed
                  <input
                    type="number"
                    value={draft.seed}
                    disabled={busy || !writable}
                    onChange={(event) =>
                      edit((s) => {
                        s.seed = Number(event.target.value);
                      })
                    }
                  />
                </label>
              </div>
              <div className="city-actor-list">
                {draft.actors.map((item) => (
                  <button
                    key={item.id}
                    className={selected === item.id ? "selected" : ""}
                    onClick={() => {
                      setSelected(item.id);
                      setVia([]);
                      setPlacement(null);
                    }}
                  >
                    <strong>{item.id}</strong>
                    <span>
                      {item.kind} · {item.role}
                    </span>
                  </button>
                ))}
              </div>
              <button
                className="button secondary"
                disabled={
                  busy || !writable || !assetId || draft.actors.length >= 32
                }
                onClick={() => {
                  const asset = assets.data?.items.find(
                    (item) => item.id === assetId,
                  );
                  if (!asset || !map) return;
                  const position =
                    map.spawn_points?.[draft.actors.length] ??
                    map.lanes[0]?.points[0];
                  if (!position) return;
                  const item = newCityActor(asset, position, draft.actors);
                  edit((s) => {
                    s.actors.push(item);
                  });
                  setSelected(item.id);
                  setVia([]);
                }}
              >
                선택 asset 추가
              </button>
              {actor && (
                <>
                  <div className="city-field-grid">
                    <label>
                      제어 방식
                      <select
                        aria-label="City actor controller"
                        value={actor.controller.kind}
                        disabled={busy || !writable}
                        onChange={(event) => {
                          if (event.target.value === "reactive") {
                            edit((s) =>
                              Object.assign(
                                s,
                                enableReactiveActor(s, selected),
                              ),
                            );
                            setNotice(
                              "반응형 /3 초안으로 전환했습니다. 간격·신호는 planner로, 대기·정지는 행동으로 이전하며 경로 끝은 정지합니다. 기존 저장 개정은 보존됩니다.",
                            );
                            return;
                          }
                          editActor((item) => {
                            const kind = event.target
                                .value as CityControllerKind,
                              reference = controllers.data?.items.find(
                                (c) => c.definition.mode === kind,
                              );
                            item.controller.kind = kind;
                            if (kind !== "reactive") {
                              delete item.behavior;
                              delete item.planner;
                              delete item.destination;
                            }
                            item.controller.ref = reference
                              ? catalogRef(reference)
                              : null;
                            if (kind === "static") item.speed_mps = 0;
                          });
                        }}
                      >
                        {(
                          Object.entries(controllerLabels) as [
                            CityControllerKind,
                            string,
                          ][]
                        )
                          .filter(([kind]) =>
                            actor.kind === "static"
                              ? kind === "static"
                              : kind !== "static",
                          )
                          .map(([kind, label]) => (
                            <option key={kind} value={kind}>
                              {label}
                              {kind === "autoware" &&
                              !controllers.data?.items.some(
                                (item) =>
                                  item.definition.mode === kind &&
                                  item.definition.status === "available",
                              )
                                ? " · 미설정"
                                : ""}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label>
                      목표 속도 · m/s
                      <input
                        aria-label="City actor speed"
                        type="number"
                        min={0}
                        max={
                          actor.controller.kind === "autoware"
                            ? 8
                            : actor.controller.kind === "timed_replay"
                              ? 30
                              : 12
                        }
                        step={0.1}
                        value={actor.speed_mps}
                        disabled={busy || !writable || actor.kind === "static"}
                        onChange={(event) =>
                          editActor((item) => {
                            item.speed_mps = Number(event.target.value);
                          })
                        }
                      />
                    </label>
                    {actor.controller.kind !== "reactive" && (
                      <label>
                        경로 끝 동작
                        <select
                          aria-label="City route end behavior"
                          disabled={busy || !writable}
                          value={actor.controller.end_behavior}
                          onChange={(event) =>
                            editActor((item) => {
                              item.controller.end_behavior = event.target
                                .value as "stop" | "repeat";
                            })
                          }
                        >
                          <option value="stop">감속 · 정지</option>
                          <option value="repeat">반복 · 명시적 설정</option>
                        </select>
                      </label>
                    )}
                  </div>
                  <p className="city-note">
                    {actor.controller.kind === "autoware"
                      ? "시뮬레이터 GT 입력의 planning/control입니다. 센서 인지·localization 검증이 아닙니다. 준비 상태와 실제 움직임을 별도로 확인합니다."
                      : actor.controller.kind === "timed_replay"
                        ? "시간별 좌표 재생입니다. 차량 동역학·피드백 추종 성능으로 평가하지 않습니다. trajectory는 YAML에서 편집하세요."
                        : "목표 경로와 반환된 실제 궤적을 따로 보존합니다. 미지원 제어 설정은 실행 준비 단계에서 거절합니다."}
                  </p>
                  {actor.controller.kind === "reactive" && (
                    <CityBehaviorEditor
                      actor={actor}
                      actors={draft.actors}
                      map={map}
                      disabled={busy || !writable}
                      onChange={editActor}
                      onPickLane={(actionId) => {
                        setTargetAction(actionId);
                        setPlacement("behavior");
                      }}
                    />
                  )}
                  {actor.controller.kind === "traffic" && (
                    <label>
                      선행 시간 간격 · s
                      <input
                        aria-label="Traffic headway seconds"
                        type="number"
                        min={0.5}
                        max={10}
                        step={0.1}
                        disabled={busy || !writable}
                        value={actor.controller.headway_s}
                        onChange={(event) =>
                          editActor((item) => {
                            item.controller.headway_s = Number(
                              event.target.value,
                            );
                          })
                        }
                      />
                    </label>
                  )}
                  {actor.controller.kind !== "reactive" && (
                    <div className="city-field-grid">
                      <label>
                        출발 대기 · s
                        <input
                          aria-label="City actor start delay"
                          type="number"
                          min={0}
                          max={draft.duration_s}
                          step={0.1}
                          disabled={busy || !writable}
                          value={actor.controller.wait_s ?? 0}
                          onChange={(event) =>
                            editActor((item) => {
                              item.controller.wait_s = Number(
                                event.target.value,
                              );
                            })
                          }
                        />
                      </label>
                      <label>
                        정지 시작 · s (선택)
                        <input
                          aria-label="City actor stop time"
                          type="number"
                          min={0}
                          max={draft.duration_s}
                          step={0.1}
                          disabled={busy || !writable}
                          value={actor.controller.stop_time_s ?? ""}
                          onChange={(event) =>
                            editActor((item) => {
                              item.controller.stop_time_s =
                                event.target.value === ""
                                  ? null
                                  : Number(event.target.value);
                            })
                          }
                        />
                      </label>
                    </div>
                  )}
                  <h4>출발 · 경유 · 도착</h4>
                  {actor.controller.kind === "reactive" && (
                    <div className="city-frozen-destination">
                      <p className="city-note">
                        안내 경로와 별도로 목적지를 고정할 수 있습니다. 차선
                        변경 후 다른 차로에서 도착하려면 선택하세요.
                      </p>
                      <button
                        className={`button ${placement === "destination" ? "" : "secondary"}`}
                        disabled={busy || !writable || !map}
                        aria-pressed={placement === "destination"}
                        onClick={() =>
                          setPlacement(
                            placement === "destination" ? null : "destination",
                          )
                        }
                      >
                        반응형 목적지 선택
                      </button>
                      <p className="city-note">
                        {actor.destination
                          ? `고정 목적지 (${actor.destination.x.toFixed(1)}, ${actor.destination.y.toFixed(1)})`
                          : "목적지: 안내 경로의 마지막 지점"}
                      </p>
                      {actor.destination && (
                        <button
                          className="button secondary"
                          disabled={busy || !writable}
                          onClick={() =>
                            editActor((a) => {
                              a.destination = null;
                            })
                          }
                        >
                          안내 경로 끝을 목적지로 사용
                        </button>
                      )}
                    </div>
                  )}
                  {actor.kind === "vehicle" &&
                    ["route_follow", "traffic"].includes(
                      actor.controller.kind,
                    ) && (
                      <label>
                        경로 계약
                        <select
                          aria-label="City vehicle route mode"
                          disabled={busy || !writable}
                          value={actor.route_mode ?? "native_lane_graph"}
                          onChange={(event) =>
                            editActor((item) => {
                              item.route_mode = event.target.value as
                                "native_lane_graph" | "authored_lane_change";
                              if (item.route_mode === "native_lane_graph")
                                delete item.controller.required_gap_m;
                            })
                          }
                        >
                          <option value="native_lane_graph">
                            네이티브 차로 그래프
                          </option>
                          <option value="authored_lane_change">
                            명시적 차로 변경 · YAML world route
                          </option>
                        </select>
                      </label>
                    )}
                  {actor.route_mode === "authored_lane_change" && (
                    <>
                      <p className="city-note">
                        YAML의 route에 부드러운 world 경로를 작성하세요. 이
                        모드는 실제 피드백 추종을 요청하며 시간 지정 좌표 재생과
                        구분됩니다. 실행 준비에서 차로·곡률·지원 범위를
                        검사합니다. 지도 경로 계산은 이 계약을 대체하지
                        않습니다.
                      </p>
                      <label>
                        차로 변경 시작 여유 거리 · m
                        <input
                          type="number"
                          aria-label="Lane change required gap"
                          min={0}
                          max={100}
                          step={0.5}
                          disabled={busy || !writable}
                          value={actor.controller.required_gap_m ?? 0}
                          onChange={(event) =>
                            editActor((item) => {
                              item.controller.required_gap_m = Number(
                                event.target.value,
                              );
                            })
                          }
                        />
                      </label>
                    </>
                  )}
                  <div className="city-button-row">
                    {(["start", "via", "goal"] as const).map((kind) => (
                      <button
                        key={kind}
                        className={`button ${placement === kind ? "" : "secondary"}`}
                        disabled={
                          busy ||
                          !writable ||
                          !map ||
                          actor.route_mode === "authored_lane_change" ||
                          (actor.kind === "static" && kind !== "start")
                        }
                        aria-pressed={placement === kind}
                        onClick={() =>
                          setPlacement(placement === kind ? null : kind)
                        }
                      >
                        {
                          {
                            start: "출발점 배치",
                            via: "경유점 추가",
                            goal: "도착점 · 경로 계산",
                          }[kind]
                        }
                      </button>
                    ))}
                  </div>
                  <p className="city-note">
                    경로 {actor.route.length}점 · 대기 경유점 {via.length}개 ·
                    출발 ({actor.pose.x.toFixed(1)}, {actor.pose.y.toFixed(1)})
                  </p>
                  <div className="city-button-row">
                    <button
                      className="button secondary"
                      disabled={busy || !writable || !actor.route.length}
                      onClick={() => {
                        editActor((item) => {
                          item.route = [];
                        });
                        setVia([]);
                      }}
                    >
                      경로 지우기
                    </button>
                    <button
                      className="button secondary"
                      disabled={busy || !writable || actor.role === "ego"}
                      onClick={() => {
                        edit((s) => {
                          s.actors = s.actors.filter(
                            (item) => item.id !== actor.id,
                          );
                        });
                        setSelected(
                          draft.actors.find((item) => item.role === "ego")!.id,
                        );
                      }}
                    >
                      객체 삭제
                    </button>
                  </div>
                </>
              )}
              <h4>고정 통과 기준 · Ego</h4>
              <details className="city-signals-author">
                <summary>
                  네이티브 신호 시험 조건 · {draft.signals?.length ?? 0}개
                </summary>
                <p className="city-note">
                  저장 지도에 연결된 신호 ID만 사용합니다. 기본값은 엔진의 신호
                  상태 유지입니다. 추가한 시험 조건의 실제 관측 색상은 실행
                  결과에 따로 보존합니다.
                </p>
                <label>
                  교통 신호
                  <select
                    aria-label="City traffic signal"
                    disabled={
                      busy ||
                      !writable ||
                      !map?.signals?.some(
                        (item) => item.kind === "traffic_light",
                      )
                    }
                    value={
                      signalId ||
                      map?.signals?.find(
                        (item) => item.kind === "traffic_light",
                      )?.id ||
                      ""
                    }
                    onChange={(event) => setSignalId(event.target.value)}
                  >
                    {map?.signals
                      ?.filter((item) => item.kind === "traffic_light")
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.id} · ({item.pose.x.toFixed(1)},{" "}
                          {item.pose.y.toFixed(1)})
                        </option>
                      ))}
                  </select>
                </label>
                <div className="city-button-row">
                  <button
                    className="button secondary"
                    disabled={
                      busy ||
                      !writable ||
                      !map?.signals?.some(
                        (item) => item.kind === "traffic_light",
                      )
                    }
                    onClick={() =>
                      edit((s) => {
                        const id =
                          signalId ||
                          map?.signals?.find(
                            (item) => item.kind === "traffic_light",
                          )?.id;
                        if (!id) return;
                        s.signals = [
                          ...(s.signals ?? []).filter((item) => item.id !== id),
                          {
                            id,
                            initial_state: "red",
                            changes: [
                              {
                                time_s: Number((s.duration_s / 2).toFixed(3)),
                                state: "green",
                              },
                            ],
                          },
                        ];
                      })
                    }
                  >
                    빨강 → 중간 시점 초록 추가
                  </button>
                  <button
                    className="button secondary"
                    disabled={busy || !writable || !draft.signals?.length}
                    onClick={() =>
                      edit((s) => {
                        delete s.signals;
                      })
                    }
                  >
                    작성 신호 조건 지우기
                  </button>
                </div>
                <p className="city-note">
                  세부 시간·상태는 YAML의 signals에서 수정합니다. 등록
                  메타데이터만으로 실제 신호가 바뀌었다고 판정하지 않습니다.
                </p>
              </details>
              <div className="city-field-grid">
                {(
                  [
                    ["max_lateral_error_p95_m", "횡오차 P95 · m", 0.5],
                    ["max_goal_error_m", "목표 오차 · m", 2],
                    ["max_final_speed_mps", "종료 속도 · m/s", 0.2],
                  ] as const
                ).map(([key, label, fallback]) => (
                  <label key={key}>
                    {label}
                    <input
                      type="number"
                      min={0}
                      step={0.1}
                      aria-label={label}
                      disabled={busy || !writable}
                      value={draft.acceptance?.[key] ?? fallback}
                      onChange={(event) =>
                        edit((s) => {
                          s.acceptance = {
                            max_lateral_error_p95_m: 0.5,
                            max_goal_error_m: 2,
                            max_final_speed_mps: 0.2,
                            require_goal: true,
                            require_no_contacts: true,
                            ...s.acceptance,
                            [key]: Number(event.target.value),
                          };
                        })
                      }
                    />
                  </label>
                ))}
              </div>
              <p className="city-note">
                기본값은 목표 도달과 접촉 없음도 요구합니다. YAML로 기준을
                명시할 수 있으며 저장 개정에 고정됩니다.
              </p>
              <h4>구성된 센서 기록 · {draft.sensors.length}개</h4>
              <div
                className="city-sensor-list"
                aria-label="Configured city sensors"
              >
                {draft.sensors.map((sensor, index) => (
                  <article
                    key={String(sensor.id)}
                    aria-label={`City sensor ${String(sensor.id)}`}
                  >
                    <strong>
                      {String(sensor.id)} · {String(sensor.kind)}
                    </strong>
                    <p>연결 객체 {String(sensor.actor_id)}</p>
                    <p>
                      {String(sensor.width)} × {String(sensor.height)} px ·{" "}
                      {String(sensor.period_s)}s 주기
                      {typeof sensor.period_s === "number" &&
                      sensor.period_s > 0
                        ? ` · ${Number((1 / sensor.period_s).toFixed(3))}Hz`
                        : ""}
                      {typeof sensor.fov_deg === "number"
                        ? ` · FOV ${sensor.fov_deg}°`
                        : ""}
                    </p>
                    <details>
                      <summary>보존 장착 위치·전체 설정</summary>
                      <pre>{JSON.stringify(sensor, null, 2)}</pre>
                    </details>
                    <button
                      type="button"
                      className="button secondary"
                      aria-label={`Remove city sensor ${String(sensor.id)}`}
                      disabled={busy || !writable}
                      onClick={() =>
                        edit((s) => {
                          s.sensors.splice(index, 1);
                        })
                      }
                    >
                      이 센서 제거
                    </button>
                  </article>
                ))}
                {!draft.sensors.length && (
                  <p className="city-note">구성된 센서가 없습니다.</p>
                )}
              </div>
              <button
                type="button"
                className="button secondary"
                aria-label="Add city ego RGB camera"
                disabled={busy || !writable || draft.sensors.length >= 8}
                onClick={() =>
                  edit((s) => {
                    s.sensors.push(newCityRgbSensor(s));
                  })
                }
              >
                기본 Ego RGB 추가 · 640 × 360 · 10Hz
              </button>
              <p className="city-note">
                ID·해상도·주기·장착 위치는 YAML에서 편집합니다. 추가는 새 ID를
                사용하며 기존 센서를 바꾸지 않습니다. 실제 CARLA renderer
                출력입니다. 물리 센서·ISP 검증으로 집계하지 않습니다. 저장
                용량과 지원 범위는 실행 준비에서 확인합니다.
              </p>
            </section>
            <div>
              {map ? (
                <CityMapCanvas
                  map={map}
                  actors={draft.actors.map((item) => {
                    const dimensions = assets.data?.items.find(
                      (asset) =>
                        asset.id === item.asset.id &&
                        asset.revision === item.asset.revision,
                    )?.definition.dimensions;
                    return {
                      ...item,
                      dimensions:
                        dimensions && typeof dimensions === "object"
                          ? (dimensions as {
                              length_m: number;
                              width_m: number;
                            })
                          : undefined,
                    };
                  })}
                  selected={selected}
                  onSelect={(id) => {
                    setSelected(id);
                    setVia([]);
                    setPlacement(null);
                  }}
                  onPick={
                    placement && !busy ? (point) => void pick(point) : undefined
                  }
                  pickLabel={
                    placement
                      ? {
                          start: "출발점 배치",
                          via: "경유점 추가",
                          goal: "도착점 선택",
                          destination: "반응형 목적지 선택",
                          behavior: `행동 ${targetAction} 목표 차로 선택`,
                        }[placement]
                      : undefined
                  }
                />
              ) : (
                <div className="city-empty">
                  저장 개정의 지도 기하를 읽는 중…
                </div>
              )}
              <div className="city-map-provenance">
                지도 {draft.map.id} · r{draft.map.revision}
                <br />
                <code>{draft.map.sha256}</code>
              </div>
            </div>
          </div>
        )
      )}
      {draft && (
        <section className="city-panel">
          <div className="city-execution-grid">
            <div>
              <h3>검증 · 개정 저장 · 실행</h3>
              <p>
                {saved
                  ? `${saved.id} · r${saved.revision}${dirty ? " · 저장하지 않은 변경 있음" : " · 저장 입력과 일치"}`
                  : "새 시나리오를 저장하면 불변 개정이 만들어집니다."}
              </p>
              {changes.length > 0 && (
                <details>
                  <summary>저장 개정과 달라진 항목 {changes.length}개</summary>
                  <ul>
                    {changes.map((field) => (
                      <li key={field}>
                        <code>{field}</code>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <div className="city-button-row">
                <button
                  className="button secondary"
                  disabled={busy || !writable}
                  onClick={() =>
                    void perform(async () => {
                      await validate();
                      setNotice("서버 스키마·카탈로그 검증 완료.");
                    })
                  }
                >
                  설정 검증
                </button>
                <button
                  className="button"
                  disabled={busy || !writable}
                  onClick={() => void save()}
                >
                  개정 저장
                </button>
                <button
                  className="button secondary"
                  disabled={busy || !saved}
                  onClick={() => void exportYaml()}
                >
                  저장 YAML 내보내기
                </button>
              </div>
              {validation && (
                <p className="city-note">
                  Configuration SHA-256{" "}
                  <code>{validation.configuration_hash}</code>
                </p>
              )}
            </div>
            <div>
              <p className="city-note">
                {native?.description ??
                  "실행 adapter가 등록되면 큐 제출이 활성화됩니다. 미연결 controller는 성공으로 처리하지 않습니다."}
              </p>
              <div className="city-button-row">
                <button
                  className="button secondary"
                  disabled={
                    busy ||
                    !writable ||
                    !saved ||
                    dirty ||
                    !validation ||
                    native?.status !== "available"
                  }
                  onClick={() =>
                    void perform(async () => {
                      setPreview(
                        await api<Preview>("/experiments/preview", {
                          method: "POST",
                          body: JSON.stringify(spec()),
                        }),
                      );
                    })
                  }
                >
                  1. 실행 준비 확인
                </button>
                <button
                  className="button"
                  disabled={busy || !writable || !preview || dirty}
                  onClick={() => void submit()}
                >
                  2. 시험 큐에 제출
                </button>
              </div>
              {preview && (
                <p role="status">
                  {preview.job_count}건 · {preview.execution_fidelity} ·{" "}
                  {preview.preview_hash.slice(0, 16)}
                </p>
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
