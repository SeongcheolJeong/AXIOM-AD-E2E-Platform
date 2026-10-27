import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Box,
  Check,
  ChevronDown,
  ChevronRight,
  Database,
  Film,
  Hexagon,
  LayoutDashboard,
  Layers3,
  ListOrdered,
  LoaderCircle,
  Play,
  Plug,
  Search,
  ScanEye,
  Settings2,
  ShieldCheck,
  Terminal,
  Workflow,
} from "lucide-react";
import { isExternalAccess, isExternalViewer, useResource } from "./api";
import type {
  Adapter,
  Overview as OverviewData,
  RunDetail,
  RunSummary,
  Scenario,
} from "./types";
import Overview from "./pages/Overview";
import Scenarios from "./pages/Scenarios";
import Runs from "./pages/Runs";
import Jobs from "./pages/Jobs";
import Suites from "./pages/Suites";
import type { JobDetail } from "./components/jobs/JobTypes";
import Catalog from "./pages/Catalog";
import Perception from "./pages/Perception";
import Datasets from "./pages/Datasets";
import Stacks from "./pages/Stacks";
import SensorConfigurations from "./pages/SensorConfigurations";
import type { InferenceSource } from "./components/perception/PerceptionTypes";
import Validation from "./pages/Validation";
import Integrations from "./pages/Integrations";
import Agents from "./pages/Agents";
import Cases from "./pages/Cases";
import Collections from "./pages/Collections";
import SceneDiscovery from "./pages/SceneDiscovery";
import ScenarioForm from "./components/ScenarioForm";
import Experiments from "./pages/Experiments";
import ReplaySpecs from "./pages/ReplaySpecs";
import ScenarioV2Workspace from "./pages/ScenarioV2";
import TeamWorkspace from "./pages/TeamWorkspace";
import Workspace from "./pages/Workspace";
import BetaModuleBar from "./components/beta-workspace/BetaModuleBar";
import { demoGalleryReturn } from "./demo-moment";
import ObjectSim from "./pages/ObjectSim";
import LogSim from "./pages/LogSim";
import SensorSim from "./pages/SensorSim";
import HilSim from "./pages/HilSim";
import NeuralSim from "./pages/NeuralSim";
import RunLauncher from "./components/RunLauncher";
import { runOptionLabel } from "./components/EngineProvenance";
import {
  ErrorState,
  Loading,
  Modal,
  SearchField,
  Toast,
} from "./components/ui";
const pages = [
  { id: "workspace", label: "시작 · 가능한 작업", icon: LayoutDashboard },
  { id: "object-sim", label: "Object Sim · 작성 / 재생", icon: Box },
  { id: "log-sim", label: "Log Sim · 조사 / 변형 / 비교", icon: Film },
  { id: "sensor-sim", label: "Sensor Sim · 영상 / RAW", icon: ScanEye },
  { id: "neural-sim", label: "Neural Sim · 재구성 / 새 시점", icon: Layers3 },
  { id: "hil-sim", label: "HIL · 입력 고장 / DUT 비교", icon: Plug },
  { id: "viewers", label: "결과 Viewer", icon: Play },
  { id: "replay-specs", label: "Log replay", icon: Play },
  { id: "validation", label: "Validation", icon: ShieldCheck },
  { id: "experiments", label: "Experiments · 상세 설정", icon: Workflow },
  { id: "scenario-v2", label: "Behavior scenarios", icon: Box },
  { id: "team", label: "Team workspace", icon: Settings2 },
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "scenarios", label: "Scenario library", icon: Box },
  { id: "suites", label: "Test suites", icon: Layers3 },
  { id: "jobs", label: "Execution queue", icon: ListOrdered },
  { id: "runs", label: "Run explorer", icon: Workflow },
  { id: "catalog", label: "Data catalog", icon: Database },
  { id: "cases", label: "Investigation cases", icon: BookOpen },
  { id: "collections", label: "Data collections", icon: Layers3 },
  { id: "scene-discovery", label: "Scene discovery", icon: Search },
  { id: "perception", label: "Perception experiments", icon: ScanEye },
  { id: "datasets", label: "Detection datasets", icon: Layers3 },
  { id: "stacks", label: "Component stacks", icon: Workflow },
  { id: "simulation-sensors", label: "Simulation sensors", icon: Settings2 },
  { id: "integrations", label: "Integrations", icon: Plug },
  { id: "agents", label: "Agent studio & API", icon: Terminal },
];
export default function App() {
  const [inferenceSource, setInferenceSource] =
    useState<InferenceSource | null>(null);
  const consumeInferenceSource = useCallback(
    () => setInferenceSource(null),
    [],
  );
  const [route, setRoute] = useState(location.hash);
  const [page, setPage] = useState(() => location.hash.slice(1).split("?")[0] || (isExternalAccess() ? "workspace" : "overview")),
    [form, setForm] = useState<Scenario | "new" | null>(null),
    [launch, setLaunch] = useState<string | null>(null),
    [selectedId, setSelectedId] = useState<string | null>(() => new URLSearchParams(location.hash.split("?")[1] ?? "").get("run")),
    [selectedJobId, setSelectedJobId] = useState<string | null>(() => new URLSearchParams(location.hash.split("?")[1] ?? "").get("job")),
    [toast, setToast] = useState(""),
    [searchOpen, setSearchOpen] = useState(false),
    [settings, setSettings] = useState(false),
    [connectionRevision, setConnectionRevision] = useState(0),
    [mobileOpen, setMobileOpen] = useState(false);
  const access = useResource<{mode:string;legacy_domains_available:boolean;role:string;user_label:string;project_id?:string|null}>("/team/whoami");
  const externalAccess = isExternalAccess();
  const legacyAccess = access.errorStatus === 404 || (access.data?.legacy_domains_available ?? false);
  const visiblePages = pages.filter(p=>legacyAccess||['workspace','object-sim','log-sim','sensor-sim','neural-sim','hil-sim','viewers','experiments','scenario-v2','replay-specs','team','validation','agents'].includes(p.id));
  const overview = useResource<OverviewData>(legacyAccess ? "/overview" : null),
    scenarios = useResource<Scenario[]>(legacyAccess ? "/scenarios" : null),
    runs = useResource<RunSummary[]>(legacyAccess ? "/runs" : null),
    adapters = useResource<Adapter[]>(legacyAccess ? "/adapters" : null),
    health = useResource<{ status: string; version: string; database: string }>(
      externalAccess ? null : "/health",
    ),
    detail = useResource<RunDetail>(legacyAccess && selectedId ? `/runs/${encodeURIComponent(selectedId)}` : null);
  useEffect(() => {
    if (!selectedId && overview.data?.recent_runs[0])
      setSelectedId(overview.data.recent_runs[0].id);
  }, [overview.data, selectedId]);
  const navigate = useCallback((next: string) => {
    setPage(next);
    location.hash = access.data?.mode === "project" && access.data.project_id
      ? `${next}?${new URLSearchParams({ project: access.data.project_id })}`
      : next;
    setMobileOpen(false);
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [access.data?.mode, access.data?.project_id]);
  useEffect(() => {
    if (access.data?.mode === 'project' && !visiblePages.some(p=>p.id===page)) navigate('workspace');
  }, [access.data, page, navigate]);
  useEffect(() => {
    const listener = () => {
      setRoute(location.hash);
      const next = location.hash.slice(1).split("?")[0];
      const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
      if (next === "runs" && params.has("run")) { setSelectedId(params.get("run")); runs.reload(); }
      if (next === "jobs" && params.has("job")) setSelectedJobId(params.get("job"));
      setPage(pages.some((p) => p.id === next) ? next : (isExternalAccess() ? "workspace" : "overview"));
      setMobileOpen(false);
      window.scrollTo({ top: 0, behavior: "instant" });
    };
    window.addEventListener("hashchange", listener);
    return () => window.removeEventListener("hashchange", listener);
  }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);
  const refresh = useCallback(() => {
    access.reload();
    overview.reload();
    scenarios.reload();
    runs.reload();
    adapters.reload();
    health.reload();
    detail.reload();
  }, [
    overview.reload,
    scenarios.reload,
    runs.reload,
    adapters.reload,
    health.reload,
    detail.reload,
  ]);
  function selectRun(id: string) {
    setSelectedId(id);
    navigate("runs");
  }
  function submittedJob(job: JobDetail) {
    setLaunch(null);
    setSelectedJobId(job.id);
    navigate("jobs");
    setToast(`Execution submitted · #${job.submission_seq}`);
  }
  const hideToast = useCallback(() => setToast(""), []),
    currentPage = pages.find((p) => p.id === page) ?? pages[0];
  const retry = () => {
    refresh();
  };
  let content;
  if (page === "workspace") content = <Workspace key={route} />;
  else if (page === "object-sim") content = <ObjectSim key={route} notify={setToast} />;
  else if (page === "log-sim") content = <LogSim key={route} />;
  else if (page === "sensor-sim") content = <SensorSim key={route} />;
  else if (page === "neural-sim") content = <NeuralSim key={route} />;
  else if (page === "hil-sim") content = <HilSim key={route} />;
  else if (page === "viewers") content = <Experiments key={route} displayMode="results" title="결과 Viewer" description="저장된 실행을 선택해 궤적·영상·좌표를 재생하고 원본 근거를 내려받습니다. 실행 완료와 평가 판정은 별도로 표시합니다." />;
  else if (page === "experiments") content = <Experiments key={route} />;
  else if (page === "scenario-v2") content = <ScenarioV2Workspace notify={setToast} />;
  else if (page === "replay-specs") content = <ReplaySpecs key={route} />;
  else if (page === "team") content = <TeamWorkspace />;
  else if (page === "agents") content = <Agents scenarios={scenarios.data ?? []} />;
  else if (access.error && access.errorStatus !== 404) content = <ErrorState message={access.error} retry={access.reload} />;
  else if (page === "jobs")
    content = (
      <Jobs
        selectedId={selectedJobId}
        onSelect={setSelectedJobId}
        onOpenRun={(id) => {
          refresh();
          selectRun(id);
        }}
        onLaunch={() => setLaunch("")}
        onSettled={refresh}
      />
    );
  else if (page === "suites")
    content = (
      <Suites
        scenarios={scenarios.data ?? []}
        adapters={adapters.data ?? []}
        onOpenJob={(id) => {
          setSelectedJobId(id);
          navigate("jobs");
        }}
        onOpenRun={(id) => {
          refresh();
          selectRun(id);
        }}
        onSettled={refresh}
        onNavigate={navigate}
        notify={setToast}
      />
    );
  else if (page === "perception")
    content = (
      <Perception
        source={inferenceSource}
        onSourceConsumed={consumeInferenceSource}
      />
    );
  else if (page === "datasets") content = <Datasets key={route} />;
  else if (page === "cases") content = <Cases key={route} />;
  else if (page === "collections") content = <Collections key={route} />;
  else if (page === "scene-discovery") content = <SceneDiscovery key={route} />;
  else if (page === "stacks") content = <Stacks key={route} />;
  else if (page === "simulation-sensors") content = <SensorConfigurations />;
  else if (overview.error)
    content = <ErrorState message={overview.error} retry={retry} />;
  else if (overview.loading && !overview.data)
    content = <Loading label="Connecting your development loop…" />;
  else if (page === "scenarios")
    content = scenarios.error ? (
      <ErrorState message={scenarios.error} retry={scenarios.reload} />
    ) : (
      <Scenarios
        scenarios={scenarios.data ?? []}
        onCreate={() => setForm("new")}
        onEdit={setForm}
        onRun={setLaunch}
        onRefresh={refresh}
        notify={setToast}
      />
    );
  else if (page === "runs")
    content = runs.error ? (
      <ErrorState message={runs.error} retry={runs.reload} />
    ) : (
      <>
        {detail.error && (
          <div className="inline-error" role="alert">
            Unable to load selected run: {detail.error}
          </div>
        )}
        <Runs
          runs={runs.data ?? []}
          run={detail.data}
          selectedId={selectedId}
          loading={detail.loading}
          loadError={detail.error}
          onSelect={setSelectedId}
          onRun={() => setLaunch("")}
          onOpenJob={(id) => {
            setSelectedJobId(id);
            navigate("jobs");
          }}
        />
      </>
    );
  else if (page === "catalog")
    content = (
      <Catalog
        key={route}
        onOpenRun={selectRun}
        onInfer={(source) => {
          setInferenceSource(source);
          navigate("perception");
        }}
      />
    );
  else if (page === "validation")
    content = (
      <Validation
        legacyAccess={legacyAccess}
        scenarios={scenarios.data ?? []}
        onSelectRun={selectRun}
        notify={setToast}
      />
    );
  else if (page === "integrations")
    content = adapters.error ? (
      <ErrorState message={adapters.error} retry={adapters.reload} />
    ) : (
      <Integrations
        adapters={adapters.data ?? []}
        onRun={() => setLaunch("")}
      />
    );
  else if (page === "agents")
    content = <Agents scenarios={scenarios.data ?? []} />;
  else
    content = overview.data ? (
      <Overview
        data={overview.data}
        run={detail.data}
        onRun={() => setLaunch("")}
        onCreate={() => setForm("new")}
        onSelect={selectRun}
        onNavigate={navigate}
      />
    ) : null;
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? "mobile-open" : ""}`}>
        <button
          className="brand"
          onClick={() => navigate("workspace")}
          aria-label="AXIOM workspace start"
        >
          <span className="brand-symbol">
            <svg width="29" height="29" viewBox="0 0 32 32">
              <path
                d="m4 27 10-22h5l10 22h-8l-4.5-11L12 27Z"
                fill="currentColor"
              />
            </svg>
          </span>
          <span>AXIOM</span>
        </button>
        <div className="workspace-selector">
          <span className="workspace-avatar">
            <Hexagon size={18} />
          </span>
          <div>
            <strong>Autonomy workspace</strong>
            <span>{externalAccess ? "Shared project workspace" : "Local development"}</span>
          </div>
          <button
            className="icon-button"
            aria-label="Workspace settings"
            onClick={() => setSettings(true)}
          >
            <ChevronDown size={14} />
          </button>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {visiblePages.slice(0, 7).map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? "active" : ""}`}
              onClick={() => navigate(item.id)}
            >
              <item.icon size={18} />
              <span>{item.label}</span>
              {item.id === "scenarios" && scenarios.data && (
                <span className="nav-count">{scenarios.data.length}</span>
              )}
              {page === item.id && <span className="nav-active-dot" />}
            </button>
          ))}
        </nav>
        <div className="nav-label second">BUILD & CONNECT</div>
        <nav>
          {visiblePages.slice(7).map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? "active" : ""}`}
              onClick={() => navigate(item.id)}
            >
              <item.icon size={18} />
              <span>{item.label}</span>
              {item.id === "agents" && <span className="nav-api">API</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
            {legacyAccess && <div className="sidebar-engine-card">
            <div>
              <Layers3 size={15} aria-hidden="true" />
              <strong>Execution engines</strong>
            </div>
            <p>Select an engine for each execution.</p>
            <p className="engine-catalog-state">
              {adapters.error
                ? "Adapter list unavailable"
                : adapters.loading
                  ? "Loading adapter list…"
                  : `${adapters.data?.filter((item) => item.status === "available").length ?? 0} adapters configured · connection checked at execution`}
            </p>
            <button onClick={() => navigate("integrations")}>
              Explore integrations
              <ArrowUpRight size={14} />
            </button>
          </div>}
          {!externalAccess && <a
            className="sidebar-link"
            href="/docs"
            target="_blank"
            rel="noreferrer"
          >
            <BookOpen size={16} />
            Documentation
            <ArrowUpRight size={13} />
          </a>}
          <button className="sidebar-link" onClick={() => setSettings(true)}>
            <Settings2 size={16} />
            Workspace settings
          </button>
          <div className="sidebar-profile">
            <span>SC</span>
            <div>
              <strong>{access.data?.user_label ?? 'Development workspace'}</strong>
              <small>{externalAccess ? (isExternalViewer() ? "External read-only session" : "External project session · Engineer") : `Local session · v${health.data?.version ?? "0.1.0"}`}</small>
            </div>
          </div>
        </div>
      </aside>
      {mobileOpen && (
        <div className="mobile-scrim" onClick={() => setMobileOpen(false)} />
      )}
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-menu icon-button"
              aria-label="Open navigation"
              onClick={() => setMobileOpen((v) => !v)}
            >
              <LayoutDashboard size={20} />
            </button>
            <span className="breadcrumb-root">Workspace</span>
            <ChevronRight size={13} />
            <strong>{currentPage.label}</strong>
          </div>
          <div className="topbar-right">
            <button
              className="global-search"
              aria-label="Search workspace"
              onClick={() => setSearchOpen(true)}
            >
              <Search size={15} />
              <span>Search workspace</span>
              <kbd>⌘ K</kbd>
            </button>
            <div
              className={`connection-status ${(externalAccess ? access.error : health.error) ? "offline" : ""}`}
            >
              <span />
              {externalAccess ? (access.error ? "Viewer connection unavailable" : access.loading ? "Connecting" : isExternalViewer() ? "Read-only access connected" : "Project access connected") : health.error
                ? "API offline"
                : health.loading
                  ? "Connecting"
                  : "Local API connected"}
            </div>
            <button
              className="avatar"
              aria-label="Open workspace settings"
              onClick={() => setSettings(true)}
            >
              SC
            </button>
          </div>
        </header>
        <main className="main-content" key={connectionRevision}>
          {page !== 'validation' && sessionStorage.getItem('axiom-verification-context') && <div className="evidence-note"><a href={`#validation?plan=${encodeURIComponent(sessionStorage.getItem('axiom-verification-context')!)}`}>Return to verification plan</a></div>}
          {page !== "workspace" && demoGalleryReturn(route) && <div className="evidence-note" aria-label="데모 사례 탐색"><a href={demoGalleryReturn(route)!}>← 질문 중심 사례 목록으로</a><span> · 저장된 실행의 비교 사례</span></div>}
          {['object-sim', 'log-sim', 'hil-sim', 'sensor-sim', 'neural-sim'].includes(page) && !(page === 'hil-sim' && new URLSearchParams(route.split('?')[1] ?? '').get('mode') === 'fpga') && <BetaModuleBar key={page} page={page} route={route} defaultProject={access.data?.project_id ?? new URLSearchParams(route.split('?')[1] ?? '').get('project') ?? ''} />}
          {content}
          <footer className="workspace-footer">
            <span>
              <Activity size={12} />
              AXIOM · Modular autonomy development
            </span>
            <span>
              Reference results are model evidence, not real-world safety
              certification.
            </span>
          </footer>
        </main>
      </div>
      {form && (
        <ScenarioForm
          scenario={form === "new" ? undefined : form}
          onClose={() => setForm(null)}
          onSaved={(scenario) => {
            setForm(null);
            refresh();
            setToast(`${scenario.name} saved · revision ${scenario.revision}`);
          }}
        />
      )}
      {launch !== null && (
        <RunLauncher
          scenarios={scenarios.data ?? []}
          adapters={adapters.data ?? []}
          adaptersLoading={adapters.loading}
          adaptersError={adapters.error}
          onReloadAdapters={adapters.reload}
          initialScenario={launch || undefined}
          onClose={() => setLaunch(null)}
          onSubmitted={submittedJob}
        />
      )}{" "}
      {toast && <Toast message={toast} onClose={hideToast} />}{" "}
      {searchOpen && (
        <WorkspaceSearch
          availablePages={visiblePages}
          scenarios={scenarios.data ?? []}
          runs={runs.data ?? []}
          onClose={() => setSearchOpen(false)}
          onScenario={(s) => {
            setSearchOpen(false);
            navigate("scenarios");
            setForm(s);
          }}
          onRun={(id) => {
            setSearchOpen(false);
            selectRun(id);
          }}
          onNavigate={(id) => {
            setSearchOpen(false);
            navigate(id);
          }}
        />
      )}
      {settings && (
        <SettingsDialog
          onClose={() => setSettings(false)}
          onSaved={() => {
            setSettings(false);
            // Nested workspaces also own API requests; discard stale auth errors
            // after validation succeeds, while keeping the current route/hash.
            setConnectionRevision((revision) => revision + 1);
            refresh();
            setToast(
              "API connection settings updated for this browser session",
            );
          }}
        />
      )}
    </div>
  );
}
function WorkspaceSearch({
  availablePages,
  scenarios,
  runs,
  onClose,
  onScenario,
  onRun,
  onNavigate,
}: {
  availablePages: typeof pages;
  scenarios: Scenario[];
  runs: RunSummary[];
  onClose: () => void;
  onScenario: (scenario: Scenario) => void;
  onRun: (id: string) => void;
  onNavigate: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  return (
    <Modal
      title="Search workspace"
      subtitle="Find scenarios, executions, and workspace tools."
      onClose={onClose}
    >
      <div className="modal-body">
        <SearchField
          value={query}
          onChange={setQuery}
          placeholder="Search by name or run ID…"
        />
        <div className="search-results">
          <span className="eyebrow">SCENARIOS</span>
          {scenarios
            .filter((s) => s.name.toLowerCase().includes(query.toLowerCase()))
            .slice(0, 5)
            .map((s) => (
              <button key={s.id} onClick={() => onScenario(s)}>
                <Box size={16} />
                <span>{s.name}</span>
                <small>r{s.revision}</small>
                <ArrowRight size={14} />
              </button>
            ))}
          <span className="eyebrow">EXECUTIONS</span>
          {runs
            .filter((r) =>
              `${r.scenario_name} ${r.id} ${r.adapter_id} ${r.provenance.adapter_version}`
                .toLowerCase()
                .includes(query.toLowerCase()),
            )
            .slice(0, 4)
            .map((r) => (
              <button key={r.id} onClick={() => onRun(r.id)}>
                <Play size={15} />
                <span>{runOptionLabel(r)}</span>
                <ArrowRight size={14} />
              </button>
            ))}
          <span className="eyebrow">PAGES</span>
          {availablePages
            .filter((p) => p.label.toLowerCase().includes(query.toLowerCase()))
            .map((p) => (
              <button key={p.id} onClick={() => onNavigate(p.id)}>
                <p.icon size={16} />
                <span>{p.label}</span>
                <ArrowRight size={14} />
              </button>
            ))}
        </div>
      </div>
    </Modal>
  );
}
function SettingsDialog({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const externalAccess = isExternalAccess();
  const [token, setToken] = useState(
      sessionStorage.getItem("axiom.apiToken") ?? "",
    ),
    [checking, setChecking] = useState(false),
    [error, setError] = useState("");
  async function save() {
    setChecking(true);
    setError("");
    try {
      const response = await fetch("/api/v1/team/whoami", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok)
        throw new Error(
          `API connection rejected (${response.status}). Verify your bearer token.`,
        );
      if (token) sessionStorage.setItem("axiom.apiToken", token);
      else sessionStorage.removeItem("axiom.apiToken");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }
  return (
    <Modal
      title="Workspace settings"
      subtitle={externalAccess ? "Password-authenticated access to shared results." : "Connect this browser session to the local platform API."}
      onClose={onClose}
    >
      <div className="modal-body">
        <div className="form-note">
          <strong>API endpoint</strong>
          <p className="mono">{window.location.origin}/api/v1</p>
        </div>
        {externalAccess ? <div className="form-note"><strong>{isExternalViewer() ? "외부 조회 전용" : "외부 프로젝트 · 수정·실행 가능"}</strong><p>접속 비밀번호로 인증된 상태입니다. API 토큰을 입력할 필요가 없습니다. {isExternalViewer() ? "공유된 프로젝트의 기존 결과를 조회하고 다운로드할 수 있습니다." : "공유된 프로젝트의 시나리오·실험을 작성하고 실행할 수 있습니다."}</p></div> : <><label>
          Bearer token <span className="optional">optional</span>
          <input
            type="password"
            value={token}
            autoComplete="off"
            onChange={(e) => setToken(e.target.value)}
            placeholder="Leave empty when local authentication is disabled"
          />
        </label>
        <p className="form-help">
          Stored only in this tab's browser session. Set a token only when the
          backend has authentication enabled. Downloads and image previews use
          the same credentials.
        </p></>}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <footer>
        <button className="button secondary" onClick={onClose}>
          Cancel
        </button>
        {!externalAccess && <button className="button primary" disabled={checking} onClick={save}>
          {checking ? (
            <LoaderCircle size={15} className="spin" />
          ) : (
            <Check size={15} />
          )}
          Test & save connection
        </button>}
      </footer>
    </Modal>
  );
}
