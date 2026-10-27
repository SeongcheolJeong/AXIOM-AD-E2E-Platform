import { useRef, useState, type FormEvent } from "react";
import { api } from "../../api";
import { Modal } from "../ui";
import type {
  SensorConfigurationCreate,
  SensorConfigurationDetail,
  SensorExecutionCapability,
} from "./SensorConfigurationTypes";

const labels: Record<string, string> = {
  channels: "Vertical channels",
  range: "Maximum range (m)",
  points_per_second: "Rays per second",
  rotation_frequency: "Scan frequency (Hz)",
  sensor_tick: "Sensor interval (s)",
  lower_fov: "Lowest elevation (°)",
  upper_fov: "Highest elevation (°)",
  horizontal_fov: "Horizontal field of view (°)",
  atmosphere_attenuation_rate: "Atmospheric attenuation",
  dropoff_general_rate: "General drop probability",
  dropoff_intensity_limit: "Drop intensity limit",
  dropoff_zero_intensity: "Zero-intensity drop probability",
  noise_stddev: "Range noise standard deviation (m)",
  seed_policy: "Seed source",
};
export default function SensorConfigurationEditor({
  configuration,
  capabilities,
  onSaved,
  onClose,
}: {
  configuration?: SensorConfigurationDetail;
  capabilities: SensorExecutionCapability[];
  onSaved: (value: SensorConfigurationDetail) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(
    configuration
      ? `${configuration.capability_id}@${configuration.capability_version}`
      : capabilities[0]
        ? `${capabilities[0].id}@${capabilities[0].version}`
        : "",
  );
  const capability = capabilities.find(
    (item) => `${item.id}@${item.version}` === selected,
  );
  const draft = (values: Record<string, number | string>) =>
    Object.fromEntries(
      Object.entries(values).map(([key, value]) => [key, String(value)]),
    );
  const [parameters, setParameters] = useState<Record<string, string>>(() =>
    draft(configuration?.parameters ?? capability?.default_parameters ?? {}),
  );
  const [name, setName] = useState(configuration?.name ?? "");
  const [description, setDescription] = useState(
    configuration?.description ?? "",
  );
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const attempts = useRef(new Map<string, string>());
  const [guard] = useState(configuration?.revision ?? 1);
  const properties = capability?.parameter_schema.properties ?? {};
  function normalized() {
    const values: Record<string, number | string> = {};
    for (const [key, shape] of Object.entries(properties)) {
      const value = parameters[key];
      if (value === undefined || !value.trim())
        throw new Error(`${labels[key] ?? key} is required.`);
      if (shape.type === "integer" || shape.type === "number") {
        const number = Number(value);
        if (
          !Number.isFinite(number) ||
          (shape.type === "integer" && !Number.isSafeInteger(number))
        )
          throw new Error(
            `${labels[key] ?? key} must be a finite ${shape.type}.`,
          );
        values[key] = number;
      } else if (shape.type === "string") values[key] = value;
      else
        throw new Error(
          `Unsupported form field ${key}; this capability cannot be authored by this client.`,
        );
    }
    return values;
  }
  let preview: Record<string, number | string> | null = null;
  try {
    preview = normalized();
  } catch {
    /* Form explains invalid fields on submission. */
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!capability) return;
    setBusy(true);
    setError("");
    try {
      const body: SensorConfigurationCreate & { expected_revision?: number } = {
        name: name.trim(),
        description: description.trim(),
        note: note.trim(),
        adapter_id: capability.adapter_id,
        capability_id: capability.id,
        capability_version: capability.version,
        parameters: normalized(),
        ...(configuration ? { expected_revision: guard } : {}),
      };
      const encoded = JSON.stringify(body);
      let key = attempts.current.get(encoded);
      if (!key) {
        key = crypto.randomUUID();
        attempts.current.set(encoded, key);
      }
      const saved = await api<SensorConfigurationDetail>(
        configuration
          ? `/simulation-sensor-configurations/${encodeURIComponent(configuration.id)}/revisions`
          : "/simulation-sensor-configurations",
        {
          method: "POST",
          body: encoded,
          headers: { "Idempotency-Key": key },
        },
      );
      if (
        !saved.id ||
        saved.revision < 1 ||
        (configuration &&
          (saved.id !== configuration.id || saved.revision !== guard + 1))
      )
        throw new Error(
          "Saved configuration identity differs from the requested revision.",
        );
      onSaved(saved);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={
        configuration
          ? "Create sensor configuration revision"
          : "Create sensor configuration"
      }
      subtitle="Pin a complete sampling recipe. Physical placement belongs to a separately selected rig."
      onClose={() => {
        if (!busy) onClose();
      }}
      className="sensor-modal"
    >
      <form className="sensor-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <label>
            Configuration name
            <input
              autoFocus
              required
              maxLength={180}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            Description
            <textarea
              maxLength={4000}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <label>
            Sensor implementation
            <select
              aria-label="Sensor implementation"
              value={selected}
              onChange={(event) => {
                const next = capabilities.find(
                  (item) => `${item.id}@${item.version}` === event.target.value,
                );
                setSelected(event.target.value);
                setParameters(draft(next?.default_parameters ?? {}));
              }}
            >
              <option value="" disabled>
                Choose an installed capability
              </option>
              {capabilities.map((item) => (
                <option
                  key={`${item.id}@${item.version}`}
                  value={`${item.id}@${item.version}`}
                >
                  {item.name} · {item.version}
                </option>
              ))}
            </select>
          </label>
          <p>{capability?.limitations.join(" ")}</p>
          <div className="sensor-form-grid two">
            {Object.entries(properties).map(([key, shape]) => (
              <label key={key}>
                {labels[key] ?? shape.title ?? key}
                {shape.enum ? (
                  <select
                    aria-label={labels[key] ?? key}
                    value={parameters[key] ?? ""}
                    onChange={(event) =>
                      setParameters({
                        ...parameters,
                        [key]: event.target.value,
                      })
                    }
                  >
                    {shape.enum.map((item) => (
                      <option key={String(item)} value={String(item)}>
                        {String(item)}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    aria-label={labels[key] ?? key}
                    required
                    type={shape.type === "string" ? "text" : "number"}
                    step={shape.type === "integer" ? "1" : "any"}
                    min={shape.minimum}
                    max={shape.maximum}
                    value={parameters[key] ?? ""}
                    onChange={(event) =>
                      setParameters({
                        ...parameters,
                        [key]: event.target.value,
                      })
                    }
                  />
                )}
                {shape.description && <small>{shape.description}</small>}
              </label>
            ))}
          </div>
          <p className="sensor-form-help">
            Nominal rays describe sampling attempts, not measured returns or
            free space. The API also checks the full execution evidence budget.
            No source points are deleted.
          </p>
          <p>
            The seed records requested configuration. Sensor RNG application is
            unverified; changing the seed may produce identical points.
          </p>
          <label>
            Revision note
            <textarea
              required
              maxLength={4000}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Explain the intended experiment and remaining limitations"
            />
          </label>
          {configuration && (
            <p>
              Expected current revision: r{guard}. Conflicts retain this draft;
              no revision is silently substituted.
            </p>
          )}
          <details>
            <summary>Review all sampling values before saving</summary>
            <pre>
              {preview
                ? JSON.stringify(preview, null, 2)
                : "Complete the required values."}
            </pre>
          </details>
          {error && (
            <p role="alert" className="inline-error">
              {error} Uncertain responses can be retried with this unchanged
              draft and the same key.
            </p>
          )}
        </fieldset>
        <footer>
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="button primary"
            disabled={
              busy || !capability || Object.keys(properties).length === 0
            }
          >
            {busy
              ? "Saving…"
              : configuration
                ? "Save configuration revision"
                : "Save sensor configuration"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
