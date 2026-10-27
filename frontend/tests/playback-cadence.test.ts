import test from "node:test";
import assert from "node:assert/strict";
import {
  followingPlaybackDeadline,
  playbackDeadline,
} from "../src/components/object-demo/playback-cadence.ts";

test("React acknowledgement latency consumes the interval instead of adding to every step", () => {
  let deadline = playbackDeadline(null, 0, "manual:1");
  for (let step = 1; step <= 200; step++) {
    assert.equal(deadline.dueMs, step * 50);
    const next = followingPlaybackDeadline(deadline, deadline.dueMs);
    deadline = playbackDeadline(next, deadline.dueMs + 17, "manual:1");
  }
  assert.equal(deadline.dueMs, 10050);
});

test("a normal readiness round trip preserves cadence; long load backlog is discarded", () => {
  const next = { key: "manual:1", dueMs: 100 };
  assert.equal(playbackDeadline(next, 83, next.key), next);
  assert.equal(playbackDeadline(next, 125, next.key), next);
  assert.deepEqual(playbackDeadline(next, 250, next.key), {
    key: next.key,
    dueMs: 300,
  });
  assert.deepEqual(followingPlaybackDeadline(next, 250), {
    key: next.key,
    dueMs: 300,
  });
});

test("pause/background reset and speed/segment/phase changes establish fresh deadlines", () => {
  const prior = { key: "manual:1", dueMs: 100 };
  assert.equal(playbackDeadline(null, 10000, "manual:1").dueMs, 10050);
  assert.equal(playbackDeadline(prior, 90, "manual:2").dueMs, 140);
  const anchor = playbackDeadline(prior, 130, "guide:0:hold");
  let deadline = anchor;
  for (let tick = 1; tick < 40; tick++)
    deadline = followingPlaybackDeadline(deadline, deadline.dueMs);
  assert.equal(deadline.dueMs - 130, 2000);
  assert.equal(playbackDeadline(deadline, 2130, "guide:0:after").dueMs, 2180);
  assert.equal(playbackDeadline(deadline, 2130, "guide:1:hold").dueMs, 2180);
});
