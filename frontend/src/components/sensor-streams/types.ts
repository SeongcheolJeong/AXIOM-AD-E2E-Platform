import type { CarlaStoredZip, CarlaZipArtifact, CarlaZipMember } from "../../pages/carla-zip";

export type Matrix = number[][];
export type SensorKind = "rgb" | "lidar" | "radar";
export type SensorStreamExecution = { id: string; experiment_id: string; project_id: string; experiment_kind: string; status: string; adapter_id: string; adapter_version: string; input_hash: string; manifest_hash: string | null; download_url: string | null; fidelity: string | null; summary: Record<string, unknown> | null; artifacts: CarlaZipArtifact[] };
export type CapturePin = { driving?: boolean; input: Record<string, unknown>; summary: Record<string, unknown>; rigs: Record<string, Record<string, unknown>>; rigHashes: Record<string, string>; stages: string[]; artifacts: CarlaZipArtifact[]; seed: number };
export type CaptureSensor = { key: string; kind: SensorKind; actorId: number; blueprint: string; mountRh: Matrix; parameters: Record<string, unknown>; fov?: number; intrinsics?: Record<string, unknown> };
export type CaptureRecord = { index: number; key: string; kind: SensorKind; frame: number; timestampNs: string; timestampSeconds: number; encoding: string; member: CarlaZipMember; width: number; height: number; pointCount: number; sensorWorld: Matrix; egoWorld: Matrix; relativeRh: Matrix; cacheKey: string };
export type CaptureMotionTick = { inputFrame: number; speedMps: number; distanceM: number; egoWorld: Matrix; requestedControl: Record<string, number>; observedControl: Record<string, number> };
export type CaptureMotion = { controller: Record<string, unknown>; route: Record<string, unknown>; distanceM: number; maximumSpeedMps: number; ticks: CaptureMotionTick[] };
export type CaptureTick = { index: number; frame: number; timestampNs: string; timestampSeconds: number; records: CaptureRecord[] };
export type CaptureEvidence = { motion?: CaptureMotion; stage: string; externalExecutionId: string; configurationHash: string; rigRevision: number; seed: number; map: string; ticks: CaptureTick[]; sensors: CaptureSensor[]; members: CarlaZipMember[]; manifest: CarlaZipArtifact; archiveArtifact: CarlaZipArtifact };
export type LoadedCapture = CaptureEvidence & { archive: CarlaStoredZip };
export type PointFrame = { kind: "lidar" | "radar"; count: number; raw: Float32Array; positions: Float32Array; values: Float32Array; warnings: string[]; valueMin: number | null; valueMax: number | null };
