/** 把一轮百度识别结果分成：作为一句对话发出，或提示重试。有文字就直接问，不放进输入框。 */

const RETRY_MESSAGE = "没有识别到有效语音，请重试";

export function interpretRecognition(message) {
  const question = String(message?.corrected_text || message?.raw_text || "").trim();
  if (!question || message?.reason === "no_speech") {
    return { action: "retry", message: RETRY_MESSAGE, question: "" };
  }
  return { action: "ask", question, message: "" };
}

export function createSubmitGate() {
  let used = false;
  return function claim() {
    if (used) return false;
    used = true;
    return true;
  };
}

export function finishUtterance({ tail, sendAudio, sendFinish }) {
  const view = tail instanceof ArrayBuffer ? new Uint8Array(tail) : tail;
  if (view && view.byteLength) sendAudio(view);
  sendFinish();
}
