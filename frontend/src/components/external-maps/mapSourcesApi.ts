import { api, ApiError, authHeaders } from "../../api";
import type { CatalogRef } from "../city-sim/city-contract";
import type { MapConversion, MapExecution, SourceDetail, SourceKind, SourceRecord, SourceRights } from "./map-source-contract";

export async function bytesSha256(bytes: ArrayBuffer): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
}
function base64(bytes: Uint8Array): string {
  const parts: string[] = [];
  for (let index = 0; index < bytes.length; index += 32768) parts.push(String.fromCharCode(...bytes.subarray(index, index + 32768)));
  return btoa(parts.join(""));
}
// Keep a failed submission's key across reloads. Never store original file bytes or credentials here.
export async function mapSourceWrite<T>(path: string, body: unknown): Promise<T> {
  const text = JSON.stringify(body), fingerprint = await bytesSha256(new TextEncoder().encode(path + "\n" + text).buffer);
  const storageKey = `axiom.map-source.pending:${fingerprint}`;
  let key: string | null = null;
  try { key = sessionStorage.getItem(storageKey); } catch { /* private storage may be unavailable */ }
  key ??= crypto.randomUUID();
  try { sessionStorage.setItem(storageKey, key); } catch { /* request remains usable */ }
  const value = await api<T>(path, { method: "POST", body: text, headers: { "Idempotency-Key": key } });
  try { sessionStorage.removeItem(storageKey); } catch { /* no persisted state */ }
  return value;
}
export async function uploadMapSource(file: File, values: { project_id: string; id?: string; expected_revision: number; name: string; source_kind: SourceKind; rights: SourceRights; metadata: Record<string, unknown> }): Promise<SourceRecord> {
  const bytes = await file.arrayBuffer(), hash = await bytesSha256(bytes);
  const value = await mapSourceWrite<SourceRecord>("/map-sources", { ...values, filename: file.name, content_base64: base64(new Uint8Array(bytes)) });
  if (value.source_sha256 !== hash || value.size_bytes !== file.size || value.project_id !== values.project_id)
    throw new Error("등록된 원본의 해시·크기·프로젝트가 선택 파일과 다릅니다. 목록을 다시 확인하세요.");
  return value;
}
export function getMapSource(project: string, id: string, revision?: number) {
  return api<SourceDetail>(`/map-sources/${encodeURIComponent(id)}?${new URLSearchParams({ project_id: project, ...(revision ? { revision: String(revision) } : {}) })}`);
}
export function convertMapSource(project: string, source: CatalogRef, options: Record<string, unknown>) {
  return mapSourceWrite<MapConversion>(`/map-sources/${encodeURIComponent(source.id)}/convert`, { project_id: project, revision: source.revision, sha256: source.sha256, options });
}
export function qualifyMapSource(project: string, source: CatalogRef, conversion: CatalogRef) {
  return mapSourceWrite<MapExecution>(`/map-sources/${encodeURIComponent(source.id)}/qualify`, { project_id: project, revision: source.revision, sha256: source.sha256, conversion_ref: conversion });
}
export function publishMapSource(project: string, source: CatalogRef, conversion: CatalogRef, execution: string) {
  return mapSourceWrite<{ map: unknown; map_ref: CatalogRef }>(`/map-sources/${encodeURIComponent(source.id)}/publish`, { project_id: project, revision: source.revision, sha256: source.sha256, conversion_ref: conversion, execution_id: execution });
}
export async function downloadMapOriginal(project: string, source: SourceRecord, attachmentIndex?: number) {
  const attachment = attachmentIndex !== undefined ? source.attachments?.[attachmentIndex] : undefined;
  if (attachmentIndex !== undefined && !attachment) throw new Error("선택한 보존 자료를 이 원본 개정에서 찾지 못했습니다.");
  const expected = attachment ?? { filename: source.filename, sha256: source.source_sha256, size_bytes: source.size_bytes };
  const response = await fetch(`/api/v1/map-sources/${encodeURIComponent(source.id)}/export?${new URLSearchParams({ project_id: project, revision: String(source.revision), ...(attachmentIndex !== undefined ? { attachment: String(attachmentIndex) } : {}) })}`, { headers: authHeaders() });
  if (!response.ok) {
    let message = `원본 내려받기 실패 (HTTP ${response.status})`;
    try { const value = await response.json(); if (typeof value.detail === "string") message = value.detail; } catch { /* preserve status */ }
    throw new ApiError(response.status, message);
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength !== expected.size_bytes || await bytesSha256(bytes) !== expected.sha256) throw new Error("내려받은 원본의 크기·해시가 저장 기록과 다릅니다.");
  const url = URL.createObjectURL(new Blob([bytes])), link = document.createElement("a");
  link.href = url; link.download = expected.filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
