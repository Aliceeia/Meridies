// node --test connect/tests/speech.test.js : the client-side spoken-output rules
const test = require("node:test");
const assert = require("node:assert");

// a fake speech engine: records what was spoken and cancelled
const log = { spoken: [], cancels: 0 };
globalThis.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
globalThis.speechSynthesis = { speaking: false, getVoices: () => [{name:"Microsoft Zira", lang:"en-US"}], cancel() { log.cancels++; },
  speak(u) { log.spoken.push(u.text); this.last = u; } };
const S = require("../frontend/speech.js");

test("length limits per kind", () => {
  const long = Array.from({ length: 12 }, (_, i) => `Sentence number ${i + 1} is here.`).join(" ");
  assert.ok(S.shape(long, "arrival").split(". ").length <= 4);
  assert.ok(S.seconds(S.shape(long, "arrival")) < 20);
  assert.ok(S.shape(long, "more").split(". ").length <= 6);
  assert.ok(S.seconds(S.shape("Stop, something is right in front of your face at head height now", "warning")) <= 2.4);
  const huge = "word ".repeat(400) + ".";
  assert.ok(S.seconds(S.shape(huge, "any")) <= 30.5);
});

test("filler, apologies, AI talk and system narration are removed", () => {
  const t = S.clean("I can see a door ahead. It appears that the room is quiet. Sorry, loading the view. As an AI, I think. In this image, a chair.");
  for (const bad of ["i can see", "it appears", "sorry", "loading", "as an ai", "in this image"]) assert.ok(!t.toLowerCase().includes(bad), t);
  assert.ok(t.startsWith("A door ahead."), t);
});

test("vary never repeats the same form twice in a row", () => {
  let prev = null;
  for (let i = 0; i < 50; i++) { const f = S.vary("k", ["a", "b", "c"]); assert.notStrictEqual(f, prev); prev = f; }
});

test("silence: the same words already heard in full are not repeated; cut-off words are", () => {
  log.spoken.length = 0;
  S.say("Kitchen.", { kind: "arrival" });
  speechSynthesis.last.onend();                       // heard in full
  assert.strictEqual(S.say("Kitchen.", { kind: "arrival" }), "");
  assert.strictEqual(log.spoken.length, 1);
  S.say("Dining room.", { kind: "arrival" });
  S.cancel();                                         // cut off mid-sentence
  speechSynthesis.last.onend();
  assert.ok(S.say("Dining room.", { kind: "arrival" }));   // so asking again answers
});

test("a warning and a description are delivered differently", () => {
  assert.strictEqual(S.styleOf("Stop, ahead.", "warning"), "alert");
  assert.strictEqual(S.styleOf("Mind the chandelier: one metre six, two steps ahead.", "arrival"), "alert");
  assert.strictEqual(S.styleOf("Chandelier, one metre six, two steps ahead.", "arrival"), "alert");
  assert.strictEqual(S.styleOf("A dining table, about two steps ahead.", "detail"), "calm");
  assert.ok(S.PACE.alert > S.PACE.calm);
  log.spoken.length = 0;
  S.say("Stop, ahead.", { kind: "warning", force: true });
  const warn = speechSynthesis.last;
  S.say("A dining table, about two steps ahead.", { kind: "detail", force: true });
  const desc = speechSynthesis.last;
  assert.ok(warn.rate > desc.rate, `${warn.rate} vs ${desc.rate}`);
});

test("errors never speak jargon", () => {
  const t = S.oops();
  assert.strictEqual(t, "I couldn't get that — try again.");
});

test("one female voice stays fixed across warnings, location and detail", () => {
  const voice = S.pickVoice();
  assert.ok(/Zira/.test(voice.name));
  for (const kind of ["warning", "detail", "more", "help"]) {
    S.say("A doorway is ahead.", {kind, force:true});
    assert.strictEqual(speechSynthesis.last.voice, voice);
    assert.strictEqual(speechSynthesis.last.pitch, 1);
  }
  S.cancel();
});

test("Gemini short rate limits honor the server retry delay", async () => {
  const original=globalThis.fetch;
  globalThis.fetch=async()=>({status:429,json:async()=>({retryAfter:12})});
  S._audioCache.clear();S._quotaUntil=0;
  const before=Date.now();
  try {
    await assert.rejects(S._audioFor('Quota test.'),/quota/i);
    assert.ok(S._quotaUntil>=before+12000 && S._quotaUntil<=Date.now()+12000);
  } finally {
    globalThis.fetch=original;S._quotaUntil=0;S._audioCache.clear();
  }
});
