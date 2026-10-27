import { useCallback, useRef, useState, type FormEvent } from "react";
import { Box, LoaderCircle, Play } from "lucide-react";
import { api } from "../api";
import { categoryLabels, type Adapter, type Scenario } from "../types";
import { Modal } from "./ui";
import type { JobDetail } from "./jobs/JobTypes";
import CarlaControlFields from "./carla/CarlaControlFields";
import LauncherSummary from "./LauncherSummary";
import {
  EMPTY_CARLA_CONTROL,
  type SimulationControlSelection,
} from "./carla/CarlaTypes";
export default function RunLauncher({
  scenarios,
  adapters,
  adaptersLoading = false,
  adaptersError = "",
  onReloadAdapters,
  initialScenario,
  onClose,
  onSubmitted,
}: {
  scenarios: Scenario[];
  adapters: Adapter[];
  adaptersLoading?: boolean;
  adaptersError?: string;
  onReloadAdapters?: () => void;
  initialScenario?: string;
  onClose: () => void;
  onSubmitted: (job: JobDetail) => void;
}) {
  const submissionKeys = useRef(new Map<string, string>());
  const [controlMode, setControlMode] = useState<"legacy" | "stack">("legacy");
  const [control, setControl] =
    useState<SimulationControlSelection>(EMPTY_CARLA_CONTROL);
  const [validation, setValidation] = useState<{
    identity: string;
    valid: boolean;
  } | null>(null);
  const [scenarioId, setScenarioId] = useState(
      initialScenario ?? scenarios[0]?.id ?? "",
    ),
    [adapterId, setAdapterId] = useState(
      adapters.find((a) => a.status === "available")?.id ?? "reference",
    ),
    [seed, setSeed] = useState(""),
    [running, setRunning] = useState(false),
    [error, setError] = useState("");
  const selected = scenarios.find((s) => s.id === scenarioId),
    adapter = adapters.find((a) => a.id === adapterId);
  const controlIdentity = JSON.stringify([
    control,
    adapterId,
    adapter?.version,
  ]);
  const onControlValidity = useCallback(
    (valid: boolean) => {
      setValidation({ identity: controlIdentity, valid });
    },
    [controlIdentity],
  );
  const controlValid =
    validation?.identity === controlIdentity && validation.valid;
  const declaredCategories =
    adapter?.capabilities.filter((item) => item in categoryLabels) ?? [];
  const categoryCompatible =
    !declaredCategories.length ||
    (!!selected && declaredCategories.includes(selected.category));
  const adapterUsable =
    !adaptersLoading && !adaptersError && adapter?.status === "available";
  function resetControl() {
    setControl({ ...EMPTY_CARLA_CONTROL });
    setValidation(null);
    setError("");
  }
  const liveScenarioCompatible =
    !!selected &&
    ["lane_follow", "emergency_brake"].includes(selected.category) &&
    selected.duration_s >= 2 &&
    selected.duration_s <= 4 &&
    Math.abs(
      selected.duration_s / 0.05 - Math.round(selected.duration_s / 0.05),
    ) < 1e-6;
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!selected || !adapterUsable || !categoryCompatible) {
      setError(
        "Select an available adapter and a supported scenario before submitting.",
      );
      return;
    }
    if (
      controlMode === "stack" &&
      (adapterId !== "carla" ||
        !controlValid ||
        !liveScenarioCompatible ||
        !adapter?.capabilities.includes("live_lidar_stack"))
    ) {
      setError(
        "Choose a validated, exact stack and rig revision for the CARLA live profile.",
      );
      return;
    }
    setRunning(true);
    setError("");
    const body = JSON.stringify({
      scenario_id: scenarioId,
      adapter_id: adapterId,
      ...(seed !== "" ? { seed: Number(seed) } : {}),
      ...(controlMode === "stack" ? { control } : {}),
    });
    let submissionKey = submissionKeys.current.get(body);
    if (!submissionKey) {
      submissionKey = crypto.randomUUID();
      submissionKeys.current.set(body, submissionKey);
    }
    try {
      const job = await api<JobDetail>("/jobs", {
        method: "POST",
        headers: { "Idempotency-Key": submissionKey },
        body,
      });
      onSubmitted(job);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }
  return (
    <Modal
      className={controlMode === "stack" ? "carla-launch-modal" : ""}
      title="Launch a simulation"
      subtitle="Capture scenario inputs and submit an execution to the durable queue."
      onClose={() => {
        if (!running) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="modal-body">
          <label>
            Scenario
            <select
              aria-label="Scenario"
              required
              disabled={running}
              value={scenarioId}
              onChange={(e) => {
                setScenarioId(e.target.value);
                setError("");
              }}
            >
              {scenarios.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} · r{s.revision} · {s.id.slice(0, 12)}
                </option>
              ))}
            </select>
          </label>
          {selected && (
            <div className="scenario-summary">
              <Box size={24} />
              <div>
                <strong>{categoryLabels[selected.category]}</strong>
                <span>
                  {selected.map_name} · {selected.weather} ·{" "}
                  {selected.duration_s}s · seed {selected.seed}
                </span>
              </div>
            </div>
          )}
          <label>
            Execution adapter
            <select
              aria-label="Execution adapter"
              disabled={running}
              value={adapterId}
              onChange={(e) => {
                setAdapterId(e.target.value);
                setControlMode("legacy");
                resetControl();
              }}
            >
              {!adapter && (
                <option value={adapterId} disabled>
                  {adapterId} · unavailable in current adapter list
                </option>
              )}
              {adapters.map((a) => (
                <option
                  key={a.id}
                  value={a.id}
                  disabled={a.status !== "available"}
                >
                  {a.name} · {a.id} · {a.version}{" "}
                  {a.status !== "available"
                    ? `(${a.status.replaceAll("_", " ")})`
                    : ""}
                </option>
              ))}
            </select>
          </label>
          {(adaptersError || !adapterUsable) && (
            <div className="inline-error" role="alert">
              {adaptersError ||
                (adaptersLoading
                  ? "Loading the adapter list…"
                  : "The selected adapter is unavailable. Choose an available engine explicitly.")}
              {onReloadAdapters && (
                <button
                  type="button"
                  className="button secondary small"
                  disabled={running || adaptersLoading}
                  onClick={onReloadAdapters}
                >
                  Retry adapter list
                </button>
              )}
            </div>
          )}
          {!categoryCompatible && (
            <p className="inline-error" role="alert">
              The selected adapter does not declare support for{" "}
              {selected ? categoryLabels[selected.category] : "this scenario"}.
              Choose a supported scenario or another engine.
            </p>
          )}
          {adapter && (
            <div className="form-note">
              <strong>
                {adapter.name} · {adapter.version}
              </strong>
              <p>{adapter.description}</p>
              {adapter.limitations[0] && <p>{adapter.limitations[0]}</p>}
            </div>
          )}
          {adapterId === "carla" && (
            <>
              <label>
                CARLA controller
                <select
                  aria-label="CARLA controller mode"
                  disabled={running}
                  value={controlMode}
                  onChange={(event) => {
                    setControlMode(event.target.value as "legacy" | "stack");
                    resetControl();
                  }}
                >
                  <option value="legacy">
                    Existing ground-truth controller
                  </option>
                  <option
                    value="stack"
                    disabled={
                      !adapter?.capabilities.includes("live_lidar_stack")
                    }
                  >
                    Saved shared stack · live sensor inputs
                  </option>
                </select>
              </label>
              {controlMode === "legacy" ? (
                <p className="form-help">
                  The existing controller uses surrounding actor ground truth.
                  Captured RGB/LiDAR do not drive its decisions.
                </p>
              ) : (
                <>
                  {!liveScenarioCompatible && (
                    <p className="inline-error" role="alert">
                      The live profile supports lane following or emergency
                      braking, 2–4 seconds in 0.05-second intervals. Select or
                      author a supported scenario.
                    </p>
                  )}
                  <CarlaControlFields
                    value={control}
                    disabled={running}
                    adapterVersion={adapter?.version}
                    effectiveSeed={seed === "" ? selected?.seed : Number(seed)}
                    onChange={(value) => {
                      setValidation(null);
                      setError("");
                      setControl(value);
                    }}
                    onValidity={onControlValidity}
                  />
                </>
              )}
            </>
          )}
          <label>
            Seed override <span className="optional">optional</span>
            <input
              type="number"
              disabled={running}
              min={0}
              max={2147483647}
              step={1}
              value={seed}
              onChange={(e) => {
                setSeed(e.target.value);
                setError("");
              }}
              placeholder={`Use scenario seed (${selected?.seed ?? 42})`}
            />
          </label>
          <LauncherSummary
            adapter={adapter}
            adapterId={adapterId}
            scenario={selected}
            seed={seed}
            controlMode={controlMode}
            control={control}
            controlValid={!!controlValid}
            adapterUsable={!!adapterUsable}
          />
          {error && (
            <div>
              <p className="inline-error" role="alert">
                {error}
              </p>
              <p className="form-help">
                The same inputs reuse this submission's request key when
                retried. A lost response does not mean execution was rejected.
                Changing inputs creates a separate request.
              </p>
            </div>
          )}
        </div>
        <footer>
          <button
            type="button"
            className="button secondary"
            disabled={running}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="button primary"
            disabled={
              running ||
              !selected ||
              !adapterUsable ||
              !categoryCompatible ||
              (controlMode === "stack" &&
                (!controlValid ||
                  !liveScenarioCompatible ||
                  !adapter?.capabilities.includes("live_lidar_stack")))
            }
          >
            {running ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <Play size={15} />
            )}{" "}
            {running
              ? "Submitting execution…"
              : error
                ? "Retry submission"
                : "Run simulation"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
