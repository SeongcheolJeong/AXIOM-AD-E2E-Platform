import type { SimulationSensorProfileOption } from "./CarlaTypes";

/** Catalog metadata authorizes a choice, never an engine or calibration result. */
export function readSensorProfileCatalog(
  value: unknown,
): SimulationSensorProfileOption[] {
  if (!Array.isArray(value) || value.length > 16)
    throw new Error("Sensor profile catalog has an unsupported shape.");
  const seen = new Set<string>();
  for (const item of value) {
    if (
      !item ||
      ![
        "carla-lidar-ego/1",
        "carla-lidar-ego-dense/1",
        "carla-lidar-ego-near/1",
      ].includes(item.id) ||
      seen.has(item.id) ||
      item.version !== "1" ||
      item.adapter_id !== "carla" ||
      ![item.name, item.description, item.source].every(
        (text) =>
          typeof text === "string" && text.length > 0 && text.length <= 8192,
      ) ||
      !Array.isArray(item.supported_adapter_versions) ||
      item.supported_adapter_versions.length > 32 ||
      !item.supported_adapter_versions.every(
        (version: unknown) =>
          typeof version === "string" && /^\d+\.\d+\.\d+$/.test(version),
      ) ||
      item.seed_policy !== "effective_scenario_seed" ||
      item.max_points !== 20000 ||
      !item.attributes ||
      Array.isArray(item.attributes) ||
      Object.keys(item.attributes).length > 64 ||
      "noise_seed" in item.attributes ||
      !Object.values(item.attributes).every(
        (attribute) =>
          typeof attribute === "string" && attribute.length <= 1024,
      ) ||
      !Number.isSafeInteger(item.nominal_points_per_frame) ||
      item.nominal_points_per_frame <= 0 ||
      item.nominal_points_per_frame > item.max_points ||
      Number(item.attributes.rotation_frequency) !== 20 ||
      Number(item.attributes.points_per_second) / 20 !==
        item.nominal_points_per_frame ||
      JSON.stringify(item.mount?.translation_m) !== "[0,0,2.2]" ||
      JSON.stringify(item.mount?.rotation_xyzw) !== "[0,0,0,1]"
    )
      throw new Error(
        "Sensor profile catalog contains an invalid or unsupported recipe.",
      );
    seen.add(item.id);
  }
  return value;
}
