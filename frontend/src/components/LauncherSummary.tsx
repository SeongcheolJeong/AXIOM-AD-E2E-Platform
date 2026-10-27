import type { Adapter, Scenario } from "../types";
import {
  isConfiguredControl,
  type SimulationControlSelection,
} from "./carla/CarlaTypes";
import { engineName } from "./EngineProvenance";

export default function LauncherSummary({
  adapter,
  adapterId,
  scenario,
  seed,
  controlMode,
  control,
  controlValid,
  adapterUsable,
}: {
  adapter?: Adapter;
  adapterId: string;
  scenario?: Scenario;
  seed: string;
  controlMode: "legacy" | "stack";
  control: SimulationControlSelection;
  controlValid: boolean;
  adapterUsable: boolean;
}) {
  const live = adapterId === "carla" && controlMode === "stack";
  return (
    <section
      className="launcher-selection-summary"
      data-testid="launch-selection-summary"
      aria-label="Execution selection summary"
    >
      <h3>Execution selection</h3>
      <dl>
        <div>
          <dt>Engine</dt>
          <dd>
            {engineName(adapterId)} ·{" "}
            {adapter?.version ?? "version unavailable"}
            <br />
            <code>{adapterId}</code> ·{" "}
            {adapterUsable
              ? "configured; execution preflight still required"
              : "unavailable for submission"}
          </dd>
        </div>
        <div>
          <dt>Controller</dt>
          <dd>
            {adapterId === "reference"
              ? "Reference object model"
              : adapterId === "carla"
                ? live
                  ? "Saved shared stack · live LiDAR"
                  : "Surrounding actor ground-truth controller"
                : "Adapter-defined controller"}
          </dd>
        </div>
        <div>
          <dt>Scenario revision</dt>
          <dd>
            {scenario
              ? `${scenario.name} · r${scenario.revision}`
              : "No scenario selected"}
            {scenario && (
              <>
                <br />
                <code>{scenario.id}</code>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>Effective seed</dt>
          <dd>{seed === "" ? (scenario?.seed ?? "Unavailable") : seed}</dd>
        </div>
        {live && (
          <>
            <div>
              <dt>Selected stack</dt>
              <dd>
                {control.stack_id && control.stack_revision ? (
                  <>
                    <code>{control.stack_id}</code> · r{control.stack_revision}
                  </>
                ) : (
                  "Choose an exact saved revision"
                )}
              </dd>
            </div>
            <div>
              <dt>Selected rig</dt>
              <dd>
                {control.rig_id && control.rig_revision ? (
                  <>
                    <code>{control.rig_id}</code> · r{control.rig_revision}
                  </>
                ) : (
                  "Choose an exact saved revision"
                )}
              </dd>
            </div>
            <div>
              <dt>LiDAR sensor key</dt>
              <dd>{control.lidar_sensor_key || "Not selected"}</dd>
            </div>
            <div>
              <dt>
                {isConfiguredControl(control)
                  ? "Sensor configuration"
                  : "Sensor profile"}
              </dt>
              <dd>
                {isConfiguredControl(control)
                  ? `${control.sensor_configuration_id || "Not selected"} · r${control.sensor_configuration_revision || "?"}`
                  : control.sensor_profile_id}
              </dd>
            </div>
          </>
        )}
      </dl>
      <p>
        {adapterId === "reference"
          ? "Synthetic, uncalibrated object simulation. No camera, LiDAR or perception model. The scenario name does not select an engine."
          : adapterId === "carla"
            ? live
              ? "LiDAR drives the geometric controller; ego state and map heading are declared oracles. No RGB input or capture. Saved transforms do not certify axes or calibration."
              : "Surrounding actor ground truth drives decisions. Captured RGB/LiDAR are evidence and do not drive this controller."
            : (adapter?.description ??
              "Adapter metadata must load before this selection can be submitted.")}
      </p>
      {live && (
        <p className={controlValid ? undefined : "launcher-selection-invalid"}>
          {controlValid
            ? "Exact revisions loaded and local profile checks passed. The backend verifies capabilities and connection before dispatch."
            : "Live configuration is incomplete or could not be verified. Submission remains disabled."}
        </p>
      )}
      <p>
        Changing the engine or controller clears the live stack and rig
        selection.
      </p>
    </section>
  );
}
