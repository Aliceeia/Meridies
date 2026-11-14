// node --test connect/tests/      (no dependencies: Node's built-in test runner)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { initial, transition } = require("../frontend/after_record.js");

const PRIVATE_HOME = { isPublicSpace: false };
const MUSEUM = { isPublicSpace: true };

// drive a sequence of events; return final state and every effect emitted
function run(events, ctx = MUSEUM) {
  let s = initial, all = [];
  for (const ev of events) {
    const r = transition(s, ev, ctx);
    s = r.state; all = all.concat(r.effects);
  }
  return { s, fx: all, commits: all.filter((e) => e.type === "commit"), asks: all.filter((e) => e.type === "ask") };
}
const toDeciding = [{ type: "START" }, { type: "STOPPED", memoryId: "m1" }, { type: "PLAYBACK_ENDED" }];

test("happy path: record -> automatic playback -> one question", () => {
  const r = run(toDeciding);
  assert.equal(r.s.phase, "deciding");
  assert.deepEqual(r.fx.filter((e) => e.type === "playReview"), [{ type: "playReview", memoryId: "m1" }]);
  assert.equal(r.asks.length, 1);
  assert.equal(r.asks[0].prompt, "Saved. Keep it private, or share with the community?");
  assert.deepEqual(r.asks[0].options, ["private", "share", "later"], "fixed order");
});

// ---------- silence is never consent ----------
for (const ev of ["TIMEOUT", "CANCEL"]) {
  test(`no answer (${ev}) -> private`, () => {
    const r = run([...toDeciding, { type: ev }]);
    assert.equal(r.s.phase, "saved");
    assert.equal(r.s.result, "private");
    assert.equal(r.commits.at(-1).choice, "private");
    assert.ok(!r.commits.some((c) => c.choice === "share"));
  });
}
test("leaving during playback (CANCEL) -> private, nothing asked", () => {
  const r = run([{ type: "START" }, { type: "STOPPED", memoryId: "m1" }, { type: "CANCEL" }]);
  assert.equal(r.s.result, "private");
  assert.equal(r.asks.length, 0);
});
test("never asked twice: extra events after the answer change nothing", () => {
  const r = run([...toDeciding, { type: "TIMEOUT" }, { type: "PLAYBACK_ENDED" }, { type: "CHOOSE", choice: "share" }]);
  assert.equal(r.asks.length, 1);
  assert.equal(r.s.result, "private");
  assert.equal(r.commits.length, 1);
});
test("an unknown choice falls back to private", () => {
  assert.equal(run([...toDeciding, { type: "CHOOSE", choice: "everyone!" }]).s.result, "private");
});

// ---------- the three options ----------
test("Keep private", () => {
  const r = run([...toDeciding, { type: "CHOOSE", choice: "private" }]);
  assert.equal(r.s.result, "private"); assert.equal(r.s.draft, false);
  assert.equal(r.commits[0].choice, "private");
});
test("Share to community -> pending_review, not public", () => {
  const r = run([...toDeciding, { type: "CHOOSE", choice: "share" }]);
  assert.equal(r.s.result, "pending_review");
  assert.equal(r.commits[0].choice, "share");
  assert.match(r.fx.find((e) => e.type === "announce").text, /private until it's approved/);
});
test("Decide later -> private draft", () => {
  const r = run([...toDeciding, { type: "CHOOSE", choice: "later" }]);
  assert.equal(r.s.result, "draft"); assert.equal(r.s.draft, true);
  assert.equal(r.commits[0].choice, "later");
});

// ---------- interruptions (obstacle warning) ----------
test("interrupted while recording: mic stops, private draft, no playback, no question, no sound", () => {
  const r = run([{ type: "START" }, { type: "INTERRUPT" }, { type: "STOPPED", memoryId: "m1" }, { type: "PLAYBACK_ENDED" }]);
  assert.equal(r.s.result, "interrupted-draft"); assert.equal(r.s.draft, true);
  assert.ok(r.fx.some((e) => e.type === "stopRecording"));
  assert.ok(!r.fx.some((e) => e.type === "playReview"), "no auto-resume into playback");
  assert.equal(r.asks.length, 0);
  assert.ok(!r.fx.some((e) => e.type === "announce"), "warning audio has priority");
});
test("interrupted during playback: stops, no question, private draft", () => {
  const r = run([{ type: "START" }, { type: "STOPPED", memoryId: "m1" }, { type: "INTERRUPT" }, { type: "PLAYBACK_ENDED" }]);
  assert.equal(r.s.result, "interrupted-draft");
  assert.ok(r.fx.some((e) => e.type === "stopAudio"));
  assert.equal(r.asks.length, 0);
  assert.deepEqual(r.commits.map((c) => c.choice), ["later"]);
  assert.ok(!r.fx.some((e) => e.type === "announce"));
});
test("interrupted while deciding: stops, closes, chooses nothing, private draft", () => {
  const r = run([...toDeciding, { type: "INTERRUPT" }, { type: "CHOOSE", choice: "share" }]);
  assert.equal(r.s.result, "interrupted-draft");
  assert.ok(r.fx.some((e) => e.type === "closeAsk"));
  assert.deepEqual(r.commits.map((c) => c.choice), ["later"], "no option auto-selected, no share afterwards");
  assert.ok(!r.fx.some((e) => e.type === "announce"));
});
test("after an interruption nothing resumes until a new manual START", () => {
  let r = run([{ type: "START" }, { type: "STOPPED", memoryId: "m1" }, { type: "INTERRUPT" }]);
  assert.equal(r.s.phase, "saved");
  const again = transition(r.s, { type: "PLAYBACK_ENDED" }, MUSEUM);
  assert.equal(again.state.phase, "saved"); assert.equal(again.effects.length, 0);
  assert.equal(transition(r.s, { type: "START" }, MUSEUM).state.phase, "recording");
});

// ---------- unlimited recording ----------
test("skipping a long playback (Enter) goes to the question, never to a choice", () => {
  const r = run([{ type: "START" }, { type: "STOPPED", memoryId: "m1" }, { type: "SKIP_REVIEW" }]);
  assert.equal(r.s.phase, "deciding");
  assert.ok(r.fx.some((e) => e.type === "stopAudio"));
  assert.equal(r.commits.length, 0);
  assert.equal(run([{ type: "START" }, { type: "STOPPED", memoryId: "m1" }, { type: "SKIP_REVIEW" }, { type: "TIMEOUT" }]).s.result, "private");
});
test("forgotten recording auto-stopped at the hard limit -> private, no playback, no question", () => {
  const r = run([{ type: "START" }, { type: "AUTO_STOPPED", memoryId: "m1" }, { type: "PLAYBACK_ENDED" }]);
  assert.equal(r.s.result, "private");
  assert.ok(!r.fx.some((e) => e.type === "playReview"));
  assert.equal(r.asks.length, 0);
  assert.deepEqual(r.commits.map((c) => c.choice), ["private"]);
});

// ---------- non-public spaces ----------
test("private home: no community option, and a forged share becomes private", () => {
  const r = run([...toDeciding, { type: "CHOOSE", choice: "share" }], PRIVATE_HOME);
  assert.deepEqual(r.asks[0].options, ["private", "later"]);
  assert.ok(!/community/.test(r.asks[0].prompt));
  assert.equal(r.s.result, "private");
  assert.ok(!r.commits.some((c) => c.choice === "share"));
});
test("missing public flag counts as NOT public", () => {
  const r = run(toDeciding, {});
  assert.deepEqual(r.asks[0].options, ["private", "later"]);
});
