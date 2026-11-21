/**
 * 中央展示。现在画抽象状态，以后换成 2D 人物时替换这个文件里的 createPresenceStage。
 * 调用方继续传入 mode、inputLevel、samples、playback 和 turnId。
 */
export function createPresenceStage(root) {
  const canvas = document.createElement("canvas");
  canvas.className = "stage-canvas";
  canvas.width = 640;
  canvas.height = 640;
  root.replaceChildren(canvas);
  const context = canvas.getContext("2d");
  let latest = { mode: "idle", samples: null, inputLevel: 0, playback: { active: false, progress: 0 }, turnId: "" };

  function render(next) {
    latest = { ...latest, ...next, playback: { ...latest.playback, ...(next.playback || {}) } };
    root.dataset.mode = latest.mode;
    root.dataset.turnId = latest.turnId || "";
    root.dataset.speaking = latest.mode === "responding" && latest.playback.active ? "true" : "false";
    draw(latest.mode === "listening", root.dataset.speaking === "true");
  }

  function draw(listening, speaking) {
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    if (!listening && !speaking) return;
    const samples = latest.samples;
    const bars = 48;
    const center = width / 2;
    const inner = 188;
    for (let index = 0; index < bars; index += 1) {
      const level = samples ? sliceLevel(samples, index, bars) : latest.inputLevel || 0;
      const amount = Math.max(0.08, Math.min(1, level));
      const length = 10 + amount * 58;
      const angle = (index / bars) * Math.PI * 2 - Math.PI / 2;
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      context.strokeStyle = speaking ? "rgba(110, 110, 115, 0.8)" : "rgba(0, 113, 227, 0.72)";
      context.lineWidth = 5;
      context.lineCap = "round";
      context.beginPath();
      context.moveTo(center + cos * inner, center + sin * inner);
      context.lineTo(center + cos * (inner + length), center + sin * (inner + length));
      context.stroke();
    }
  }

  return { render };
}

function sliceLevel(samples, index, bars) {
  const size = Math.floor(samples.length / bars) || 1;
  const start = index * size;
  let sum = 0;
  for (let offset = 0; offset < size && start + offset < samples.length; offset += 1) {
    const value = samples[start + offset];
    sum += value * value;
  }
  return Math.min(1, Math.sqrt(sum / size) * 3.2);
}
