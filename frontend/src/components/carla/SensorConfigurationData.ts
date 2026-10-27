import type {
  SensorExecutionCapability,
  SensorConfigurationDetail,
} from "./SensorConfigurationTypes";
export function readSensorCapabilities(
  value: unknown,
): SensorExecutionCapability[] {
  if (!Array.isArray(value) || value.length > 32)
    throw new Error("Unsupported sensor capability catalog.");
  const seen = new Set<string>();
  for (const item of value) {
    const key = `${item?.id}@${item?.version}`;
    if (
      !item ||
      item.id !== "carla-raycast-lidar/1" ||
      item.version !== "1" ||
      item.adapter_id !== "carla" ||
      item.status !== "supported" ||
      seen.has(key) ||
      typeof item.name !== "string" ||
      !/^[a-f0-9]{64}$/.test(item.schema_hash) ||
      !Array.isArray(item.supported_adapter_versions) ||
      !Array.isArray(item.supported_context_versions) ||
      !Array.isArray(item.supported_kernel_versions) ||
      !item.supported_context_versions.includes(
        "simulation-control-context/2",
      ) ||
      !item.parameter_schema?.properties ||
      Object.keys(item.parameter_schema.properties).length !== 13 ||
      !item.default_parameters ||
      !Array.isArray(item.limitations)
    )
      throw new Error(
        "Sensor capability metadata is incomplete or unsupported.",
      );
    seen.add(key);
  }
  return value;
}
export function validConfiguration(
  value: SensorConfigurationDetail | null,
): value is SensorConfigurationDetail {
  return (
    !!value &&
    value.schema_version === "simulation-sensor-configuration/1" &&
    typeof value.id === "string" &&
    Number.isSafeInteger(value.revision) &&
    value.revision > 0 &&
    value.adapter_id === "carla" &&
    value.capability_id === "carla-raycast-lidar/1" &&
    value.capability_version === "1" &&
    [
      value.manifest_hash,
      value.definition_hash,
      value.capability_schema_hash,
    ].every((hash) => /^[a-f0-9]{64}$/.test(hash)) &&
    !!value.parameters &&
    Object.keys(value.parameters).length === 13 &&
    Object.values(value.parameters).every(
      (number) => typeof number === "number" && Number.isFinite(number),
    )
  );
}
