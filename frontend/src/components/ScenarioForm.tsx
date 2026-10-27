import { useState, type FormEvent } from "react";
import { ArrowRight, LoaderCircle } from "lucide-react";
import { api } from "../api";
import {
  categories,
  categoryLabels,
  type Scenario,
  type ScenarioCreate,
} from "../types";
import { Modal } from "./ui";
const defaults: ScenarioCreate = {
  name: "",
  description: "",
  category: "cut_in",
  map_name: "Reference highway",
  weather: "clear",
  time_of_day: "day",
  tags: [],
  parameters: {
    ego_speed_mps: 20,
    target_speed_mps: 14,
    initial_gap_m: 24,
    trigger_time_s: 3,
    friction: 0.85,
    response_delay_s: 0.6,
  },
  duration_s: 12,
  seed: 42,
};
const parameterFields = [
  ["ego_speed_mps", "Ego speed", "m/s", 0, 55, 0.5],
  ["target_speed_mps", "Actor speed", "m/s", 0, 55, 0.5],
  ["initial_gap_m", "Initial gap", "m", 0, 500, 1],
  ["trigger_time_s", "Trigger time", "s", 0, 119, 0.1],
  ["friction", "Road friction", "μ", 0.1, 1.2, 0.05],
  ["response_delay_s", "Response delay", "s", 0, 10, 0.05],
] as const;
export default function ScenarioForm({
  scenario,
  onClose,
  onSaved,
}: {
  scenario?: Scenario;
  onClose: () => void;
  onSaved: (scenario: Scenario) => void;
}) {
  const [form, setForm] = useState<ScenarioCreate>(
      scenario
        ? {
            name: scenario.name,
            description: scenario.description,
            category: scenario.category,
            map_name: scenario.map_name,
            weather: scenario.weather,
            time_of_day: scenario.time_of_day,
            tags: scenario.tags,
            parameters: { ...scenario.parameters },
            duration_s: scenario.duration_s,
            seed: scenario.seed,
          }
        : defaults,
    ),
    [tags, setTags] = useState(scenario?.tags.join(", ") ?? ""),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const field = <K extends keyof ScenarioCreate>(
    key: K,
    value: ScenarioCreate[K],
  ) => setForm((previous) => ({ ...previous, [key]: value }));
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (form.parameters.trigger_time_s >= form.duration_s) {
      setError("Trigger time must be earlier than the scenario duration.");
      return;
    }
    const parsedTags = tags
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean);
    if (parsedTags.length > 32 || parsedTags.some((tag) => tag.length > 64)) {
      setError(
        "Use up to 32 tags, each containing no more than 64 characters.",
      );
      return;
    }
    setSaving(true);
    try {
      const result = await api<Scenario>(
        scenario ? `/scenarios/${scenario.id}` : "/scenarios",
        {
          method: scenario ? "PATCH" : "POST",
          body: JSON.stringify({
            ...form,
            tags: parsedTags,
          }),
        },
      );
      onSaved(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal
      title={scenario ? "Edit scenario" : "Create a scenario"}
      subtitle="Define a reproducible test. Every change creates a new revision."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <div className="modal-body">
          <label>
            Scenario name
            <input
              required
              autoFocus
              maxLength={180}
              value={form.name}
              onChange={(e) => field("name", e.target.value)}
              placeholder="e.g. Highway cut-in · wet surface"
            />
          </label>
          <label>
            Description
            <textarea
              rows={2}
              maxLength={4000}
              value={form.description}
              onChange={(e) => field("description", e.target.value)}
              placeholder="What behavior should this scenario exercise?"
            />
          </label>
          <div className="form-grid">
            <label>
              Category
              <select
                value={form.category}
                onChange={(e) =>
                  field(
                    "category",
                    e.target.value as ScenarioCreate["category"],
                  )
                }
              >
                {categories.map((value) => (
                  <option key={value} value={value}>
                    {categoryLabels[value]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Map / environment
              <input
                required
                maxLength={180}
                value={form.map_name}
                onChange={(e) => field("map_name", e.target.value)}
              />
            </label>
            <label>
              Weather
              <select
                value={form.weather}
                onChange={(e) =>
                  field("weather", e.target.value as ScenarioCreate["weather"])
                }
              >
                <option value="clear">Clear</option>
                <option value="rain">Rain</option>
                <option value="fog">Fog</option>
              </select>
            </label>
            <label>
              Time of day
              <select
                value={form.time_of_day}
                onChange={(e) =>
                  field(
                    "time_of_day",
                    e.target.value as ScenarioCreate["time_of_day"],
                  )
                }
              >
                <option value="day">Day</option>
                <option value="night">Night</option>
                <option value="dusk">Dusk</option>
              </select>
            </label>
          </div>
          <div className="form-section-title">
            Simulation parameters <span>SI units</span>
          </div>
          <div className="form-grid">
            {parameterFields.map(([key, label, unit, min, max]) => (
              <label key={key}>
                {label}
                <div className="unit-input">
                  <input
                    required
                    type="number"
                    min={min}
                    max={max}
                    step="any"
                    value={form.parameters[key]}
                    onChange={(e) =>
                      field("parameters", {
                        ...form.parameters,
                        [key]: Number(e.target.value),
                      })
                    }
                  />
                  <span>{unit}</span>
                </div>
              </label>
            ))}
            <label>
              Duration
              <div className="unit-input">
                <input
                  required
                  type="number"
                  min={2}
                  max={120}
                  step="any"
                  value={form.duration_s}
                  onChange={(e) => field("duration_s", Number(e.target.value))}
                />
                <span>s</span>
              </div>
            </label>
            <label>
              Random seed
              <input
                required
                type="number"
                min={0}
                max={2147483647}
                step={1}
                value={form.seed}
                onChange={(e) => field("seed", Number(e.target.value))}
              />
            </label>
          </div>
          <label>
            Tags
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="regression, highway, planning"
            />
          </label>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
        </div>
        <footer>
          <button type="button" className="button secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="button primary" disabled={saving}>
            {saving ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <ArrowRight size={16} />
            )}{" "}
            {saving
              ? "Saving…"
              : scenario
                ? "Save revision"
                : "Create scenario"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
