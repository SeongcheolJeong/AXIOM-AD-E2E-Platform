import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { deflateRawSync } from "node:zlib";
import { createArtifactRangeReader, locateZipDirectory, openCarlaStoredZip, parseZipDirectory, readStoredMember } from "../src/pages/carla-zip.ts";

function checksum(bytes: Uint8Array) {
  let crc = 0xffffffff;
  for (const byte of bytes) { crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = crc >>> 1 ^ (crc & 1 ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
function fixture(name = "frames/0000-front.bgra8", payload = new Uint8Array([1, 2, 3, 255, 40, 50, 60, 255]), method: 0 | 8 = 0, encoded = method === 8 ? new Uint8Array(deflateRawSync(payload)) : payload) {
  const nameBytes = new TextEncoder().encode(name);
  const local = new Uint8Array(30 + nameBytes.length + encoded.length), lh = new DataView(local.buffer);
  lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(8, method, true); lh.setUint32(14, checksum(payload), true); lh.setUint32(18, encoded.length, true); lh.setUint32(22, payload.length, true); lh.setUint16(26, nameBytes.length, true);
  local.set(nameBytes, 30); local.set(encoded, 30 + nameBytes.length);
  const central = new Uint8Array(46 + nameBytes.length), cd = new DataView(central.buffer);
  cd.setUint32(0, 0x02014b50, true); cd.setUint16(4, 0x0314, true); cd.setUint16(6, 20, true); cd.setUint16(10, method, true); cd.setUint32(16, checksum(payload), true); cd.setUint32(20, encoded.length, true); cd.setUint32(24, payload.length, true); cd.setUint16(28, nameBytes.length, true); central.set(nameBytes, 46);
  const end = new Uint8Array(22), eo = new DataView(end.buffer);
  eo.setUint32(0, 0x06054b50, true); eo.setUint16(8, 1, true); eo.setUint16(10, 1, true); eo.setUint32(12, central.length, true); eo.setUint32(16, local.length, true);
  const zip = new Uint8Array(local.length + central.length + end.length); zip.set(local); zip.set(central, local.length); zip.set(end, local.length + central.length);
  return { zip, local, central, payload, location: { offset: local.length, size: central.length, count: 1 }, member: { name, size_bytes: payload.length, sha256: createHash("sha256").update(payload).digest("hex") } };
}

test("a stored native member is read through bounded archive ranges and source SHA verification", async () => {
  const source = fixture(), requested: number[][] = [];
  const reader = await openCarlaStoredZip(source.zip.length, async (start, end) => { requested.push([start, end]); return source.zip.slice(start, end + 1); }, new AbortController().signal);
  assert.equal(reader.has(source.member), true);
  assert.deepEqual(await reader.read(source.member, new AbortController().signal), source.payload);
  assert.equal(requested.length, 3);
  assert.equal(requested[1][1] - requested[1][0] + 1, source.central.length);
  assert.equal(requested[2][1], source.location.offset - 1);
});

test("city camera member profile stays distinct from legacy sensor archive names", async () => {
  const source = fixture("ego_front_rgb/000000.bgra"), signal = new AbortController().signal;
  const range = async (start: number, end: number) => source.zip.slice(start, end + 1);
  const legacy = await openCarlaStoredZip(source.zip.length, range, signal);
  assert.throws(() => legacy.has(source.member), /manifest/);
  const city = await openCarlaStoredZip(source.zip.length, range, signal, "city-camera");
  city.assertClosure?.([source.member]);
  assert.deepEqual(await city.read(source.member, signal), source.payload);
  assert.throws(() => city.assertClosure?.([]), /closure|미참조/);
});

test("valid directory and local headers bind the exact native member name and size", () => {
  const source = fixture(), location = locateZipDirectory(source.zip, source.zip.length);
  assert.deepEqual(location, source.location);
  const entry = parseZipDirectory(source.central, location).get(source.member.name)!;
  assert.deepEqual(readStoredMember(source.local, entry), source.payload);
});

for (const [name, mutate] of [
  ["encrypted flags", (bytes: Uint8Array) => new DataView(bytes.buffer).setUint16(8, 1, true)],
  ["data descriptors", (bytes: Uint8Array) => new DataView(bytes.buffer).setUint16(8, 8, true)],
  ["unsupported compressed payload", (bytes: Uint8Array) => new DataView(bytes.buffer).setUint16(10, 12, true)],
  ["ZIP64 sentinel", (bytes: Uint8Array) => new DataView(bytes.buffer).setUint32(24, 0xffffffff, true)],
  ["outside-archive offset", (bytes: Uint8Array) => new DataView(bytes.buffer).setUint32(42, 999999, true)],
  ["unsupported version", (bytes: Uint8Array) => new DataView(bytes.buffer).setUint16(6, 45, true)],
] as const) test(`directory rejects ${name}`, () => {
  const source = fixture(); mutate(source.central);
  assert.throws(() => parseZipDirectory(source.central, source.location), /CARLA ZIP/);
});

test("directory rejects traversal names", () => {
  const source = fixture("../0000-front.bgra8");
  assert.throws(() => parseZipDirectory(source.central, source.location), /member 이름/);
});

test("EOCD rejects multi-disk and central range mismatch", () => {
  const source = fixture(), changed = source.zip.slice(), data = new DataView(changed.buffer);
  data.setUint16(changed.length - 18, 1, true);
  assert.throws(() => locateZipDirectory(changed, changed.length), /분할/);
  data.setUint16(changed.length - 18, 0, true); data.setUint32(changed.length - 10, 1, true);
  assert.throws(() => locateZipDirectory(changed, changed.length), /중앙 디렉터리/);
});

test("local header rejects a substituted file name", () => {
  const source = fixture(), entry = parseZipDirectory(source.central, source.location).get(source.member.name)!;
  source.local[30] ^= 1;
  assert.throws(() => readStoredMember(source.local, entry), /이름이 다릅니다/);
});

test("native member rejects a mutated payload even when archive length and headers match", async () => {
  const source = fixture(); source.zip[30 + new TextEncoder().encode(source.member.name).length] ^= 1;
  const reader = await openCarlaStoredZip(source.zip.length, async (start, end) => source.zip.slice(start, end + 1), new AbortController().signal);
  await assert.rejects(reader.read(source.member, new AbortController().signal), /SHA-256/);
});

test("native member rejects a manifest byte count mismatch", async () => {
  const source = fixture(), reader = await openCarlaStoredZip(source.zip.length, async (start, end) => source.zip.slice(start, end + 1), new AbortController().signal);
  assert.throws(() => reader.has({ ...source.member, size_bytes: source.member.size_bytes + 1 }), /manifest/);
});

const artifact = { id: "artifact_zip", name: "single-native-capture.zip", media_type: "application/zip", size_bytes: 1000, sha256: "a".repeat(64), download_url: "/api/v1/experiments/executions/job_native/artifacts/artifact_zip" };
test("Range reader pins route, exact Content-Range, count and request headers", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, artifact.download_url); assert.equal(new Headers(options?.headers).get("Range"), "bytes=100-103");
    return new Response(new Uint8Array([1, 2, 3, 4]), { status: 206, headers: { "Content-Range": "bytes 100-103/1000", "Content-Length": "4" } });
  };
  try { assert.deepEqual(await createArtifactRangeReader("job_native", artifact, {})(100, 103, new AbortController().signal), new Uint8Array([1, 2, 3, 4])); }
  finally { globalThis.fetch = original; }
});

