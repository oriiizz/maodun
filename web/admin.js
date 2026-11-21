const statusBox = document.getElementById("status");
const resultBox = document.getElementById("import-result");

document.getElementById("import-demo").addEventListener("click", () => send("/api/import/demo", {}));
document.getElementById("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = document.getElementById("upload-file").files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  resultBox.textContent = "正在导入并重建索引……";
  try {
    const response = await fetch("/api/import", { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "导入失败");
    showImport(payload);
    loadStatus();
  } catch (error) {
    resultBox.textContent = error.message;
  }
});
document.getElementById("path-form").addEventListener("submit", (event) => {
  event.preventDefault();
  send("/api/import/path", { path: document.getElementById("excel-path").value.trim() });
});

async function send(url, payload) {
  resultBox.textContent = "正在导入并重建索引……";
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "导入失败");
    showImport(data);
    loadStatus();
  } catch (error) {
    resultBox.textContent = error.message;
  }
}

function showImport(payload) {
  const issues = payload.issues || [];
  const failures = issues.filter((item) => item.severity === "error");
  const lines = [
    payload.message || "",
    payload.index_message || "",
    `失败 ${payload.errors ?? failures.length} 条。`,
  ];
  failures.slice(0, 12).forEach((item) => {
    lines.push(`第 ${item.row_number || "—"} 行：${item.message}`);
  });
  resultBox.replaceChildren();
  lines.filter(Boolean).forEach((line) => {
    const paragraph = document.createElement("p");
    paragraph.textContent = line;
    resultBox.append(paragraph);
  });
}

async function loadStatus() {
  const response = await fetch("/api/status");
  const data = await response.json();
  statusBox.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = "当前连接";
  statusBox.append(title);
  [
    ["资料", data.knowledge?.message],
    ["规模", `来源 ${data.counts?.sources ?? 0} 份，条目 ${data.counts?.entries ?? 0} 条`],
    ["检索", data.retriever?.message],
    ["问答模型", data.qa?.message],
    ["语音识别", data.asr?.message],
    ["语音合成", data.tts?.message],
    ["识别方式", "点击开始，再点击结束。识别在录音完成之后进行，不是实时转写。"],
  ].forEach(([name, text]) => {
    const paragraph = document.createElement("p");
    paragraph.textContent = `${name}：${text || "—"}`;
    statusBox.append(paragraph);
  });
}

loadStatus();
