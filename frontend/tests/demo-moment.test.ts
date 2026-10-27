import test from "node:test";
import assert from "node:assert/strict";
import { boundedDemoMoment, demoGalleryReturn, demoMoment, demoMomentIndex } from "../src/demo-moment.ts";

test("bookmarked time ignores invalid inputs and clamps to actual capture bounds", () => {
  for (const value of ["", "-1", "Infinity", "NaN", "oops"]) assert.equal(demoMoment(`#hil-sim?time_s=${value}`), null);
  assert.equal(demoMoment("#sensor-sim?time_s=7.25"), 7.25);
  assert.equal(boundedDemoMoment(null, .05, 18), .05);
  assert.equal(boundedDemoMoment(1000, .05, 18), 18);
  assert.equal(boundedDemoMoment(0, .05, 18), .05);
});

test("capture bookmark selects the nearest retained sample, not a fixed assumed FPS", () => {
  assert.equal(demoMomentIndex([0, .1, .3, .9], .35), 2);
  assert.equal(demoMomentIndex([0, .1, .3, .9], 30), 3);
  assert.equal(demoMomentIndex([], 3), 0);
});

test("return navigation preserves project and case identity without accepting an external URL", () => {
  assert.equal(demoGalleryReturn("#hil-sim?project=p&demo_case=hil_delay_200&time_s=4"), "#workspace?project=p&demo_case=hil_delay_200");
  assert.equal(demoGalleryReturn("#hil-sim?project=p"), null);
});