for (const [name, response] of [
  ["full archive fallback", () => new Response(new Uint8Array([1]), { status: 200 })],
  ["wrong Content-Range", () => new Response(new Uint8Array([1, 2, 3, 4]), { status: 206, headers: { "Content-Range": "bytes 100-103/999" } })],
  ["short response", () => new Response(new Uint8Array([1]), { status: 206, headers: { "Content-Range": "bytes 100-103/1000" } })],
  ["oversized response", () => new Response(new Uint8Array([1, 2, 3, 4, 5]), { status: 206, headers: { "Content-Range": "bytes 100-103/1000" } })],
  ["encoded response", () => new Response(new Uint8Array([1, 2, 3, 4]), { status: 206, headers: { "Content-Range": "bytes 100-103/1000", "Content-Encoding": "gzip" } })],
] as const) test(`Range reader rejects ${name}`, async () => {
  const original = globalThis.fetch; globalThis.fetch = async () => response();
  try { await assert.rejects(createArtifactRangeReader("job_native", artifact, {})(100, 103, new AbortController().signal), /CARLA ZIP/); }
  finally { globalThis.fetch = original; }
});

test("archive path substitution and excessive range are rejected before fetch", async () => {
  assert.throws(() => createArtifactRangeReader("job_native", { ...artifact, download_url: "https://external.invalid/archive" }, {}), /契約|계약/);
  await assert.rejects(createArtifactRangeReader("job_native", artifact, {})(0, 1001, new AbortController().signal), /Range/);
});

