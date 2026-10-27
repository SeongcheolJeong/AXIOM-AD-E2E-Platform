import { useEffect, useState } from "react";
import type { RunDetail, Artifact } from "../../types";
import { liveArtifact, readLiveArtifact } from "./CarlaLiveData";
import {
  validateSensorMountEvidence,
  type SensorMountEvidence,
  type MountRow,
} from "./SensorMountData";
import { runExecutionId } from "../jobs/RunEvidenceIdentity";

export default function CarlaSensorConfigurationInspector({
  run,
}: {
  run: RunDetail;
}) {
  const [state, setState] = useState<{
    key: string;
    data: SensorMountEvidence | null;
    artifact: Artifact | null;
    bytes: Uint8Array<ArrayBuffer> | null;
    error: string;
  }>({ key: "", data: null, artifact: null, bytes: null, error: "" });
  const [index, setIndex] = useState(-1),
    [attempt, setAttempt] = useState(0);
  const key = JSON.stringify([
    run.id,
    run.provenance.input_hash,
    run.execution_context?.context_hash,
    run.artifacts.map((item) => [item.id, item.sha256, item.size_bytes]),
  ]);
  useEffect(() => {
    const abort = new AbortController();
    setState({ key, data: null, artifact: null, bytes: null, error: "" });
    setIndex(-1);
    (async () => {
      try {
        const artifact = liveArtifact(
          run,
          "carla-sensor-configuration.json",
          "sensor_configuration",
          2 * 1024 * 1024,
        );
        const bytes = await readLiveArtifact(artifact, abort.signal);
        const data = validateSensorMountEvidence(
          JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)),
          run,
        );
        // An observed mount is tied to preserved physics; a failed pre-spawn
        // attempt can correctly have no frame evidence at all.
        if (data.observations.length) {
          const physicsArtifact = liveArtifact(
            run,
            "carla-stack-physics.json",
            "physics_trace",
            4 * 1024 * 1024,
          );
          const raw = await readLiveArtifact(physicsArtifact, abort.signal);
          const physics = JSON.parse(
            new TextDecoder("utf-8", { fatal: true }).decode(raw),
          );
          if (
            physics.execution_id !== runExecutionId(run) ||
            physics.context_hash !== data.context_hash ||
            physics.input_hash !== data.input_hash ||
            !Array.isArray(physics.observations) ||
            physics.observations.length > 81 ||
            data.observations.some(
              (row, i) =>
                physics.observations[i]?.world_frame !== row.world_frame ||
                physics.observations[i]?.source_time_s !== row.source_time_s,
            )
          )
            throw new Error(
              "Sensor mount frames differ from the selected run's preserved physics observations.",
            );
        }
        if (!abort.signal.aborted)
          setState({ key, data, artifact, bytes, error: "" });
      } catch (error) {
        if (!abort.signal.aborted)
          setState({
            key,
            data: null,
            artifact: null,
            bytes: null,
            error: (error as Error).message,
          });
      }
    })();
    return () => abort.abort();
  }, [key, attempt]); // key includes every selected evidence descriptor.
  const ready = state.key === key ? state : null;
  const data = ready?.data;
  const row = data
    ? index < 0
      ? data.startup_mount
      : (data.observations[index] ?? null)
    : null;
  function download() {
    if (!ready?.bytes) return;
    const href = URL.createObjectURL(
      new Blob([ready.bytes], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = href;
    link.download = `${run.id}-carla-sensor-configuration.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  }
  return (
    <section
      className="carla-saved-context"
      aria-label="Mounted sensor configuration evidence"
    >
      <header className="sensor-library-heading">
        <div>
          <h2>Mounted sensor configuration evidence</h2>
          <p>
            Requested sampling → detached blueprint → spawned actor → coherent
            physical pose.
          </p>
        </div>
        {data && (
          <button className="button secondary" onClick={download}>
            Download verified sensor readback
          </button>
        )}
      </header>
      {!ready || (!ready.data && !ready.error) ? (
        <p>Verifying selected run's sensor artifact…</p>
      ) : ready.error ? (
        <div role="alert" className="inline-error">
          Sensor configuration evidence rejected: {ready.error}
          <button
            className="button secondary small"
            onClick={() => setAttempt((n) => n + 1)}
          >
            Retry sensor readback
          </button>
        </div>
      ) : (
        data && (
          <>
            <p className="carla-scope-notice">
              {data.status === "completed"
                ? "All scheduled sensor readbacks retained."
                : `Partial readback · ${data.failure_phase ?? "phase unavailable"}. Unobserved fields remain unknown.`}{" "}
              These checks establish declared configuration consistency, not
              collision clearance, internal RNG application, optical calibration
              or driving quality.
            </p>
            {data.error && <p role="alert">{data.error}</p>}
            <div className="carla-context-grid">
              <div>
                <span>Saved configuration SHA-256</span>
                <code>{data.configuration_manifest_hash}</code>
              </div>
              <div>
                <span>Saved rig SHA-256</span>
                <code>{data.rig_manifest_hash}</code>
              </div>
              <div>
                <span>Actual actor attributes</span>
                <strong>
                  {data.actor_attributes === null
                    ? "Not observed"
                    : data.actor_attributes.qualified
                      ? "Matched typed request"
                      : "Mismatch retained"}
                </strong>
                <span>Sensor frame {data.sensor_frame}</span>
                <span>
                  {data.observations.length} /{" "}
                  {Math.round(run.scenario_snapshot.duration_s / 0.05) + 1}{" "}
                  measurement poses retained
                </span>
              </div>
            </div>
            <div className="sensor-readback-table">
              <table>
                <caption>
                  Requested and independently observed sampling attributes
                </caption>
                <thead>
                  <tr>
                    <th>Attribute</th>
                    <th>Requested text</th>
                    <th>Detached blueprint</th>
                    <th>Spawned actor</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.requested.attributes).map(
                    ([name, value]) => (
                      <tr key={name}>
                        <th>{name.replaceAll("_", " ")}</th>
                        <td>{value}</td>
                        <td>
                          {data.detached_attributes[name]
                            ? `${data.detached_attributes[name].value} (${data.detached_attributes[name].type})`
                            : "Not observed"}
                        </td>
                        <td>
                          {data.actor_attributes?.raw[name] ?? "Not observed"}
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
            <p>
              The seed records requested configuration. Sensor RNG application
              is unverified; changing the seed may produce identical points.
            </p>
            <label>
              Pose observation
              <select
                aria-label="Sensor mount observation"
                value={index}
                onChange={(event) => setIndex(Number(event.target.value))}
              >
                <option value={-1}>Startup boundary</option>
                {data.observations.map((item, i) => (
                  <option key={item.world_frame} value={i}>
                    Measurement {i} · world frame {item.world_frame}
                  </option>
                ))}
              </select>
            </label>
            {row ? (
              <PoseReadback row={row} />
            ) : (
              <p data-testid="sensor-mount-not-observed">
                Startup mount was not observed. No successful physical placement
                is claimed.
              </p>
            )}
            <p>
              Callback shutdown:{" "}
              {data.callback_drain.drained ? "drained" : "not confirmed"} ·
              started {data.callback_drain.started}, finished{" "}
              {data.callback_drain.finished}, active{" "}
              {data.callback_drain.active}. This describes sensor callbacks, not
              world cleanup.
            </p>
            <details>
              <summary>
                Inspect original mount evidence and native conversion
              </summary>
              <pre>
                {JSON.stringify(
                  {
                    spawn: data.spawn,
                    selected_observation: row,
                    callback_drain: data.callback_drain,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            <p className="carla-context-hash">
              Artifact SHA-256 <code>{ready?.artifact?.sha256}</code>
            </p>
          </>
        )
      )}
    </section>
  );
}
function PoseReadback({ row }: { row: MountRow }) {
  const a = row.analysis;
  return (
    <div
      className="carla-pinned-definition"
      data-testid="sensor-mount-readback"
    >
      <strong>
        World frame {row.world_frame} ·{" "}
        {a.qualified ? "Declared pose matched" : "Pose rejected"}
      </strong>
      <span>
        Time {row.source_time_s} s · RPC bracket {row.bracket_before_frame} →{" "}
        {row.bracket_after_frame}
      </span>
      <span>
        Sensor actor {row.sensor_actor_id} · parent{" "}
        {row.parent_actor_id ?? "unavailable"} · ego {row.ego_actor_id}
      </span>
      <span>
        Relative RH position [
        {a.relative_rh_matrix
          .slice(0, 3)
          .map((r) => r[3].toFixed(6))
          .join(", ")}
        ] m
      </span>
      <span>
        Translation residual {a.translation_residual_m.toExponential(4)} m /{" "}
        {a.translation_tolerance_m.toExponential(4)} m allowed
      </span>
      <span>
        Rotation matrix residual {a.rotation_residual.toExponential(4)} /{" "}
        {a.rotation_tolerance.toExponential(4)} allowed
      </span>
      <span>
        Measurement pose:{" "}
        {row.measurement_world_matrix === null
          ? "not observed at startup"
          : `frame ${row.measurement_frame} · ${row.measurement_time_s} s`}
      </span>
      {a.reasons.length > 0 && <p role="alert">{a.reasons.join(" · ")}</p>}
    </div>
  );
}
