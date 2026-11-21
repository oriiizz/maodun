import assert from "node:assert/strict";
import test from "node:test";

import { createSubmitGate, finishUtterance, interpretRecognition } from "../web/asr_flow.js";
import { createFrameWriter } from "../web/speech.js";

test("one sentence followed by trailing silence is asked once", () => {
  const claim = createSubmitGate();
  const decision = interpretRecognition({
    raw_text: "请谈谈《子夜》。",
    corrected_text: "请谈谈《子夜》。",
    submit: true,
    reason: "ok",
  });
  assert.equal(decision.action, "ask");
  assert.equal(decision.question, "请谈谈《子夜》。");
  assert.equal(claim(), true);
  assert.equal(claim(), false);
});

test("several final sentences stay in order and submit once", () => {
  const decision = interpretRecognition({
    corrected_text: "第一句。第二句。",
    submit: true,
    reason: "ok",
  });
  assert.equal(decision.action, "ask");
  assert.equal(decision.question, "第一句。第二句。");
});

test("no final text asks the user to retry and does not submit", () => {
  const decision = interpretRecognition({
    raw_text: "",
    corrected_text: "",
    submit: false,
    reason: "no_speech",
  });
  assert.equal(decision.action, "retry");
  assert.equal(decision.message, "没有识别到有效语音，请重试");
  assert.equal(decision.question, "");
});

test("interim text is not treated as a final question", () => {
  const decision = interpretRecognition({ raw_text: "", corrected_text: "", submit: false, reason: "no_speech" });
  assert.equal(decision.action, "retry");
});

test("a mid-stream hole, timeout, or dropped connection still asks with the heard sentence", () => {
  for (const reason of ["incomplete", "timeout", "interrupted"]) {
    const decision = interpretRecognition({
      corrected_text: "已经听到的句子。",
      submit: false,
      reason,
    });
    assert.equal(decision.action, "ask");
    assert.equal(decision.question, "已经听到的句子。");
    assert.equal(decision.message, "");
  }
});

test("automatic submit happens once, so a later callback cannot send again", () => {
  const claim = createSubmitGate();
  assert.equal(claim(), true);
  assert.equal(claim(), false);
});

test("ending sends the unsent tail and then FINISH, without padding silence", () => {
  const frames = [];
  const writer = createFrameWriter(4, (frame) => frames.push(new Uint8Array(frame)));
  writer.push(new Uint8Array([1, 2, 3, 4, 5]));
  const tail = writer.flush();
  assert.deepEqual(frames, [new Uint8Array([1, 2, 3, 4])]);
  assert.deepEqual(new Uint8Array(tail), new Uint8Array([5]));

  const sent = [];
  finishUtterance({
    tail,
    sendAudio: (frame) => sent.push(new Uint8Array(frame)),
    sendFinish: () => sent.push("FINISH"),
  });
  assert.deepEqual(sent[0], new Uint8Array([5]));
  assert.equal(sent[1], "FINISH");
  assert.equal(sent.length, 2);
});
