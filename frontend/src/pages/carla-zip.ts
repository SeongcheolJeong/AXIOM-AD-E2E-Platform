export type CarlaZipArtifact = { id: string; name: string; media_type: string; size_bytes: number; sha256: string; download_url: string };
export type CarlaZipMember = { name: string; size_bytes: number; sha256: string };
export type ZipEntry = { name: string; size: number; compressedSize: number; method: 0 | 8; crc: number; offset: number; flags: number; nameBytes: Uint8Array };
export type ZipDirectoryLocation = { offset: number; size: number; count: number };
export type RangeReader = (start: number, end: number, signal: AbortSignal) => Promise<Uint8Array>;

const TAIL_LIMIT = 65557, DIRECTORY_LIMIT = 4 * 1024 * 1024, FRAME_LIMIT = 16 * 1024 * 1024, LOCAL_HEADER_LIMIT = 4096;
const COMPRESSED_LIMIT = FRAME_LIMIT + 65536;
function reject(message: string): never { throw new Error(`CARLA ZIP: ${message}`); }
function view(bytes: Uint8Array) { return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength); }
function flagsSupported(flags: number) { if ((flags & ~0x800) !== 0) reject("encrypted / descriptor / unsupported flags는 지원하지 않습니다."); }
function extraSupported(bytes: Uint8Array) {
  const data = view(bytes); let offset = 0;
  while (offset < bytes.length) {
    if (offset + 4 > bytes.length) reject("extra field 경계가 잘못되었습니다.");
    const id = data.getUint16(offset, true), size = data.getUint16(offset + 2, true);
    if (id === 1 || id === 0x9901) reject("ZIP64 / encrypted archive는 지원하지 않습니다.");
    offset += 4 + size;
    if (offset > bytes.length) reject("extra field 크기가 잘못되었습니다.");
  }
}

export function locateZipDirectory(tail: Uint8Array, total: number): ZipDirectoryLocation {
  if (!Number.isSafeInteger(total) || total < 22 || total >= 0xffffffff || tail.length !== Math.min(total, TAIL_LIMIT)) reject("원본 archive 크기·tail 범위가 유효하지 않습니다.");
  const data = view(tail);
  for (let offset = tail.length - 22; offset >= 0; offset--) {
    if (data.getUint32(offset, true) !== 0x06054b50 || offset + 22 + data.getUint16(offset + 20, true) !== tail.length) continue;
    const disk = data.getUint16(offset + 4, true), directoryDisk = data.getUint16(offset + 6, true), diskCount = data.getUint16(offset + 8, true), count = data.getUint16(offset + 10, true), size = data.getUint32(offset + 12, true), directoryOffset = data.getUint32(offset + 16, true);
    if (disk !== 0 || directoryDisk !== 0 || diskCount !== count) reject("분할 archive는 지원하지 않습니다.");
    if (count === 0xffff || size === 0xffffffff || directoryOffset === 0xffffffff) reject("ZIP64는 지원하지 않습니다.");
    if (count < 1 || count > 20000 || size < 46 || size > DIRECTORY_LIMIT || directoryOffset + size !== total - tail.length + offset) reject("중앙 디렉터리 범위·개수 제한이 유효하지 않습니다.");
    return { offset: directoryOffset, size, count };
  }
  return reject("정확한 EOCD를 찾지 못했습니다.");
}

export function parseZipDirectory(bytes: Uint8Array, location: ZipDirectoryLocation): Map<string, ZipEntry> {
  if (bytes.length !== location.size || bytes.length > DIRECTORY_LIMIT) reject("중앙 디렉터리 크기가 다릅니다.");
  const data = view(bytes), entries = new Map<string, ZipEntry>(); let offset = 0;
  for (let index = 0; index < location.count; index++) {
    if (offset + 46 > bytes.length || data.getUint32(offset, true) !== 0x02014b50) reject("중앙 디렉터리 header가 잘못되었습니다.");
    const flags = data.getUint16(offset + 8, true), method = data.getUint16(offset + 10, true), compressed = data.getUint32(offset + 20, true), size = data.getUint32(offset + 24, true), nameLength = data.getUint16(offset + 28, true), extraLength = data.getUint16(offset + 30, true), commentLength = data.getUint16(offset + 32, true), disk = data.getUint16(offset + 34, true), localOffset = data.getUint32(offset + 42, true);
    flagsSupported(flags);
    if (data.getUint16(offset + 6, true) > 20 || (method !== 0 && method !== 8) || (method === 0 && compressed !== size) || size > FRAME_LIMIT || compressed > COMPRESSED_LIMIT || localOffset === 0xffffffff) reject("지원하지 않는 압축 방식·크기 또는 ZIP64 member입니다.");
    if (disk !== 0 || nameLength < 1 || nameLength > 1024 || offset + 46 + nameLength + extraLength + commentLength > bytes.length || localOffset + 30 + nameLength + compressed > location.offset) reject("member header·범위가 유효하지 않습니다.");
    const nameBytes = bytes.slice(offset + 46, offset + 46 + nameLength), name = new TextDecoder("utf-8", { fatal: true }).decode(nameBytes);
    if (name.startsWith("/") || name.includes("\\") || name.includes("\0") || name.split("/").some((part) => part === "." || part === "..") || entries.has(name)) reject("모호하거나 안전하지 않은 member 이름입니다.");
    extraSupported(bytes.subarray(offset + 46 + nameLength, offset + 46 + nameLength + extraLength));
    entries.set(name, { name, size, compressedSize: compressed, method: method as 0 | 8, flags, offset: localOffset, crc: data.getUint32(offset + 16, true), nameBytes });
    offset += 46 + nameLength + extraLength + commentLength;
  }
  if (offset !== bytes.length) reject("중앙 디렉터리에 지원하지 않는 추가 구조가 있습니다.");
  return entries;
}

