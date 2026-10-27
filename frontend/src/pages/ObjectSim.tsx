import { useEffect, useState } from "react";
import { ErrorState, Loading, PageHeading } from "../components/ui";
import Experiments from "./Experiments";
import ScenarioV2Workspace from "./ScenarioV2";
import CityScenarioEditor from "../components/city-sim/CityScenarioEditor";
import ObjectMapEditor from "../components/city-sim/ObjectMapEditor";
import ExternalMapLibrary from "../components/external-maps/ExternalMapLibrary";
import { publishedMapRoute } from "../components/external-maps/map-source-contract";
import ObjectDemoWorkspace from "../components/object-demo/ObjectDemoWorkspace";
import ObjectBetaWorkspace from "../components/object-demo/ObjectBetaWorkspace";
import ObjectComparison from "../components/object-demo/ObjectComparison";
import StuntWorkspace from "../components/stunt-lab/StuntWorkspace";
import type { CatalogRef } from "../components/city-sim/city-contract";
import { useResource } from "../api";
import "../object-sim.css";

export default function ObjectSim({
  notify,
}: {
  notify?: (message: string) => void;
}) {
  const initial = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const startsWithAuthor =
    initial.get("mode") === "author" || initial.get("tab") === "author";
  const [tab, setTab] = useState<
    | "beta"
    | "quick"
    | "compare"
    | "author"
    | "results"
    | "city"
    | "maps"
    | "map-sources"
    | "stunt"
  >(
    initial.get("mode") === "stunt"
      ? "stunt"
      : (initial.get("mode") === "story" && initial.get("left")) ||
          (initial.get("mode") === "compare" &&
            initial.get("left") &&
            initial.get("right"))
        ? "compare"
        : initial.get("mode") === "map-sources"
          ? "map-sources"
        : initial.get("mode") === "maps"
          ? "maps"
          : initial.get("mode") === "city"
            ? "city"
            : startsWithAuthor
              ? "author"
              : initial.get("execution")
                ? "results"
                : initial.get("mode") === "quick"
                  ? "quick"
                  : "beta",
  );
  const [comparison, setComparison] = useState<{
    left: string;
    right?: string;
    story?: boolean;
  } | null>(
    initial.get("left")
      ? {
          left: initial.get("left")!,
          right: initial.get("right") ?? undefined,
          story: initial.get("mode") === "story",
        }
      : null,
  );
  const [authorOpened, setAuthorOpened] = useState(startsWithAuthor);
  const [cityOpened, setCityOpened] = useState(initial.get("mode") === "city");
  const [mapOpened, setMapOpened] = useState(initial.get("mode") === "maps");
  const [selectedProject, setProject] = useState(initial.get("project") ?? "");
  const [comparisonOrigin, setComparisonOrigin] = useState<"quick" | "beta">(
    initial.get("origin") === "beta" ? "beta" : "quick",
  );
  const [initialMap, setInitialMap] = useState<CatalogRef | undefined>(() => {
    const id = initial.get("map"), revision = Number(initial.get("map_revision")), sha256 = initial.get("map_hash");
    return id && Number.isSafeInteger(revision) && revision > 0 && sha256 && /^[a-f0-9]{64}$/.test(sha256)
      ? { id, revision, sha256 } : undefined;
  });
  const projects = useResource<{ items: { id: string; name: string }[] }>(
    "/projects",
  );
  // Resolve a missing selection from accessible projects before mounting any
  // project-scoped workspace. A select's first visible option is not its state.
  const project =
    selectedProject ||
    projects.data?.items.find((item) => item.id === "default")?.id ||
    projects.data?.items[0]?.id ||
    "";
  const projectAvailable = projects.data?.items.some(
    (item) => item.id === project,
  );
  const selectedMapRecord = useResource<CatalogRef & { project_id: string }>(initialMap && project
    ? `/maps/${encodeURIComponent(initialMap.id)}?${new URLSearchParams({ project_id: project, revision: String(initialMap.revision) })}` : null);
  const mapResumeError = initial.get("mode") === "city" && initial.has("map") && !initialMap
    ? "지도 이어하기 주소의 개정·해시가 올바르지 않습니다. 외부 지도 라이브러리에서 게시된 지도를 다시 여세요."
    : initialMap && selectedMapRecord.data && (selectedMapRecord.data.id !== initialMap.id || selectedMapRecord.data.revision !== initialMap.revision || selectedMapRecord.data.sha256 !== initialMap.sha256 || selectedMapRecord.data.project_id !== project)
      ? "요청한 지도 개정·해시·프로젝트가 저장 기록과 다릅니다. 다른 지도로 대신 열지 않습니다." : "";
  useEffect(() => {
    if (!projectAvailable || !location.hash.startsWith("#object-sim")) return;
    const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
    if (query.has("project")) return;
    query.set("project", project);
    history.replaceState(
      null,
      "",
      `${location.pathname}${location.search}#object-sim?${query}`,
    );
  }, [project, projectAvailable]);

  function author() {
    setAuthorOpened(true);
    setTab("author");
  }
  function changedProject(projectId: string) {
    setProject(projectId);
    setInitialMap(undefined);
    const query = new URLSearchParams({
      project: projectId,
      mode: tab === "compare" ? "quick" : tab,
    });
    if (tab === "compare") {
      setTab("quick");
      setComparison(null);
    }
    history.replaceState(
      null,
      "",
      `${location.pathname}${location.search}#object-sim?${query}`,
    );
  }
  function openQuick() {
    setTab("quick");
    history.replaceState(
      null,
      "",
      `${location.pathname}${location.search}#object-sim?${new URLSearchParams({ project, mode: "quick" })}`,
    );
  }
  function openBeta() {
    setTab("beta");
    history.replaceState(
      null,
      "",
      `${location.pathname}${location.search}#object-sim?${new URLSearchParams({ project, mode: "beta" })}`,
    );
  }
  function compare(
    left: string,
    right: string,
    origin: "quick" | "beta" = "quick",
  ) {
    setComparisonOrigin(origin);
    setComparison({ left, right });
    setTab("compare");
    history.replaceState(
      null,
      "",
      `${location.pathname}${location.search}#object-sim?${new URLSearchParams({ project, mode: "compare", left, right, origin })}`,
    );
  }
  function story(left: string, right?: string) {
    setComparison({ left, right, story: true });
    setTab("compare");
    const query = new URLSearchParams({ project, mode: "story", left });
    if (right) query.set("right", right);
    history.replaceState(
      null,
      "",
      `${location.pathname}${location.search}#object-sim?${query}`,
    );
  }
  function submitted(id: string, projectId: string) {
    const query = new URLSearchParams({ project: projectId, execution: id });
    history.replaceState(
      null,
      "",
      `${location.pathname}${location.search}#object-sim?${query}`,
    );
    setTab("results");
  }

  if (projects.loading)
    return <Loading label="접근 가능한 프로젝트를 확인하고 있습니다…" />;
  if (projects.error)
    return <ErrorState message={projects.error} retry={projects.reload} />;
  if (!projectAvailable)
    return (
      <ErrorState
        message={
          project
            ? "요청한 프로젝트에 접근할 수 없습니다. 공유받은 프로젝트 주소로 접속해 주세요."
            : "접근 가능한 프로젝트가 없습니다."
        }
      />
    );

  return (
    <div className="object-sim-workspace">
      <PageHeading
        eyebrow="SIMULATION · OBJECT SIM"
        title="Object Sim"
        description="장면을 수정하고 실제 움직임을 다시 실행하세요. 원본 영상과 객체의 판단을 A/B로 비교합니다."
      />
      <nav className="object-sim-tabs" aria-label="Object Sim workspace">
        <button
          type="button"
          className={`button ${tab === "stunt" ? "" : "secondary"}`}
          aria-pressed={tab === "stunt"}
          onClick={() => {
            setTab("stunt");
            history.replaceState(
              null,
              "",
              `#object-sim?${new URLSearchParams({ project, mode: "stunt" })}`,
            );
          }}
        >
          추력 차량 시험장
        </button>
        <button
          type="button"
          className={`button ${tab === "beta" ? "" : "secondary"}`}
          aria-pressed={tab === "beta"}
          onClick={openBeta}
        >
          시나리오 만들기 · 베타
        </button>
        <button
          type="button"
          className={`button ${tab === "quick" || tab === "compare" ? "" : "secondary"}`}
          aria-pressed={tab === "quick" || tab === "compare"}
          onClick={openQuick}
        >
          빠른 시작 · 데모
        </button>
        <button
          type="button"
          className={`button ${tab === "map-sources" ? "" : "secondary"}`}
          aria-pressed={tab === "map-sources"}
          onClick={() => {
            setTab("map-sources");
            history.replaceState(null, "", `${location.pathname}${location.search}#object-sim?${new URLSearchParams({ project, mode: "map-sources" })}`);
          }}
        >
          한국·외부 지도
        </button>
        <button
          type="button"
          className={`button ${tab === "maps" ? "" : "secondary"}`}
          aria-pressed={tab === "maps"}
          onClick={() => {
            setMapOpened(true);
            setTab("maps");
          }}
        >
          ① 지도 편집 · 빌드
        </button>
        <button
          type="button"
          className={`button ${tab === "city" ? "" : "secondary"}`}
          aria-pressed={tab === "city"}
          onClick={() => {
            setCityOpened(true);
            setTab("city");
          }}
        >
          ② 도시 시나리오 · 행동
        </button>
        <button
          type="button"
          className={`button ${tab === "results" ? "" : "secondary"}`}
          aria-pressed={tab === "results"}
          onClick={() => setTab("results")}
        >
          ③ 실행 결과 · 행동 조사
        </button>
        <button
          type="button"
          className={`button ${tab === "author" ? "" : "secondary"}`}
          aria-pressed={tab === "author"}
          onClick={author}
        >
          esmini 시나리오 작성 · 실행
        </button>
      </nav>
      {(tab === "beta" ||
        tab === "quick" ||
        tab === "compare" ||
        tab === "stunt" ||
        tab === "map-sources") && (
        <label className="object-map-project">
          작업 프로젝트
          <select
            aria-label="Demo project"
            value={project}
            onChange={(event) => changedProject(event.target.value)}
          >
            {projects.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {tab === "stunt" && (
        <StuntWorkspace key={`stunt:${project}`} project={project} />
      )}
      {tab === "map-sources" && <ExternalMapLibrary key={`map-sources:${project}`} project={project} onUseMap={map => {
        setInitialMap(map);
        setCityOpened(true);
        setTab("city");
        history.replaceState(null, "", `${location.pathname}${location.search}${publishedMapRoute(project, map)}`);
      }}/>} 
      <div hidden={tab !== "beta"}>
        <ObjectBetaWorkspace
          key={`beta:${project}`}
          project={project}
          notify={notify}
          onAdvanced={() => {
            setCityOpened(true);
            setTab("city");
            history.replaceState(
              null,
              "",
              `${location.pathname}${location.search}#object-sim?${new URLSearchParams({ project, mode: "city" })}`,
            );
          }}
          onCompare={(left, right) => {
            compare(left, right, "beta");
          }}
        />
      </div>
      <div hidden={tab !== "quick"}>
        <ObjectDemoWorkspace
          key={project}
          project={project}
          notify={notify}
          onAdvanced={() => {
            setCityOpened(true);
            setTab("city");
            history.replaceState(
              null,
              "",
              `${location.pathname}${location.search}#object-sim?${new URLSearchParams({ project, mode: "city" })}`,
            );
          }}
          onCompare={(left, right) => {
            compare(left, right);
          }}
          onStory={(left, right) => {
            setComparisonOrigin("quick");
            story(left, right);
          }}
        />
      </div>
      {tab === "compare" && comparison && (
        <ObjectComparison
          project={project}
          leftExecutionId={comparison.left}
          rightExecutionId={comparison.right}
          autoStartStory={comparison.story}
          onBack={comparisonOrigin === "beta" ? openBeta : openQuick}
        />
      )}
      {mapOpened && (
        <div hidden={tab !== "maps"}>
          <label className="object-map-project">
            지도 프로젝트
            <select
              aria-label="Map editor project"
              value={project}
              onChange={(e) => changedProject(e.target.value)}
            >
              {projects.data?.items.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <ObjectMapEditor
            key={project}
            project={project}
            onBuilt={(ref) => {
              setInitialMap(ref);
              setCityOpened(true);
              setTab("city");
            }}
          />
        </div>
      )}
      {authorOpened && (
        <div hidden={tab !== "author"}>
          <ScenarioV2Workspace
            embedded
            notify={notify}
            onProject={changedProject}
            onExecution={submitted}
          />
        </div>
      )}
      {cityOpened && (
        <div hidden={tab !== "city"}>
          {mapResumeError ? <ErrorState message={mapResumeError}/> : selectedMapRecord.error ? <ErrorState message={selectedMapRecord.error} retry={selectedMapRecord.reload}/> : initialMap && !selectedMapRecord.data ? <Loading label="게시된 지도 개정·해시를 확인하고 있습니다…"/> : <CityScenarioEditor
            key={
              initialMap
                ? `${project}:${initialMap.id}:${initialMap.revision}`
                : `city:${project}`
            }
            initialMap={initialMap}
            notify={notify}
            onProject={changedProject}
            onExecution={submitted}
          />}
        </div>
      )}
      {tab === "results" && (
        <Experiments
          displayMode="results"
          kindFilter={["object_sim"]}
          title="Object Sim · 저장 결과 재생"
          description="동일 프로젝트의 Object Sim 실행 결과입니다. 실제 adapter·fidelity·입력 개정과 관찰 지표를 함께 확인하세요."
        />
      )}
    </div>
  );
}