for (const [name, count] of [["frames/0000-lidar.f32", 16], ["frames/0000-radar.f32", 0], ["frames/0000-roof_scan.f32", 32]] as const) test(`bounded ranging member ${name} accepts original ${count} bytes`, async () => {
  const source = fixture(name, new Uint8Array(count)), reader = await openCarlaStoredZip(source.zip.length, async (start, end) => source.zip.slice(start, end + 1), new AbortController().signal);
  assert.equal(reader.has(source.member), true); assert.deepEqual(await reader.read(source.member, new AbortController().signal), source.payload);
  reader.assertClosure!([source.member]);
});
for (const [name, count] of [["frames/0000-lidar.f32", 15], ["frames/0000-radar.f32", 3200 * 16 + 16], ["frames/0000-front.bgra8", 3], ["unknown.f32", 16]] as const) test(`native member ${name} refuses unsupported count/name ${count}`, async () => {
  const source = fixture(name, new Uint8Array(count)), reader = await openCarlaStoredZip(source.zip.length, async (start, end) => source.zip.slice(start, end + 1), new AbortController().signal);
  assert.throws(() => reader.has(source.member), /manifest/);
});
test("archive closure rejects missing, duplicated and substituted descriptor members", async () => {
  const source = fixture(), reader = await openCarlaStoredZip(source.zip.length, async (start, end) => source.zip.slice(start, end + 1), new AbortController().signal);
  assert.throws(() => reader.assertClosure!([]), /closure|member/);
  assert.throws(() => reader.assertClosure!([source.member, source.member]), /closure/);
  assert.throws(() => reader.assertClosure!([{ ...source.member, size_bytes: source.member.size_bytes + 1 }]), /closure/);
});

const cityMember = "ego_front_rgb/000000.bgra";
async function cityReader(source: ReturnType<typeof fixture>, requested: number[][] = []) {
  return openCarlaStoredZip(source.zip.length, async (start, end) => { requested.push([start, end]); return source.zip.slice(start, end + 1); }, new AbortController().signal, "city-camera");
}
function redeclareSize(source: ReturnType<typeof fixture>, size: number) {
  new DataView(source.zip.buffer).setUint32(22, size, true);
  new DataView(source.zip.buffer).setUint32(source.location.offset + 24, size, true);
  source.member.size_bytes = size;
}

test("city DEFLATE reads only bounded compressed ranges and restores exact independent zlib bytes", async () => {
  const source = fixture(cityMember, new Uint8Array(1024 * 1024).fill(42), 8), requested: number[][] = [];
  const reader = await cityReader(source, requested);
  reader.assertClosure!([source.member]);
  assert.deepEqual(await reader.read(source.member, new AbortController().signal), source.payload);
  assert.ok(source.zip.length < source.payload.length / 100);
  assert.ok(requested[2][1] - requested[2][0] + 1 <= source.local.length);
});

test("legacy sensor profile still refuses DEFLATE archives", async () => {
  const source = fixture(undefined, undefined, 8);
  await assert.rejects(openCarlaStoredZip(source.zip.length, async (start, end) => source.zip.slice(start, end + 1), new AbortController().signal), /ZIP_STORED/);
});

test("declared oversized DEFLATE frame rejects before decoding", async () => {
  const source = fixture(cityMember, new Uint8Array(8), 8); redeclareSize(source, 16 * 1024 * 1024 + 4);
  await assert.rejects(cityReader(source), /압축 방식·크기/);
});

test("DEFLATE expansion beyond its declared frame rejects instead of growing the output buffer", async () => {
  const source = fixture(cityMember, new Uint8Array(256 * 1024), 8); redeclareSize(source, 8);
  await assert.rejects((await cityReader(source)).read(source.member, new AbortController().signal), /프레임 크기를 초과/);
});

test("DEFLATE expansion shorter than declared frame rejects", async () => {
  const source = fixture(cityMember, new Uint8Array(8), 8); redeclareSize(source, 12);
  await assert.rejects((await cityReader(source)).read(source.member, new AbortController().signal), /출력 크기/);
});

test("truncated raw DEFLATE stream rejects even when compressed sizes and headers agree", async () => {
  const payload = new Uint8Array(4096).fill(42), encoded = deflateRawSync(payload);
  const source = fixture(cityMember, payload, 8, encoded.subarray(0, encoded.length - 1));
  await assert.rejects((await cityReader(source)).read(source.member, new AbortController().signal));
});

test("tampered decoded pixels still reject through the pinned original SHA256", async () => {
  const payload = new Uint8Array(8).fill(42), changed = payload.slice(); changed[1] ^= 1;
  const source = fixture(cityMember, payload, 8, deflateRawSync(changed));
  await assert.rejects((await cityReader(source)).read(source.member, new AbortController().signal), /SHA-256/);
});

test("CRC32 is checked even if both ZIP headers repeat the same false CRC and source SHA matches", async () => {
  const source = fixture(cityMember, undefined, 8), data = new DataView(source.zip.buffer);
  data.setUint32(14, 0, true); data.setUint32(source.location.offset + 16, 0, true);
  await assert.rejects((await cityReader(source)).read(source.member, new AbortController().signal), /CRC32/);
});

test("DEFLATE local compression method mismatch rejects", async () => {
  const source = fixture(cityMember, undefined, 8); new DataView(source.zip.buffer).setUint16(8, 0, true);
  await assert.rejects((await cityReader(source)).read(source.member, new AbortController().signal), /local header/);
});

test("aborted DEFLATE decode does not return pixels", async () => {
  const source = fixture(cityMember, new Uint8Array(4096), 8), reader = await cityReader(source), controller = new AbortController();
  controller.abort();
  await assert.rejects(reader.read(source.member, controller.signal), /abort/i);
});