function readMemberPayload(bytes: Uint8Array, entry: ZipEntry): Uint8Array {
  if (bytes.length < 30 || bytes.length > entry.compressedSize + LOCAL_HEADER_LIMIT) reject("local member 범위가 잘못되었습니다.");
  const data = view(bytes), nameLength = data.getUint16(26, true), extraLength = data.getUint16(28, true), headerLength = 30 + nameLength + extraLength;
  if (data.getUint32(0, true) !== 0x04034b50 || data.getUint16(4, true) > 20 || data.getUint16(6, true) !== entry.flags || data.getUint16(8, true) !== entry.method || data.getUint32(14, true) !== entry.crc || data.getUint32(18, true) !== entry.compressedSize || data.getUint32(22, true) !== entry.size || nameLength !== entry.nameBytes.length || headerLength > LOCAL_HEADER_LIMIT || headerLength + entry.compressedSize > bytes.length) reject("local header가 중앙 디렉터리와 다릅니다.");
  const name = bytes.subarray(30, 30 + nameLength);
  if (name.some((value, index) => value !== entry.nameBytes[index])) reject("local member 이름이 다릅니다.");
  extraSupported(bytes.subarray(30 + nameLength, headerLength));
  return bytes.slice(headerLength, headerLength + entry.compressedSize);
}

export function readStoredMember(bytes: Uint8Array, entry: ZipEntry): Uint8Array {
  if (entry.method !== 0) reject("ZIP_STORED member가 아닙니다.");
  return readMemberPayload(bytes, entry);
}

async function inflateMember(payload: Uint8Array, size: number, signal: AbortSignal): Promise<Uint8Array> {
  signal.throwIfAborted();
  if (typeof DecompressionStream === "undefined") reject("이 브라우저는 raw DEFLATE 재생을 지원하지 않습니다.");
  // Stream one bounded member, with bounded input chunks, never the entire ZIP.
  let inputOffset = 0;
  const input = new ReadableStream<BufferSource>({ pull(controller) {
    if (signal.aborted) { controller.error(signal.reason); return; }
    if (inputOffset === payload.length) { controller.close(); return; }
    const end = Math.min(inputOffset + 4096, payload.length);
    controller.enqueue(new Uint8Array(payload.subarray(inputOffset, end))); inputOffset = end;
  } });
  const reader = input.pipeThrough(new DecompressionStream("deflate-raw")).getReader();
  const bytes = new Uint8Array(size); let offset = 0;
  const abort = () => { void reader.cancel(signal.reason).catch(() => {}); };
  signal.addEventListener("abort", abort, { once: true });
  try {
    while (true) {
      signal.throwIfAborted();
      const next = await reader.read(); if (next.done) break;
      if (offset + next.value.length > size) reject("DEFLATE 출력이 선언한 프레임 크기를 초과합니다.");
      bytes.set(next.value, offset); offset += next.value.length;
    }
    signal.throwIfAborted();
    if (offset !== size) reject("DEFLATE 출력 크기가 선언한 프레임과 다릅니다.");
    return bytes;
  } catch (reason) { await reader.cancel().catch(() => {}); throw reason; }
  finally { signal.removeEventListener("abort", abort); reader.releaseLock(); }
}

const CRC_TABLE = Uint32Array.from({ length: 256 }, (_, value) => {
  for (let bit = 0; bit < 8; bit++) value = value >>> 1 ^ (value & 1 ? 0xedb88320 : 0);
  return value >>> 0;
});
function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crc >>> 8 ^ CRC_TABLE[(crc ^ byte) & 255];
  return (crc ^ 0xffffffff) >>> 0;
}

