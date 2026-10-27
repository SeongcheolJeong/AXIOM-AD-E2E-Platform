import { authHeaders } from "../../api";
import { verifiedArtifactBytes } from "../../pages/CarlaCameraFrame";
import type { CarlaCameraRecord } from "../../pages/CarlaCameraFrame";
import {
  createArtifactRangeReader,
  openCarlaStoredZip,
} from "../../pages/carla-zip";
import { verifiedReceipt } from "../object-scene/load-object-scene";
import type { ObjectExecution } from "../object-scene/object-scene";
import { record, sameValue } from "../object-scene/object-scene";
import { cityEvidenceContext, parseCityScene, type HilSide } from "./city-scene";
export async function loadCityScene(
  execution: ObjectExecution,
  signal: AbortSignal,
  hilSide?: HilSide,
) {
  const prefix = hilSide ? `${hilSide}-` : "";
  const artifacts = execution.artifacts.filter(
    (item) => item.name === `${prefix}city-scene.json`,
  );
  if (artifacts.length !== 1 || artifacts[0].media_type !== "application/json")
    throw new Error("봉인된 city-scene.json 근거가 없거나 모호합니다.");
  const [receipt, bytes] = await Promise.all([
    verifiedReceipt(execution, signal, 32 * 1024 * 1024),
    verifiedArtifactBytes(execution.id, artifacts[0], 64 * 1024 * 1024, signal),
  ]);
  const scene = parseCityScene(
    receipt,
    JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)),
    execution,
    hilSide,
  );
  const archives = new Map<
      string,
      Awaited<ReturnType<typeof openCarlaStoredZip>>
    >(),
    descriptors = new Map<string, (typeof execution.artifacts)[number]>();
  for (const name of new Set(
    scene.camera_records.map((item) => item.archive),
  )) {
    const matches = execution.artifacts.filter(
      (artifact) => artifact.name === `${prefix}${name}`,
    );
    if (matches.length !== 1)
      throw new Error("보존 카메라 archive가 누락되었거나 모호합니다.");
    const descriptor = matches[0],
      archive = await openCarlaStoredZip(
        descriptor.size_bytes,
        createArtifactRangeReader(execution.id, descriptor, authHeaders()),
        signal,
        "city-camera",
      );
    archive.assertClosure?.(
      scene.camera_records
        .filter((item) => item.archive === name)
        .map((item) => ({
          name: item.member,
          size_bytes: item.size_bytes,
          sha256: item.sha256,
        })),
    );
    archives.set(name, archive);
    descriptors.set(name, descriptor);
  }
  const cameraRecords: CarlaCameraRecord[] = scene.camera_records.map(
    (item) => ({
      index: item.index,
      frame: item.frame,
      timestampNs: String(Math.round(item.simulation_time_s * 1e9)),
      timestampSeconds: item.simulation_time_s,
      width: item.width,
      height: item.height,
      key: item.sensor_id,
      artifact: descriptors.get(item.archive)!,
      encoding: "bgra8",
      cacheKey: `${execution.id}:${prefix}${item.archive}:${item.member}:${item.sha256}`,
      archive: archives.get(item.archive),
      member: {
        name: item.member,
        size_bytes: item.size_bytes,
        sha256: item.sha256,
      },
    }),
  );
  const context = cityEvidenceContext(receipt, execution, hilSide),
    summary = record(context.summary, "city summary"),
    evaluation = record(summary.evaluation, "city evaluation"),
    scenario = context.input.scenario;
  if (
    summary.schema_version !== "carla-city-result/1" ||
    summary.scene_sha256 !== artifacts[0].sha256 ||
    evaluation.schema_version !== "carla-city-evaluation/1" ||
    evaluation.physical_sensor_validation !== false ||
    !sameValue(evaluation.criteria, record(scenario, "scenario").acceptance)
  )
    throw new Error("고정 기준과 봉인된 평가 출처가 일치하지 않습니다.");
  const checks = evaluation.checks;
  if (
    !Array.isArray(checks) ||
    checks.some(
      (item) =>
        !item ||
        typeof item !== "object" ||
        typeof item.metric !== "string" ||
        !["pass", "fail", "unevaluated"].includes(String(item.status)),
    )
  )
    throw new Error("보존된 도시 판정의 지표 계약이 유효하지 않습니다.");
  return {
    scene,
    cameraRecords,
    evaluation: {
      verdict: String(evaluation.verdict),
      classification: String(evaluation.classification),
      checks: checks as {
        metric: string;
        value: number | null;
        threshold: number;
        operator: string;
        status: string;
      }[],
    },
  };
}
