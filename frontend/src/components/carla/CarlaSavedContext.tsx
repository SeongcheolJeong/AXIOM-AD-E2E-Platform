import type { PreparedSimulationContext } from "./CarlaTypes";
import { validateLiveContext } from "./CarlaLiveData";
import "./carla.css";

export default function CarlaSavedContext({
  context,
}: {
  context: PreparedSimulationContext;
}) {
  try {
    validateLiveContext(context);
  } catch (error) {
    return (
      <p role="alert" className="inline-error">
        Saved live control context rejected: {(error as Error).message}
      </p>
    );
  }
  const stack = context.stack_snapshot,
    rig = context.rig_snapshot;
  const ground = stack.definition.perception.parameters.ground_reference_z_m;
  const attributes = context.sensor_profile.attributes;
  const nominal =
    Number(attributes.points_per_second) /
    Number(attributes.rotation_frequency);
  return (
    <div className="carla-saved-context" data-testid="carla-saved-context">
      <div className="carla-scope-notice">
        <strong>PINNED LIVE SENSOR CONTROLLER</strong>
        <p>
          LiDAR geometric stack + ego/map oracle; no RGB input or capture.
          Surrounding actor GT remains an observer and metric source. Ray-cast
          returns and declared calibration do not establish real-world sensor
          performance.
        </p>
      </div>
      <div className="carla-context-grid">
        <div>
          <span>Immutable stack</span>
          <strong>
            {stack.name} · r{stack.revision}
          </strong>
          <code>{stack.id}</code>
          <span>{stack.kernel_version}</span>
          <span>
            {stack.component_metadata
              .map((item) => `${item.id}/${item.version}`)
              .join(" · ")}
          </span>
          <code>{stack.manifest_hash}</code>
        </div>
        <div>
          <span>Declared rig</span>
          <strong>
            {rig.name} · r{rig.revision}
          </strong>
          <code>
            {rig.id} · {context.lidar_sensor_key}
          </code>
          <code>{rig.manifest_hash}</code>
        </div>
        <div>
          <span>
            {context.schema_version === "simulation-control-context/2"
              ? "Saved sampling configuration"
              : "Fixed sensor recipe"}
          </span>
          <strong>
            {context.schema_version === "simulation-control-context/2"
              ? `${context.sensor_configuration_snapshot.name} · r${context.sensor_configuration_snapshot.revision}`
              : context.sensor_profile.id}
          </strong>
          {context.schema_version === "simulation-control-context/2" && (
            <>
              <code>{context.sensor_configuration_snapshot.manifest_hash}</code>
              <span>
                Physical sensor frame: {context.sensor_profile.sensor_frame}
              </span>
              <span>
                Declared position [
                {context.sensor_profile.mount.translation_m.join(", ")}] m
              </span>
              <span>
                Declared quaternion [
                {context.sensor_profile.mount.rotation_xyzw.join(", ")}]
              </span>
            </>
          )}
          <code>{context.sensor_profile.profile_hash}</code>
          {Number.isSafeInteger(nominal) && nominal > 0 && (
            <span>
              {nominal.toLocaleString("en-US")} nominal rays per frame · actual
              returns are recorded per observation
            </span>
          )}
          <span>Saved noise seed {attributes.noise_seed ?? "unavailable"}</span>
          <p>
            The seed records requested configuration. Sensor RNG application is
            unverified; changing the seed may produce identical points.
          </p>
          <span>RH ego · x forward, y left, z up</span>
        </div>
      </div>
      <p className="carla-subtle">
        {ground == null
          ? "Ground unknown → the pinned kernel must retain unknown-stop behavior."
          : `Declared ground ${ground} m · ${String(stack.definition.perception.parameters.ground_reference_source)}`}
      </p>
      <details>
        <summary>Inspect the saved configuration and provenance</summary>
        <p className="carla-subtle">
          These snapshots were stored with this execution. The inspector does
          not resolve current library revisions. Simulation seconds converted to
          ns gain no extra source-clock precision.
        </p>
        <pre>{JSON.stringify(context, null, 2)}</pre>
      </details>
      <div className="carla-context-hash">
        <span>Context SHA-256</span>
        <code>{context.context_hash}</code>
      </div>
    </div>
  );
}
