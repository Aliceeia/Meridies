// node --test connect/tests/voice_control.test.js : what each spoken phrase does in blindfold mode
const test = require("node:test");
const assert = require("node:assert");
const { parse } = require("../frontend/voice_control.js");

const cases = {
  "Take me to the kitchen": { act: "go-to", target: "kitchen" },
  "go to the dining room please": { act: "go-to", target: "dining room" },
  "Take me to the nearest memory.": { act: "go-to", target: "nearest memory" },
  "take me to Theo's memory": { act: "go-to", target: "theo's memory" },
  "Where's the bedroom?": { act: "where-is", target: "bedroom" },
  "take me there": { act: "go-last" },
  "Go forward": { act: "step", dir: 1, n: 1 },
  "walk forward three steps": { act: "step", dir: 1, n: 3 },
  "go back": { act: "step", dir: -1, n: 1 },
  "turn left": { act: "turn", deg: -90 },
  "Right.": { act: "turn", deg: 90 },
  "a little left": { act: "turn", deg: -30 },
  "turn around": { act: "turn", deg: 180 },
  "Where am I?": { act: "where" },
  "what's around me": { act: "where" },
  "tell me more": { act: "more" },
  "describe this place": { act: "more" },
  "play it": { act: "play" },
  "what did Theo say": { act: "play" },
  "stop": { act: "quiet" },
  "be quiet": { act: "quiet" },
  "record a memory": { act: "record" },
  "leave a note here": { act: "record" },
  "stop recording": { act: "stopRecording" },
  "keep it private": { act: "answer", choice: "private" },
  "decide later": { act: "answer", choice: "later" },
  "help": { act: "help" },
  "say that again": { act: "repeat" },
  "take off the blindfold": { act: "unblind" },
  "go back to the gallery": { act: "gallery" },
};
for (const [phrase, want] of Object.entries(cases)) {
  test(`"${phrase}"`, () => assert.deepStrictEqual(parse(phrase), want));
}
test("chatter that isn't a command does nothing", () => {
  for (const s of ["is the light on", "what's on my left", "hello there", "it's nice here", ""]) {
    const c = parse(s);
    assert.ok(c === null || !["step", "turn", "go-to"].includes(c.act), `${s} -> ${JSON.stringify(c)}`);
  }
});
