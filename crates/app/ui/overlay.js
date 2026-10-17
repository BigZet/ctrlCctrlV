// Region selection. Coordinates are CSS pixels of this window, which covers
// one monitor, so they are the monitor's logical pixels from its top-left.
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const box = document.getElementById("box");
const size = document.getElementById("size");
let start = null;
let pointer = null;

function reset() {
  start = null;
  box.hidden = true;
  document.body.classList.remove("dragging");
}

function region(event) {
  const x = Math.min(start.x, event.clientX);
  const y = Math.min(start.y, event.clientY);
  return { x, y, width: Math.abs(event.clientX - start.x), height: Math.abs(event.clientY - start.y) };
}

listen("overlay-reset", reset);

addEventListener("pointerdown", (event) => {
  if (event.button !== 0) {
    reset();
    invoke("selection_cancelled");
    return;
  }
  start = { x: event.clientX, y: event.clientY };
  pointer = event.pointerId;
  document.documentElement.setPointerCapture(pointer);
});

addEventListener("pointermove", (event) => {
  if (!start) return;
  const r = region(event);
  document.body.classList.add("dragging");
  box.hidden = false;
  Object.assign(box.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.width}px`, height: `${r.height}px` });
  size.textContent = `${Math.round(r.width)} × ${Math.round(r.height)}`;
});

addEventListener("pointerup", (event) => {
  if (!start) return;
  const r = region(event);
  reset();
  // A click without a drag keeps the selection open.
  if (r.width < 3 || r.height < 3) return;
  invoke("selection_finished", r);
});

addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    reset();
    invoke("selection_cancelled");
  }
});

addEventListener("contextmenu", (event) => event.preventDefault());