export function createArtifactRangeReader(executionId: string, artifact: CarlaZipArtifact, headers: HeadersInit): RangeReader {
  const route = `/api/v1/experiments/executions/${encodeURIComponent(executionId)}/artifacts/${encodeURIComponent(artifact.id)}`;
  if (artifact.download_url !== route || artifact.media_type !== "application/zip" || !/^[a-f0-9]{64}$/.test(artifact.sha256) || !Number.isSafeInteger(artifact.size_bytes) || artifact.size_bytes < 22 || artifact.size_bytes >= 0xffffffff) reject("고정 archive 계약이 유효하지 않습니다.");
  return async (start, end, signal) => {
    const count = end - start + 1;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end < start || end >= artifact.size_bytes || count > COMPRESSED_LIMIT + LOCAL_HEADER_LIMIT) reject("요청 Range가 제한을 초과합니다.");
    const requestHeaders = new Headers(headers); requestHeaders.set("Range", `bytes=${start}-${end}`);
    const response = await fetch(route, { headers: requestHeaders, signal, redirect: "error" });
    if (response.status !== 206 || response.headers.get("Content-Range") !== `bytes ${start}-${end}/${artifact.size_bytes}` || response.headers.get("Content-Encoding") && response.headers.get("Content-Encoding") !== "identity" || !response.body) { await response.body?.cancel(); reject(`정확한 Range 응답이 필요합니다 (${response.status}).`); }
    const declared = response.headers.get("Content-Length");
    if (declared !== null && declared !== String(count)) { await response.body.cancel(); reject("Range Content-Length가 다릅니다."); }
    const reader = response.body.getReader(), bytes = new Uint8Array(count); let offset = 0;
    try {
      while (true) {
        const result = await reader.read(); if (result.done) break;
        if (offset + result.value.length > count) reject("Range 응답이 고정 크기를 초과합니다.");
        bytes.set(result.value, offset); offset += result.value.length;
      }
    } catch (reason) { await reader.cancel(); throw reason; }
    if (offset !== count) reject("Range 응답 크기가 부족합니다.");
    return bytes;
  };
}

export type CarlaStoredZip = { has: (member: CarlaZipMember) => boolean; read: (member: CarlaZipMember, signal: AbortSignal) => Promise<Uint8Array>; assertClosure?: (members: CarlaZipMember[]) => void };
export async function openCarlaStoredZip(total: number, range: RangeReader, signal: AbortSignal, profile: "legacy-sensor" | "city-camera" = "legacy-sensor"): Promise<CarlaStoredZip> {
  const tail = await range(Math.max(0, total - TAIL_LIMIT), total - 1, signal), location = locateZipDirectory(tail, total);
  const entries = parseZipDirectory(await range(location.offset, location.offset + location.size - 1, signal), location);
  if (profile === "legacy-sensor" && [...entries.values()].some(entry => entry.method !== 0)) reject("legacy sensor는 ZIP_STORED만 지원합니다.");
  function pinned(member: CarlaZipMember) {
    const entry = entries.get(member.name);
    const camera = profile === "city-camera" ? /^[A-Za-z0-9_-]{1,64}\/\d{6}\.bgra$/.test(member.name) : /^frames\/\d{4}-[A-Za-z][A-Za-z0-9_-]{0,31}\.bgra8$/.test(member.name);
    const ranging = profile === "legacy-sensor" && /^frames\/\d{4}-[A-Za-z][A-Za-z0-9_-]{0,31}\.f32$/.test(member.name);
    // Kind/blueprint and its tighter count limit are additionally bound by the
    // capture parser. Accept zero native ranging returns, never invent a point.
    if (!entry || !(camera || ranging) || !Number.isSafeInteger(member.size_bytes) || member.size_bytes < (camera ? 4 : 0) || member.size_bytes > (camera ? FRAME_LIMIT : 3200 * 16) || member.size_bytes % (camera ? 4 : 16) !== 0 || entry.size !== member.size_bytes || !/^[a-f0-9]{64}$/.test(member.sha256)) reject("manifest의 native sensor member가 archive와 다릅니다.");
    return entry;
  }
  return {
    has(member) { pinned(member); return true; },
    assertClosure(members) {
      const names = new Set<string>();
      for (const member of members) {
        const entry = entries.get(member.name);
        if (names.has(member.name) || !entry || entry.size !== member.size_bytes || !Number.isSafeInteger(member.size_bytes) || member.size_bytes < 0 || !/^[a-f0-9]{64}$/.test(member.sha256)) reject("native archive의 원본 manifest closure가 다릅니다.");
        names.add(member.name);
      }
      if (names.size !== entries.size) reject("native archive에 미참조·누락 member가 있습니다.");
    },
    async read(member, signal) {
      const entry = pinned(member), count = Math.min(entry.compressedSize + LOCAL_HEADER_LIMIT, location.offset - entry.offset);
      const payload = readMemberPayload(await range(entry.offset, entry.offset + count - 1, signal), entry);
      const bytes = entry.method === 8 ? await inflateMember(payload, entry.size, signal) : payload;
      const actual = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)))).map((value) => value.toString(16).padStart(2, "0")).join("");
      if (actual !== member.sha256) reject("native 프레임 SHA-256이 capture manifest와 다릅니다.");
      if (crc32(bytes) !== entry.crc) reject("native 프레임 CRC32가 ZIP header와 다릅니다.");
      return bytes;
    },
  };
}
