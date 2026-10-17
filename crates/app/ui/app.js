// Shared by every page: Tauri calls, navigation, the status panel and toasts.
// Pages register themselves in `App.pages`; data arrives as events from the
// Rust side (see src/model.rs).
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const $ = (selector, root = document) => root.querySelector(selector);

/// Creates an element: el("div", { class: "x", onclick }, child, "text").
function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function toast(message, { error = false, action, onAction, timeout = 4000 } = {}) {
  const node = el("div", { class: error ? "toast error" : "toast" }, el("span", {}, message));
  if (action) {
    node.append(el("button", { onclick: () => { node.remove(); onAction(); } }, action));
  }
  $("#toasts").append(node);
  setTimeout(() => node.remove(), action ? Math.max(timeout, 8000) : timeout);
}

/// Runs a command; failures become an error toast and resolve to `undefined`.
async function call(command, args) {
  try {
    return await invoke(command, args);
  } catch (error) {
    toast(String(error), { error: true });
    return undefined;
  }
}

// The models the result window can run: installed local ones and, with a
// key, the starred OpenRouter models plus the ones already given a role.
function modelChoices(library, catalog, view) {
  const names = new Map(catalog.models.map((m) => [m.id, m.name]));
  const local = library.models.filter((m) => m.installed)
    .map((m) => ({ provider: "local", model: m.id, label: m.label.replace(/ · .*$/, ""), reviews: m.reviews }));
  const picked = view.reviewModels.filter((m) => m.provider === "openrouter").map((m) => m.model);
  const ids = [...new Set([...catalog.favorites, ...picked, view.settings.remote_model])].filter(Boolean);
  const remote = view.hasKey
    ? ids.map((id) => ({ provider: "openrouter", model: id, label: names.get(id) || id, reviews: true }))
    : [];
  return [...local, ...remote];
}

const choiceKey = (choice) => `${choice.provider}:${choice.model}`;

function formatSize(bytes) {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} ГБ` : `${Math.round(bytes / 1e6)} МБ`;
}

function formatSeconds(ms) {
  return `${(ms / 1000).toFixed(1)} с`;
}

const App = {
  pages: {},
  page: "result",
  status: { phase: "idle", message: "Готово", error: false },

  show(name) {
    this.page = name;
    for (const button of document.querySelectorAll("#nav button")) {
      button.classList.toggle("active", button.dataset.page === name);
    }
    for (const section of document.querySelectorAll(".page")) {
      section.classList.toggle("active", section.id === `page-${name}`);
    }
    this.pages[name]?.shown?.();
  },

  showStatus(value) {
    this.status = value;
    const node = $("#status");
    $(".text", node).textContent = value.message;
    node.classList.toggle("error", value.error);
    node.dataset.phase = value.phase;
    for (const page of Object.values(this.pages)) page.status?.(value);
  },

  showHotkeys([capture, type, cancel]) {
    const row = (label, keys) => el("div", {}, `${label} `, el("kbd", {}, keys));
    $("#hotkeys").replaceChildren(row("Распознать", capture), row("Напечатать", type), row("Остановить", cancel));
  },
};

for (const button of document.querySelectorAll("#nav button")) {
  button.addEventListener("click", () => App.show(button.dataset.page));
}
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (button) invoke("run_action", { action: button.dataset.action });
});

listen("status", (event) => App.showStatus(event.payload));
listen("typing", ({ payload }) => {
  const bar = $("#typing");
  bar.hidden = payload.state === "done" || payload.state === "stopped";
  bar.max = Math.max(payload.total, 1);
  bar.value = payload.done;
});
listen("changed", async () => {
  const snapshot = await invoke("snapshot");
  App.showHotkeys(snapshot.hotkeys);
  for (const page of Object.values(App.pages)) page.changed?.();
});

window.addEventListener("DOMContentLoaded", async () => {
  const snapshot = await invoke("snapshot");
  App.showHotkeys(snapshot.hotkeys);
  for (const page of Object.values(App.pages)) await page.init?.(snapshot);
  App.showStatus(snapshot.status);
});
