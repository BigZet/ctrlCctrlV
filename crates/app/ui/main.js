// Main window: status, the last OCR answer and the actions. Everything it
// shows arrives as events from the Rust side (see src/model.rs).
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const status = document.getElementById("status");
const typing = document.getElementById("typing");
const result = document.getElementById("result");
const meta = document.getElementById("meta");
const copy = document.getElementById("copy");

function showStatus(value) {
  status.textContent = value.message;
  status.classList.toggle("error", value.error);
  if (value.phase !== "typing") typing.hidden = true;
}

function showRun(run) {
  const seconds = (run.elapsedMs / 1000).toFixed(1);
  meta.textContent = `${run.provider === "local" ? "Локально" : "OpenRouter"} · ${run.model} · ${seconds} с`;
  result.value = run.text;
  copy.disabled = false;
}

for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => invoke("run_action", { action: button.dataset.action }));
}

copy.addEventListener("click", async () => {
  try {
    await invoke("copy_text", { text: result.value });
    showStatus({ phase: "idle", message: "Текст скопирован", error: false });
  } catch (error) {
    showStatus({ phase: "idle", message: String(error), error: true });
  }
});

listen("status", (event) => showStatus(event.payload));
listen("ocr-result", (event) => showRun(event.payload));
listen("typing-progress", ({ payload }) => {
  typing.hidden = false;
  typing.max = payload.total;
  typing.value = payload.done;
});

invoke("snapshot").then((snapshot) => {
  showStatus(snapshot.status);
  const last = snapshot.runs[snapshot.runs.length - 1];
  if (last) showRun(last);
  const [capture, type, cancel] = snapshot.hotkeys;
  document.getElementById("hotkeys").textContent =
    `Распознать: ${capture} · Напечатать буфер: ${type} · Остановить: ${cancel}`;
});
