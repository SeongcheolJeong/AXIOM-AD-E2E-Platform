import type { CatalogRef } from "../city-sim/city-contract";

export type SourceKind = "opendrive" | "ngii_shp";
export type SourceRights = { license: string; source_url: string; redistribution: "unknown" | "permitted" | "restricted"; notes: string };
export type SourceRecord = CatalogRef & {
  project_id: string; name: string; source_kind: SourceKind; filename: string;
  source_sha256: string; size_bytes: number; rights: SourceRights;
  attachments?: { filename: string; role: string; sha256: string; size_bytes: number }[];
  metadata: Record<string, unknown>; editable: false; native_ready: boolean; created_at: string;
};
export type MapDiagnostic = { severity: "error" | "warning" | "info"; code: string; message: string; [key: string]: unknown };
export type MapConversion = CatalogRef & {
  source_ref: CatalogRef; ready: boolean; diagnostics: MapDiagnostic[];
  coordinate_transform: unknown; inference_flags: unknown; candidate_sha256?: string;
  converter: unknown; [key: string]: unknown;
};
export type MapExecution = {
  id: string; project_id?: string; status: string; error?: string | null;
  adapter_id?: string; adapter_version?: string; fidelity?: string;
  input_hash?: string; outcome_uncertain?: boolean; cancellation_supported?: boolean;
  summary?: Record<string, unknown>; artifacts?: { name: string; sha256?: string; download_url?: string }[];
  [key: string]: unknown;
};
export type SourceDetail = SourceRecord & { conversions: MapConversion[]; executions: MapExecution[]; published_maps: CatalogRef[] };
export type SourceCapabilities = {
  schema_version: string; source_kinds: SourceKind[]; max_upload_bytes: number; editable: false;
  conversion: Record<SourceKind, { available: boolean; reason?: string }>;
  qualification: { adapter_id: string; configured: boolean; readiness: string; scope: string };
  limits: Record<string, unknown>;
};
export const sourceKindNames: Record<SourceKind, string> = { opendrive: "OpenDRIVE · XODR", ngii_shp: "NGII 정밀도로지도 · SHP ZIP" };
export const executionStatusNames: Record<string, string> = {
  queued: "대기 중", pending: "접수됨", running: "실제 엔진 확인 중", completed: "실행 완료",
  failed: "실패", interrupted: "중단됨", cancelled: "취소됨", cancelling: "취소·정리 중", cancel_requested: "취소 요청됨",
};
export function sourceRef(source: CatalogRef): CatalogRef { return { id: source.id, revision: source.revision, sha256: source.sha256 }; }
export function sameSourceRef(a: CatalogRef | null | undefined, b: CatalogRef | null | undefined): boolean {
  return !!a && !!b && a.id === b.id && a.revision === b.revision && a.sha256 === b.sha256;
}
export function isPendingMapExecution(execution: MapExecution): boolean {
  return ["queued", "pending", "running", "cancelling", "cancel_requested"].includes(execution.status);
}
export function hasMatchingNativeQualification(execution: MapExecution | null | undefined, source: CatalogRef | null | undefined, conversion: CatalogRef | null | undefined): boolean {
  if (!execution || !source || !conversion || execution.status !== "completed" || execution.outcome_uncertain !== false || execution.fidelity !== "native_carla_external_map_load_readback") return false;
  const summary = execution.summary, qualification = summary?.qualification as { ready?: boolean; scope?: string } | undefined;
  return summary?.native_ready === true && summary?.driving_evaluated === false && summary?.physical_validation === false
    && qualification?.ready === true && qualification.scope === "native_map_load_readback_not_driving"
    && sameSourceRef(summary?.source_ref as CatalogRef | undefined, source)
    && sameSourceRef(summary?.conversion_ref as CatalogRef | undefined, conversion);
}
export function mapSourceRoute(project: string, source?: CatalogRef, conversion?: CatalogRef, execution?: string): string {
  const query = new URLSearchParams({ project, mode: "map-sources" });
  if (source) { query.set("source", source.id); query.set("revision", String(source.revision)); query.set("source_hash", source.sha256); }
  if (conversion) { query.set("conversion", conversion.id); query.set("conversion_revision", String(conversion.revision)); query.set("conversion_hash", conversion.sha256); }
  if (execution) query.set("qualification", execution);
  return `#object-sim?${query}`;
}
export function publishedMapRoute(project: string, map: CatalogRef): string {
  return `#object-sim?${new URLSearchParams({ project, mode: "city", map: map.id, map_revision: String(map.revision), map_hash: map.sha256 })}`;
}
export function readSourceResume(hash: string) {
  const query = new URLSearchParams(hash.split("?")[1] ?? "");
  const id = query.get("source") ?? "", revisionText = query.get("revision"), revision = Number(revisionText);
  const sourceHash = query.get("source_hash"), conversionId = query.get("conversion") ?? "";
  const conversionRevision = Number(query.get("conversion_revision")), conversionHash = query.get("conversion_hash");
  const error = id && (!revisionText || !Number.isSafeInteger(revision) || revision < 1 || !sourceHash || !/^[a-f0-9]{64}$/.test(sourceHash))
    ? "이어하기 주소의 원본 개정·해시가 올바르지 않습니다. 지도 목록에서 다시 선택하세요."
    : conversionId && (!id || !Number.isSafeInteger(conversionRevision) || conversionRevision < 1 || !conversionHash || !/^[a-f0-9]{64}$/.test(conversionHash))
      ? "이어하기 주소의 변환 개정·해시가 올바르지 않습니다. 지도 목록에서 다시 선택하세요." : "";
  return { id, revision: error ? null : id ? revision : null, hash: sourceHash, conversionId, conversionRevision, conversionHash, executionId: query.get("qualification") ?? "", error };
}
export function mapUploadError(file: Pick<File, "name" | "size"> | null, kind: SourceKind, maxBytes: number): string {
  if (!file) return "원본 파일을 선택하세요.";
  if (!file.size) return "빈 파일은 등록할 수 없습니다.";
  if (file.size > maxBytes) return `파일이 업로드 한도 ${(maxBytes / 1024 / 1024).toFixed(0)} MiB를 넘습니다.`;
  if (!(kind === "opendrive" ? /\.xodr$/i : /\.zip$/i).test(file.name)) return kind === "opendrive" ? "OpenDRIVE 원본 .xodr 파일을 선택하세요." : "SHP와 속성·좌표계 파일을 함께 담은 원본 .zip을 선택하세요.";
  return "";
}
