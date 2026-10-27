import test from "node:test";
import assert from "node:assert/strict";
import {
  advanceStory,
  interruptStory,
  startStory,
  storyProblem,
  storyDecisionSummary,
} from "../src/components/object-demo/story-model.ts";
import type {
  ObjectStory,
  StorySegment,
} from "../src/components/object-demo/story-model.ts";

// Authored controller fixtures: no native execution or safety evidence is claimed.
function segment(start = 0, anchor = 2, end = 6): StorySegment {
  return {
    id: `segment-${start}`,
    start_time_s: start,
    anchor_time_s: anchor,
    end_time_s: end,
    hold_s: 2,
    playback_rate: 1,
    title: "observed event",
    description: "planner event",
    actor_ids: ["ego"],
    events: [
      {
        side: "left",
        category: "wait",
        actor_id: "ego",
        time_s: anchor - 0.05,
        observation_time_s: anchor,
        type: "decision_changed",
        reason: "gap_wait",
        index: 40,
        input_frame: 42,
        provenance: "observed_state_reactive_planner",
      },
    ],
  };
}
function story(segments = [segment()]): ObjectStory {
  return {
    schema_version: "object-sim-story/1",
    clock: "shared_simulation_time_s",
    status: "available",
    reason: null,
    segments,
    total_presentation_s: segments.reduce(
      (sum, item) => sum + item.end_time_s - item.start_time_s + item.hold_s,
      0,
    ),
  } as ObjectStory;
}

test("guide holds exactly at observed anchor before continuing to post-event time", () => {
  const segments = [segment()];
  let state = startStory(segments);
  state = advanceStory(state, segments, 1, true);
  assert.equal(state.timeS, 1);
  state = advanceStory(state, segments, 10, true);
  assert.equal(state.timeS, 2);
  assert.equal(state.phase, "hold");
  state = advanceStory(state, segments, 1, true);
  assert.equal(state.timeS, 2);
  assert.equal(state.holdRemainingS, 1);
  state = advanceStory(state, segments, 1, true);
  assert.equal(state.phase, "after");
  assert.equal(state.timeS, 2);
  state = advanceStory(state, segments, 1, true);
  assert.equal(state.timeS, 3);
});

test("both native viewers must be ready; loading never consumes the explanation hold", () => {
  const segments = [segment(0, 0, 4)];
  const state = startStory(segments);
  assert.equal(state.phase, "hold");
  assert.equal(advanceStory(state, segments, 2, false), state);
  assert.equal(advanceStory(state, segments, NaN, true), state);
});

test("manual pause, seek or camera interaction exits guide until explicit restart", () => {
  const segments = [segment(), segment(10, 12, 16)];
  const state = interruptStory(startStory(segments, 1), 13.5);
  assert.equal(state.mode, "manual");
  assert.equal(state.timeS, 13.5);
  assert.equal(advanceStory(state, segments, 20, true), state);
  assert.equal(startStory(segments, state.segmentIndex).timeS, 10);
});

test("segment advances use one shared simulation clock and stop after final original time", () => {
  const segments = [segment(0, 0, 1), segment(10, 10, 11)];
  let state = advanceStory(startStory(segments), segments, 2, true);
  state = advanceStory(state, segments, 2, true);
  assert.equal(state.segmentIndex, 1);
  assert.equal(state.timeS, 10);
  state = advanceStory(state, segments, 2, true);
  state = advanceStory(state, segments, 2, true);
  assert.equal(state.mode, "completed");
  assert.equal(state.timeS, 11);
  assert.equal(advanceStory(state, segments, 2, true), state);
});

test("reject guide budgets over60s, invented anchors and unsupported timing", () => {
  assert.equal(storyProblem(story()), null);
  assert.match(storyProblem(story([segment(0, 2, 59)]))!, /60/);
  const invented = story();
  invented.segments[0].anchor_time_s = 3;
  assert.match(storyProblem(invented)!, /사건 시각/);
  const invalidHold = story();
  invalidHold.segments[0].hold_s = 3;
  assert.match(storyProblem(invalidHold)!, /사건 시각/);
  const unavailable = {
    ...story(),
    status: "unavailable",
    reason: "no_representative_planner_events",
    segments: [],
  } as ObjectStory;
  assert.match(storyProblem(unavailable)!, /대표 사건/);
});

test("guide anchor is retained response time, never the earlier command timestamp", () => {
  const value = story([segment(0.05, 0.05, 4)]);
  assert.equal(value.segments[0].events[0].time_s, 0);
  assert.equal(storyProblem(value), null);
  const state = startStory(value.segments);
  assert.equal(state.timeS, 0.05);
  assert.equal(state.phase, "hold");
  value.segments[0].anchor_time_s = 0;
  assert.match(storyProblem(value)!, /사건 시각/);
});

test("story target explanation requires same-frame retained diagnostics and preserves observed time", () => {
  const event = segment().events[0];
  event.reason = "follow_gap";
  event.decision_context = { source: "retained_planner_diagnostics", frame_index: event.index, input_frame: event.input_frame, target_actor_id: "lead", gap_m: 5.4, lead_speed_mps: 0, requested_speed_mps: 0, stop: true };
  assert.equal(storyDecisionSummary(event), "ego → lead · 간격 5.4 m · 선행차 GT 0.0 m/s · 정지 요청 · 관측 2.00s");
  event.reason = "follow_lead_stop";
  assert.equal(storyDecisionSummary(event), "ego → lead · 간격 5.4 m · 선행차 GT 0.0 m/s · 정지 요청 · 관측 2.00s");
  event.decision_context.frame_index++;
  assert.equal(storyDecisionSummary(event), null);
  delete event.decision_context;
  assert.equal(storyDecisionSummary(event), null);
});
