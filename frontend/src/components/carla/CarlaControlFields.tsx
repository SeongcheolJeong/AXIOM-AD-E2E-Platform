import { useEffect } from "react";
import { useSensorResource } from "../sensors/SensorData";
import type { RigDetail } from "../sensors/SensorTypes";
import type { StackDetail, StackComponentMetadata } from "../stacks/StackTypes";
import { stackAvailable } from "../stacks/StackKernels";
import type {
  CarlaSensorProfileId,
  SimulationControlSelection,
  SimulationSensorProfileOption,
} from "./CarlaTypes";
import { isConfiguredControl, EMPTY_CARLA_CONTROL } from "./CarlaTypes";
import type { SensorConfigurationDetail } from "./SensorConfigurationTypes";
import {
  readSensorCapabilities,
  validConfiguration,
} from "./SensorConfigurationData";
import { readSensorProfileCatalog } from "./CarlaSensorProfiles";
import "./carla.css";

/** Choices name immutable revisions. A refreshed library never advances them. */
export default function CarlaControlFields({
  value,
  disabled,
  adapterVersion,
  effectiveSeed,
  onChange,
  onValidity,
}: {
  value: SimulationControlSelection;
  disabled: boolean;
  adapterVersion?: string;
  effectiveSeed?: number;
  onChange: (value: SimulationControlSelection) => void;
  onValidity: (valid: boolean) => void;
}) {
  const configured = isConfiguredControl(value);
  const configCatalog = useSensorResource<SensorConfigurationDetail[]>(
    configured ? "/simulation-sensor-configurations" : null,
  );
  const capabilityCatalog = useSensorResource<unknown>(
    configured ? "/simulation-sensor-capabilities?adapter_id=carla" : null,
  );
  let capabilities = [] as ReturnType<typeof readSensorCapabilities>;
  let capabilityError = capabilityCatalog.error;
  if (capabilityCatalog.data !== null) {
    try {
      capabilities = readSensorCapabilities(capabilityCatalog.data);
    } catch (error) {
      capabilityError = (error as Error).message;
    }
  }
  const configHistory = useSensorResource<SensorConfigurationDetail[]>(
    configured && value.sensor_configuration_id
      ? `/simulation-sensor-configurations/${encodeURIComponent(value.sensor_configuration_id)}/revisions`
      : null,
  );
  const configResult = useSensorResource<SensorConfigurationDetail>(
    configured &&
      value.sensor_configuration_id &&
      value.sensor_configuration_revision > 0
      ? `/simulation-sensor-configurations/${encodeURIComponent(value.sensor_configuration_id)}?revision=${value.sensor_configuration_revision}`
      : null,
  );
  const configuration =
    configured &&
    validConfiguration(configResult.data) &&
    configResult.data.id === value.sensor_configuration_id &&
    configResult.data.revision === value.sensor_configuration_revision
      ? configResult.data
      : null;
  const capability = capabilities.find(
    (item) =>
      item.id === configuration?.capability_id &&
      item.version === configuration?.capability_version &&
      item.schema_hash === configuration?.capability_schema_hash,
  );
  const configurationCompatible =
    configured &&
    !!configuration &&
    !!capability &&
    capability.supported_adapter_versions.includes(adapterVersion ?? "") &&
    !configResult.loading &&
    !capabilityCatalog.loading &&
    !capabilityError;
  const stacks = useSensorResource<StackDetail[]>("/stacks");
  const components =
    useSensorResource<StackComponentMetadata[]>("/stack-components");
  const rigs = useSensorResource<RigDetail[]>("/sensor-rigs");
  const catalog = useSensorResource<unknown>(
    configured ? null : "/simulation-sensor-profiles",
  );
  let profiles: SimulationSensorProfileOption[] = [];
  let catalogError = catalog.error;
  if (catalog.data !== null) {
    try {
      profiles = readSensorProfileCatalog(catalog.data);
    } catch (error) {
      catalogError = (error as Error).message;
    }
  }
  const profile = profiles.find(
    (item) => !configured && item.id === value.sensor_profile_id,
  );
  const profileCompatible =
    !catalog.loading &&
    !catalogError &&
    !!profile &&
    !!adapterVersion &&
    profile.supported_adapter_versions.includes(adapterVersion);
  const stackHistory = useSensorResource<StackDetail[]>(
    value.stack_id
      ? `/stacks/${encodeURIComponent(value.stack_id)}/revisions`
      : null,
  );
  const rigHistory = useSensorResource<RigDetail[]>(
    value.rig_id
      ? `/sensor-rigs/${encodeURIComponent(value.rig_id)}/revisions`
      : null,
  );
  const stackResult = useSensorResource<StackDetail>(
    value.stack_id && value.stack_revision > 0
      ? `/stacks/${encodeURIComponent(value.stack_id)}?revision=${value.stack_revision}`
      : null,
  );
  const rigResult = useSensorResource<RigDetail>(
    value.rig_id && value.rig_revision > 0
      ? `/sensor-rigs/${encodeURIComponent(value.rig_id)}?revision=${value.rig_revision}`
      : null,
  );
  const stack =
    stackResult.data?.id === value.stack_id &&
    stackResult.data.revision === value.stack_revision
      ? stackResult.data
      : null;
  const rig =
    rigResult.data?.id === value.rig_id &&
    rigResult.data.revision === value.rig_revision
      ? rigResult.data
      : null;
  const sensor = rig?.sensors.find(
    (item) => item.key === value.lidar_sensor_key,
  );
  const errors = [
    stacks.error,
    rigs.error,
    stackHistory.error,
    rigHistory.error,
    stackResult.error,
    components.error,
    rigResult.error,
    ...(configured
      ? [
          configCatalog.error,
          configHistory.error,
          configResult.error,
          capabilityError,
        ]
      : []),
  ].filter(Boolean);
  const mountingCompatible =
    !!stack &&
    !!rig &&
    stack.definition.reference_frame === "ego" &&
    rig.reference_frame === "ego" &&
    rig.coordinate_convention === "right_handed_z_up" &&
    sensor?.kind === "lidar" &&
    !!sensor.transform &&
    (configured
      ? !!sensor.frame_id &&
        sensor.transform.translation_m.length === 3 &&
        sensor.transform.translation_m.every(Number.isFinite) &&
        Math.abs(sensor.transform.translation_m[0]) <= 5 &&
        Math.abs(sensor.transform.translation_m[1]) <= 5 &&
        sensor.transform.translation_m[2] >= 0 &&
        sensor.transform.translation_m[2] <= 5 &&
        sensor.transform.rotation_xyzw.length === 4 &&
        sensor.transform.rotation_xyzw.every(Number.isFinite) &&
        Math.abs(
          sensor.transform.rotation_xyzw.reduce(
            (sum, item) => sum + item * item,
            0,
          ) - 1,
        ) <= 1e-6
      : sensor.frame_id === "lidar_roof" &&
        JSON.stringify(sensor.transform.translation_m) === "[0,0,2.2]" &&
        JSON.stringify(sensor.transform.rotation_xyzw) === "[0,0,0,1]");
  const kernelCompatible =
    !!stack &&
    stackAvailable(stack, components.data ?? []) &&
    (configured
      ? !!capability?.supported_kernel_versions.includes(stack.kernel_version)
      : stack.kernel_version !== "stack-kernel/2" ||
        ["0.7.0", "0.8.0"].includes(adapterVersion ?? ""));
  const compatible =
    errors.length === 0 &&
    mountingCompatible &&
    (configured ? configurationCompatible : profileCompatible) &&
    kernelCompatible;
  const selectionIdentity = JSON.stringify([value, adapterVersion]);
  useEffect(() => {
    onValidity(compatible);
    return () => onValidity(false);
  }, [compatible, selectionIdentity, onValidity]);
  const ground = stack?.definition.perception.parameters.ground_reference_z_m;
  function retry() {
    onValidity(false);
    catalog.reload();
    configCatalog.reload();
    capabilityCatalog.reload();
    configHistory.reload();
    configResult.reload();
    stacks.reload();
    components.reload();
    rigs.reload();
    stackHistory.reload();
    rigHistory.reload();
    stackResult.reload();
    rigResult.reload();
  }
  return (
    <fieldset className="carla-control-fields" disabled={disabled}>
      <legend>Pin the live controller inputs</legend>
      <div className="carla-scope-notice">
        <strong>SENSOR-DRIVEN GEOMETRIC PILOT</strong>
        <p>
          LiDAR points drive the shared observation → policy → control kernel.
          Ego state and map route heading are declared oracles. This mode has no
          RGB input or capture. Surrounding actor ground truth is retained
          separately for observation.
        </p>
      </div>
      <label>
        Sensor setup
        <select
          aria-label="Live CARLA sensor setup"
          value={configured ? "configuration" : "profile"}
          onChange={(event) => {
            onValidity(false);
            const common = {
              mode: "stack" as const,
              stack_id: value.stack_id,
              stack_revision: value.stack_revision,
              rig_id: value.rig_id,
              rig_revision: value.rig_revision,
              lidar_sensor_key: value.lidar_sensor_key,
            };
            onChange(
              event.target.value === "configuration"
                ? {
                    ...common,
                    schema_version: "simulation-control-selection/2",
                    sensor_configuration_id: "",
                    sensor_configuration_revision: 0,
                  }
                : {
                    ...common,
                    sensor_profile_id: EMPTY_CARLA_CONTROL.sensor_profile_id,
                  },
            );
          }}
        >
          <option value="profile">Fixed roof profile</option>
          <option value="configuration">
            Saved sampling configuration + physical rig
          </option>
        </select>
      </label>
      <div className="carla-form-grid">
        <label>
          Component stack
          <select
            aria-label="Live CARLA stack"
            required
            value={value.stack_id}
            onChange={(event) =>
              onChange({
                ...value,
                stack_id: event.target.value,
                stack_revision: 0,
              })
            }
          >
            <option value="">Choose a saved stack</option>
            {stacks.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Exact stack revision
          <select
            aria-label="Live CARLA stack revision"
            required
            value={value.stack_revision || ""}
            onChange={(event) =>
              onChange({ ...value, stack_revision: Number(event.target.value) })
            }
          >
            <option value="">Choose a revision</option>
            {stackHistory.data
              ?.filter((item) => item.id === value.stack_id)
              .map((item) => (
                <option key={item.revision} value={item.revision}>
                  r{item.revision} · {item.kernel_version}
                </option>
              ))}
          </select>
        </label>
        <label>
          Sensor rig
          <select
            aria-label="Live CARLA sensor rig"
            required
            value={value.rig_id}
            onChange={(event) =>
              onChange({
                ...value,
                rig_id: event.target.value,
                rig_revision: 0,
                lidar_sensor_key: "",
              })
            }
          >
            <option value="">Choose a saved rig</option>
            {rigs.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Exact rig revision
          <select
            aria-label="Live CARLA rig revision"
            required
            value={value.rig_revision || ""}
            onChange={(event) =>
              onChange({
                ...value,
                rig_revision: Number(event.target.value),
                lidar_sensor_key: "",
              })
            }
          >
            <option value="">Choose a revision</option>
            {rigHistory.data
              ?.filter((item) => item.id === value.rig_id)
              .map((item) => (
                <option key={item.revision} value={item.revision}>
                  r{item.revision} · {item.calibration_status}
                </option>
              ))}
          </select>
        </label>
        <label>
          LiDAR sensor key
          <select
            aria-label="Live CARLA LiDAR sensor"
            required
            value={value.lidar_sensor_key}
            onChange={(event) =>
              onChange({ ...value, lidar_sensor_key: event.target.value })
            }
          >
            <option value="">Choose the mounted LiDAR</option>
            {rig?.sensors
              .filter((item) => item.kind === "lidar")
              .map((item) => (
                <option key={item.key} value={item.key}>
                  {item.name} · {item.key} · {item.frame_id}
                </option>
              ))}
          </select>
        </label>
        {!configured && (
          <label>
            Sensor profile
            <select
              aria-label="Live CARLA sensor profile"
              value={value.sensor_profile_id}
              disabled={catalog.loading || !!catalogError}
              required
              onChange={(event) =>
                onChange({
                  ...value,
                  sensor_profile_id: event.target.value as CarlaSensorProfileId,
                })
              }
            >
              {!profile && (
                <option value={value.sensor_profile_id} disabled>
                  {value.sensor_profile_id} ·{" "}
                  {catalog.loading ? "loading catalog" : "unavailable"}
                </option>
              )}
              {profiles.map((item) => {
                const supported =
                  !!adapterVersion &&
                  item.supported_adapter_versions.includes(adapterVersion);
                return (
                  <option key={item.id} value={item.id} disabled={!supported}>
                    {item.name} · {item.id}
                    {supported
                      ? ""
                      : ` · requires ${item.supported_adapter_versions.join(" / ") || "an unavailable adapter version"}`}
                  </option>
                );
              })}
            </select>
          </label>
        )}
      </div>
      {configured && (
        <>
          <div className="carla-form-grid">
            <label>
              Sampling configuration
              <select
                aria-label="Live CARLA sensor configuration"
                value={value.sensor_configuration_id}
                onChange={(event) =>
                  onChange({
                    ...value,
                    sensor_configuration_id: event.target.value,
                    sensor_configuration_revision: 0,
                  })
                }
              >
                <option value="">Choose a saved configuration</option>
                {configCatalog.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Exact sampling revision
              <select
                aria-label="Live CARLA sensor configuration revision"
                value={value.sensor_configuration_revision || ""}
                onChange={(event) =>
                  onChange({
                    ...value,
                    sensor_configuration_revision: Number(event.target.value),
                  })
                }
              >
                <option value="">Choose a revision</option>
                {configHistory.data
                  ?.filter((item) => item.id === value.sensor_configuration_id)
                  .map((item) => (
                    <option key={item.revision} value={item.revision}>
                      r{item.revision} · {item.note}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <p>
            Create or revise recipes and physical rigs in the Simulation sensors
            workspace. This selection never advances to a newer revision
            automatically.
          </p>
          {configuration && (
            <div
              className="carla-pinned-definition"
              data-testid="carla-selected-configuration"
            >
              <strong>
                {configuration.name} · fixed r{configuration.revision}
              </strong>
              <code>{configuration.manifest_hash}</code>
              <p>
                {Number(configuration.parameters.points_per_second) / 20}{" "}
                nominal rays per frame; actual returns are measured separately.
                All returns are retained.
              </p>
              <pre>
                {JSON.stringify(
                  {
                    parameters: configuration.parameters,
                    effective_noise_seed: effectiveSeed ?? null,
                    sensor_frame: sensor?.frame_id ?? null,
                    physical_mount: sensor?.transform ?? null,
                  },
                  null,
                  2,
                )}
              </pre>
            </div>
          )}
          <p>
            The seed records requested configuration. Sensor RNG application is
            unverified; changing the seed may produce identical points.
          </p>
          {!configurationCompatible && (
            <p className="inline-error" role="alert">
              Saved sensor configuration is incomplete or unavailable for CARLA{" "}
              {adapterVersion ?? "unknown"}. This capability requires an
              explicitly supported engine version and exact loaded revision.
              Submission is blocked.
            </p>
          )}
          <p className="carla-subtle">
            Physical mounting requires an explicit RH ego rig: X/Y within ±5 m,
            Z within 0–5 m and a unit quaternion. The engine must confirm the
            actual attributes, parent and pose before measured driving.
            Registration is not calibration or visibility acceptance; the
            backend checks the full execution evidence budget.
          </p>
        </>
      )}
      {profile && (
        <div
          className="carla-pinned-definition"
          data-testid="carla-selected-profile"
        >
          <strong>
            {profile.name} · {profile.id}
          </strong>
          <p>{profile.description}</p>
          <p>
            {profile.nominal_points_per_frame.toLocaleString("en-US")} nominal
            rays per frame · {profile.attributes.points_per_second} rays/s ·{" "}
            {profile.attributes.channels} channels ·{" "}
            {profile.attributes.rotation_frequency} Hz. Actual returned and
            consumed point counts are measured per observation; geometry and
            dropout can reduce them.
          </p>
          <p>
            Noise seed {effectiveSeed ?? "unavailable"} follows the effective
            scenario seed, including any explicit override. It is pinned at
            submission; the profile does not choose a separate seed.
          </p>
          <p>
            The seed records requested configuration. Sensor RNG application is
            unverified; changing the seed may produce identical points.
          </p>
        </div>
      )}
      {!configured &&
        !catalog.loading &&
        !catalogError &&
        !profileCompatible && (
          <p className="inline-error" role="alert">
            Sensor profile {value.sensor_profile_id} is unavailable for CARLA{" "}
            {adapterVersion ?? "version unavailable"}. Choose an explicitly
            supported profile; the selection is not replaced automatically.
          </p>
        )}
      {!configured && (
        <p className="carla-subtle">
          Profile requires RH ego coordinates, lidar_roof, translation [0, 0,
          2.2] m and quaternion [0, 0, 0, 1]. Declared mounting does not
          establish measured calibration. The server rechecks the exact recipe
          and installed component versions before dispatch.
        </p>
      )}
      {stack && (
        <div
          className="carla-pinned-definition"
          data-testid="carla-pinned-stack"
        >
          <strong>
            {stack.name} · fixed r{stack.revision}
          </strong>
          <code>{stack.manifest_hash}</code>
          <span>{stack.kernel_version}</span>
          <span>
            {["perception", "policy", "controller"]
              .map((role) => {
                const selection =
                  stack.definition[
                    role as "perception" | "policy" | "controller"
                  ];
                return `${selection.id}/${selection.version}`;
              })
              .join(" · ")}
          </span>
          <p>
            {ground == null
              ? "Ground is unknown in this revision. The shared kernel retains its unknown-stop policy; the launcher does not invent a ground height."
              : `Declared ground ${ground} m · ${String(stack.definition.perception.parameters.ground_reference_source ?? "source unavailable")}`}
          </p>
        </div>
      )}
      {stack && !kernelCompatible && (
        <p className="inline-error" role="alert">
          The exact saved kernel and component versions must be available.
          Kernel 2 live execution requires CARLA adapter 0.7.0 or 0.8.0. No
          component is substituted.
        </p>
      )}
      {rig && (
        <div className="carla-pinned-definition" data-testid="carla-pinned-rig">
          <strong>
            {rig.name} · fixed r{rig.revision}
          </strong>
          <code>{rig.manifest_hash}</code>
          <span>
            {rig.calibration_status} · {rig.reference_frame} ·{" "}
            {rig.coordinate_convention}
          </span>
        </div>
      )}
      {stack && rig && (
        <details className="carla-pinned-definition">
          <summary>Inspect the pinned stack and rig definitions</summary>
          <pre>{JSON.stringify({ stack, rig }, null, 2)}</pre>
        </details>
      )}
      {stack && rig && sensor && !mountingCompatible && (
        <p className="inline-error" role="alert">
          The selected stack/rig/LiDAR does not match the supported live
          profile. Choose an explicit compatible revision; no replacement is
          selected automatically.
        </p>
      )}
      {(errors.length > 0 || catalogError) && (
        <div className="inline-error" role="alert">
          {[
            ...new Set([
              ...errors,
              ...(catalogError
                ? [`Sensor profile catalog unavailable: ${catalogError}`]
                : []),
            ]),
          ].map((error) => (
            <p key={error}>{error}</p>
          ))}
          <button
            type="button"
            className="button secondary small"
            onClick={retry}
          >
            Retry live input definitions
          </button>
        </div>
      )}
      <p className="carla-subtle">
        A library update leaves these revision numbers unchanged. Unknown sensor
        observations can request a stop; this pilot is not free-space,
        detector-accuracy or vehicle-safety certification.
      </p>
    </fieldset>
  );
}
