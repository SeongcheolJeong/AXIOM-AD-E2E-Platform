import type { ParameterSchema } from "../stacks/StackTypes";
export interface SensorConfigurationCreate {
  name: string;
  description: string;
  note: string;
  adapter_id: string;
  capability_id: string;
  capability_version: string;
  parameters: Record<string, number | string>;
}
export interface SensorConfigurationDetail extends SensorConfigurationCreate {
  schema_version: "simulation-sensor-configuration/1";
  id: string;
  revision: number;
  created_at: string;
  definition_hash: string;
  capability_schema_hash: string;
  manifest_hash: string;
  manifest_size_bytes: number;
  download_url: string;
}
export interface SensorExecutionCapability {
  id: string;
  version: string;
  name: string;
  status: "supported";
  supported_context_versions: string[];
  supported_kernel_versions: string[];
  mount_rules: Record<string, unknown>;
  limits: Record<string, unknown>;
  budget_policy_version: string;
  calibration_status: "uncalibrated_engine_model";
  adapter_id: string;
  supported_adapter_versions: string[];
  parameter_schema: ParameterSchema;
  default_parameters: Record<string, number | string>;
  schema_hash: string;
  limitations: string[];
}
