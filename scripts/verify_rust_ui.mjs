// No browser, network, real clipboard or third-party test dependencies.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ui = new URL("../crates/app/ui/", import.meta.url);
for (const file of readdirSync(ui).filter((name) => name.endsWith(".js"))) {
  new vm.Script(readFileSync(new URL(file, ui), "utf8"), { filename: file });
}

class Node {
  children = [];
  listeners = {};
  value = "";
  textContent = "";
  classList = { add() {}, remove() {}, toggle() {} };
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll() { return []; }
  insertBefore(child) { this.append(child); }
  replaceChildren(...children) { this.children = children; }
  remove() {}
}

function environment() {
  const nodes = new Map();
  const calls = [];
  const toasts = [];
  const timers = [];
  let answer = () => null;
  const context = vm.createContext({
    Node, console, setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout() {},
    document: {
      createElement: () => new Node(),
      createTextNode: (text) => Object.assign(new Node(), { textContent: text }),
      querySelector: (key) => {
        if (key.includes("[data-run=")) return null;
        if (!nodes.has(key)) nodes.set(key, new Node());
        return nodes.get(key);
      },
      querySelectorAll: () => [], addEventListener() {},
    },
    localStorage: { getItem: () => null, setItem() {} },
    window: {
      addEventListener() {}, confirm: () => false,
      __TAURI__: {
        core: { invoke: async (command, args) => { calls.push({ command, args }); return answer(command, args); } },
        event: { listen() {} },
      },
    },
  });
  const load = (name, subject) => {
    vm.runInContext(readFileSync(new URL(name, ui), "utf8") +
      (subject ? `\nglobalThis.subject = ${subject};` : ""), context, { filename: fileURLToPath(new URL(name, ui)) });
    return context.subject;
  };
  load("app.js");
  context.toast = (message, options = {}) => toasts.push({ message, ...options });
  return { context, calls, toasts, nodes, load, answer: (fn) => { answer = fn; } };
}

{
  const env = environment();
  const history = env.load("history.js", "History");
  let reloads = 0;
  history.load = () => reloads++;
  let token = 0;
  env.answer((command) => {
    if (command === "history_delete") return ++token;
    if (command === "history_undo") throw new Error("synthetic locked database");
    return null;
  });
  await history.remove({ id: 1, created: "first" });
  await history.remove({ id: 2, created: "second" });
  await env.toasts[0].onAction();
  const undo = env.calls.find((c) => c.command === "history_undo");
  assert.equal(undo.args.deletion, 1, "old toast must not address newest deletion");
  assert.equal(env.toasts[0].timeout, 30000);
  assert.equal(reloads, 3, "reload even when undo reports partial/failing result");
}
{
  const env = environment();
  const page = env.load("settings.js", "SettingsPage");
  page.collect = () => ({ max_tokens: 512 });
  page.needsRecovery = true;
  await page.save();
  assert.equal(env.calls.length, 0, "recovery requires explicit confirmation");
  env.context.window.confirm = () => true;
  await page.save();
  assert.equal(env.calls[0].command, "settings_save");
  assert.equal(env.calls[0].args.recover, true);
}
{
  const env = environment();
  const result = env.load("result.js", "Result");
  result.session = { capture: 5, runs: [] };
  result.textModel = "synthetic";
  env.answer(() => { throw new Error("synthetic clipboard failure"); });
  await result.copy();
  assert.equal(env.calls[0].command, "use_text");
  assert(!env.toasts.some((t) => t.message === "Скопировано и сохранено в истории"));
  const group = new Node();
  result.roundNode = () => group;
  result.renderChanges = async () => {};
  await result.renderRun({
    id: 1, round: 1, kind: "ocr", state: "done", model: "m", provider: "local",
    label: "<not markup>", elapsedMs: 1250, text: "a,b", warning: "literal fallback",
  });
  const content = (node) => typeof node === "string" ? node :
    `${node.textContent ?? ""} ${(node.children ?? []).map(content).join(" ")}`;
  const rendered = content(group);
  assert(rendered.includes("запрос модели 1.3 с"));
  assert(rendered.includes("literal fallback"));
  assert(rendered.includes("<not markup>"));
}
console.log("Rust UI: syntax, deletion identity, recovery, error reporting and timing labels passed");
