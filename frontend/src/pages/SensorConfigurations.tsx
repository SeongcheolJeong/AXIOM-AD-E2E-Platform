import { useState } from "react";
import { PageHeading, Loading, ErrorState } from "../components/ui";
import { useSensorResource } from "../components/sensors/SensorData";
import SensorRigEditor from "../components/sensors/SensorRigEditor";
import type { RigDetail } from "../components/sensors/SensorTypes";
import { readSensorCapabilities } from "../components/carla/SensorConfigurationData";
import SensorConfigurationEditor from "../components/carla/SensorConfigurationEditor";
import type {
  SensorConfigurationDetail,
  SensorExecutionCapability,
} from "../components/carla/SensorConfigurationTypes";
import { readLiveArtifact } from "../components/carla/CarlaLiveData";
import "../sensors.css";
import "../components/carla/carla.css";

export default function SensorConfigurations() {
  const capabilities = useSensorResource<SensorExecutionCapability[]>(
    "/simulation-sensor-capabilities?adapter_id=carla",
  );
  const configs = useSensorResource<SensorConfigurationDetail[]>(
    "/simulation-sensor-configurations",
  );
  const rigs = useSensorResource<RigDetail[]>("/sensor-rigs");
  const [id, setId] = useState(""),
    [revision, setRevision] = useState(0);
  const [rigId, setRigId] = useState(""),
    [rigRevision, setRigRevision] = useState(0);
  const history = useSensorResource<SensorConfigurationDetail[]>(
    id
      ? `/simulation-sensor-configurations/${encodeURIComponent(id)}/revisions`
      : null,
  );
  const rigHistory = useSensorResource<RigDetail[]>(
    rigId ? `/sensor-rigs/${encodeURIComponent(rigId)}/revisions` : null,
  );
  const selected =
    history.data?.find(
      (item) => item.id === id && item.revision === revision,
    ) ?? null;
  const rig =
    rigHistory.data?.find(
      (item) => item.id === rigId && item.revision === rigRevision,
    ) ?? null;
  const [editor, setEditor] = useState<
    SensorConfigurationDetail | "new" | null
  >(null);
  const [rigEditor, setRigEditor] = useState<RigDetail | "new" | null>(null);
  const [downloadError, setDownloadError] = useState("");
  let supported: SensorExecutionCapability[] = [];
  let capabilityError = capabilities.error;
  if (capabilities.data !== null) {
    try {
      supported = readSensorCapabilities(capabilities.data);
    } catch (reason) {
      capabilityError = (reason as Error).message;
    }
  }
  const canRevise =
    selected &&
    supported.some(
      (item) =>
        item.id === selected.capability_id &&
        item.version === selected.capability_version &&
        item.schema_hash === selected.capability_schema_hash,
    );
  async function download(
    item: SensorConfigurationDetail | RigDetail,
    collection: string,
  ) {
    setDownloadError("");
    try {
      const url = new URL(item.download_url, window.location.origin);
      if (
        url.origin !== window.location.origin ||
        url.username ||
        url.password ||
        url.hash ||
        url.pathname !==
          `/api/v1/${collection}/${encodeURIComponent(item.id)}/export` ||
        url.search !== `?revision=${item.revision}`
      )
        throw new Error(
          "Export URL differs from the selected immutable revision.",
        );
      if (
        !Number.isSafeInteger(item.manifest_size_bytes) ||
        item.manifest_size_bytes <= 0 ||
        item.manifest_size_bytes > 8 * 1024 * 1024 ||
        !/^[a-f0-9]{64}$/.test(item.manifest_hash)
      )
        throw new Error("Invalid manifest digest or size.");
      const bytes = await readLiveArtifact(
        {
          download_url: item.download_url,
          size_bytes: item.manifest_size_bytes,
          sha256: item.manifest_hash,
        },
        new AbortController().signal,
      );
      const blob = URL.createObjectURL(
        new Blob([bytes], { type: "application/json" }),
      );
      const link = document.createElement("a");
      link.href = blob;
      link.download = `${item.id}-r${item.revision}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(blob), 1000);
    } catch (error) {
      setDownloadError((error as Error).message);
    }
  }
  function refresh() {
    configs.reload();
    capabilities.reload();
    history.reload();
    rigs.reload();
    rigHistory.reload();
  }
  return (
    <div className="sensor-configurations-page">
      <PageHeading
        eyebrow="SAMPLING AND PHYSICAL PLACEMENT"
        title="Simulation sensors"
        description="Save complete LiDAR sampling recipes and separately versioned rig geometry, then select exact revisions for an execution."
        actions={
          <button className="button secondary" onClick={refresh}>
            Refresh sensor library
          </button>
        }
      />
      <p className="carla-scope-notice">
        Configuration registration is not engine availability or sensor-quality
        acceptance. A new physical placement must be checked in the simulator;
        original returns remain intact.
      </p>
      {capabilityError && (
        <ErrorState
          message={`Sensor capability catalog unavailable: ${capabilityError}`}
          retry={capabilities.reload}
        />
      )}
      {configs.error && (
        <ErrorState message={configs.error} retry={configs.reload} />
      )}
      <section
        className="carla-saved-context"
        aria-label="Sensor configuration library"
      >
        <header className="sensor-library-heading">
          <h2>Sampling configurations</h2>
          <button
            className="button primary"
            disabled={
              !supported.length || capabilities.loading || !!capabilityError
            }
            onClick={() => setEditor("new")}
          >
            New sensor configuration
          </button>
        </header>
        {configs.loading && <Loading label="Loading sensor configurations…" />}
        <div className="carla-form-grid">
          <label>
            Saved configuration
            <select
              aria-label="Saved sensor configuration"
              value={id}
              onChange={(event) => {
                setId(event.target.value);
                setRevision(0);
                setDownloadError("");
              }}
            >
              <option value="">Choose a configuration</option>
              {id && !configs.data?.some((item) => item.id === id) && (
                <option value={id}>Selected {id} · catalog loading</option>
              )}
              {configs.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · latest r{item.revision}
                </option>
              ))}
            </select>
          </label>
          <label>
            Exact configuration revision
            <select
              aria-label="Sensor configuration revision"
              value={revision || ""}
              onChange={(event) => {
                setRevision(Number(event.target.value));
                setDownloadError("");
              }}
            >
              <option value="">Choose a revision</option>
              {history.data
                ?.filter((item) => item.id === id)
                .map((item) => (
                  <option key={item.revision} value={item.revision}>
                    r{item.revision} · {item.note}
                  </option>
                ))}
            </select>
          </label>
        </div>
        {history.error && (
          <ErrorState message={history.error} retry={history.reload} />
        )}
        {selected && (
          <article data-testid="sensor-configuration-detail">
            <h3>
              {selected.name} · fixed r{selected.revision}
            </h3>
            <p>{selected.description}</p>
            <p>{selected.note}</p>
            <p>
              {selected.capability_id} · {selected.capability_version}
            </p>
            <code>{selected.manifest_hash}</code>
            <pre>{JSON.stringify(selected.parameters, null, 2)}</pre>
            <div className="sensor-library-heading">
              <button
                className="button secondary"
                disabled={!canRevise}
                onClick={() => setEditor(selected)}
              >
                Create configuration revision
              </button>
              <button
                className="button secondary"
                onClick={() =>
                  download(selected, "simulation-sensor-configurations")
                }
              >
                Download verified configuration
              </button>
            </div>
            <p>
              A new revision never replaces a saved run's inputs. Conflicting
              edits are rejected against r{selected.revision}.
            </p>
          </article>
        )}
      </section>
      <section className="carla-saved-context" aria-label="Sensor rig library">
        <header className="sensor-library-heading">
          <h2>Physical sensor rigs</h2>
          <button
            className="button primary"
            onClick={() => setRigEditor("new")}
          >
            New sensor rig
          </button>
        </header>
        <p>
          Physical translation and rotation come only from the selected rig
          revision. Declare the source and limits of the geometry; saving it
          does not calibrate a sensor.
        </p>
        <div className="carla-form-grid">
          <label>
            Saved rig
            <select
              aria-label="Saved sensor rig"
              value={rigId}
              onChange={(event) => {
                setRigId(event.target.value);
                setRigRevision(0);
              }}
            >
              <option value="">Choose a rig</option>
              {rigId && !rigs.data?.some((item) => item.id === rigId) && (
                <option value={rigId}>
                  Selected {rigId} · catalog loading
                </option>
              )}
              {rigs.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · latest r{item.revision}
                </option>
              ))}
            </select>
          </label>
          <label>
            Exact rig revision
            <select
              aria-label="Sensor library rig revision"
              value={rigRevision || ""}
              onChange={(event) => setRigRevision(Number(event.target.value))}
            >
              <option value="">Choose a revision</option>
              {rigHistory.data
                ?.filter((item) => item.id === rigId)
                .map((item) => (
                  <option key={item.revision} value={item.revision}>
                    r{item.revision} · {item.note}
                  </option>
                ))}
            </select>
          </label>
        </div>
        {(rigs.error || rigHistory.error) && (
          <ErrorState
            message={rigs.error || rigHistory.error}
            retry={() => {
              rigs.reload();
              rigHistory.reload();
            }}
          />
        )}
        {rig && (
          <article data-testid="sensor-library-rig-detail">
            <h3>
              {rig.name} · fixed r{rig.revision}
            </h3>
            <p>
              {rig.reference_frame} · {rig.coordinate_convention} ·{" "}
              {rig.calibration_status}
            </p>
            <code>{rig.manifest_hash}</code>
            <pre>{JSON.stringify(rig.sensors, null, 2)}</pre>
            <div className="sensor-library-heading">
              <button
                className="button secondary"
                onClick={() => setRigEditor(rig)}
              >
                Create rig revision
              </button>
              <button
                className="button secondary"
                onClick={() => download(rig, "sensor-rigs")}
              >
                Download verified rig
              </button>
            </div>
          </article>
        )}
      </section>
      {downloadError && (
        <p className="inline-error" role="alert">
          {downloadError}
        </p>
      )}
      {editor && (
        <SensorConfigurationEditor
          configuration={editor === "new" ? undefined : editor}
          capabilities={supported}
          onClose={() => setEditor(null)}
          onSaved={(item) => {
            setEditor(null);
            setId(item.id);
            setRevision(item.revision);
            configs.reload();
            history.reload();
          }}
        />
      )}
      {rigEditor && (
        <SensorRigEditor
          rig={rigEditor === "new" ? undefined : rigEditor}
          onClose={() => setRigEditor(null)}
          onSaved={(item) => {
            setRigEditor(null);
            setRigId(item.id);
            setRigRevision(item.revision);
            rigs.reload();
            rigHistory.reload();
          }}
        />
      )}
    </div>
  );
}
