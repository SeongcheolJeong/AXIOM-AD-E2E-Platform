import assert from "node:assert/strict";
import test from "node:test";
import { hasMatchingNativeQualification, mapSourceRoute, mapUploadError, publishedMapRoute, readSourceResume } from "../src/components/external-maps/map-source-contract.ts";
import type { MapExecution } from "../src/components/external-maps/map-source-contract.ts";

const source = { id: "map-source-서울", revision: 3, sha256: "a".repeat(64) };
const conversion = { id: "conversion-a", revision: 2, sha256: "b".repeat(64) };
const verified = (): MapExecution => ({ id: "job-native", status: "completed", outcome_uncertain: false, fidelity: "native_carla_external_map_load_readback", summary: {
  source_ref: source, conversion_ref: conversion, native_ready: true, driving_evaluated: false, physical_validation: false,
  qualification: { ready: true, scope: "native_map_load_readback_not_driving" },
} });

test("native publishing is pinned to the exact source, conversion and explicit native scope", () => {
  assert.equal(hasMatchingNativeQualification(verified(), source, conversion), true);
  for (const mismatch of [{ ...source, revision: 4 }, { ...source, sha256: "c".repeat(64) }, { ...source, id: "another-source" }])
    assert.equal(hasMatchingNativeQualification(verified(), mismatch, conversion), false);
  assert.equal(hasMatchingNativeQualification(verified(), source, { ...conversion, revision: 3 }), false);
  assert.equal(hasMatchingNativeQualification(verified(), source, { ...conversion, sha256: "c".repeat(64) }), false);
});

test("completed queue state, configured adapters, synthetic or uncertain results cannot enable publishing", () => {
  assert.equal(hasMatchingNativeQualification({ id: "configured", status: "completed", summary: { native_ready: true } }, source, conversion), false);
  for (const override of [{ status: "running" }, { status: "failed" }, { fidelity: "authored_fixture" }, { outcome_uncertain: true }, { outcome_uncertain: undefined }])
    assert.equal(hasMatchingNativeQualification({ ...verified(), ...override }, source, conversion), false);
  for (const override of [{ driving_evaluated: true }, { physical_validation: true }, { qualification: { ready: false } }, { source_ref: undefined }])
    assert.equal(hasMatchingNativeQualification({ ...verified(), summary: { ...verified().summary, ...override } }, source, conversion), false);
});

test("source resume preserves revision and both hashes; incomplete revision is rejected", () => {
  const route = mapSourceRoute("project 서울", source, conversion, "job-native");
  const resumed = readSourceResume(route);
  assert.equal(resumed.id, source.id); assert.equal(resumed.revision, 3); assert.equal(resumed.hash, source.sha256);
  assert.equal(resumed.conversionId, conversion.id); assert.equal(resumed.conversionRevision, 2); assert.equal(resumed.conversionHash, conversion.sha256);
  assert.equal(resumed.executionId, "job-native"); assert.equal(resumed.error, "");
  for (const query of ["source=x", "source=x&revision=0", "source=x&revision=-1", "source=x&revision=1.2", "source=x&revision=no"])
    assert.ok(readSourceResume(`#object-sim?${query}`).error);
  const published = new URLSearchParams(publishedMapRoute("project 서울", source).split("?")[1]);
  assert.equal(published.get("map_hash"), source.sha256); assert.equal(published.get("map_revision"), "3");
});

test("binary upload validation rejects empty, wrong-extension and oversized source files", () => {
  assert.equal(mapUploadError({ name: "source.XODR", size: 100 }, "opendrive", 200), "");
  assert.equal(mapUploadError({ name: "original.zip", size: 200 }, "ngii_shp", 200), "");
  assert.match(mapUploadError({ name: "converted.json", size: 100 }, "ngii_shp", 200), /원본/);
  assert.match(mapUploadError({ name: "source.xodr", size: 201 }, "opendrive", 200), /한도/);
  assert.match(mapUploadError({ name: "source.zip", size: 0 }, "ngii_shp", 200), /빈 파일/);
  assert.match(mapUploadError(null, "opendrive", 200), /선택/);
});
