// The history page: recent and pinned entries, search, delete with undo.
const History = {
  entries: [],
  selected: null,
  timer: null,

  init() {
    $("#h-search").addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.load(), 200);
    });
  },

  shown() {
    this.load();
  },

  async load() {
    const entries = await call("history_list", { search: $("#h-search").value });
    if (!entries) return;
    this.entries = entries;
    const key = (e) => `${e.id}|${e.created}`;
    if (this.selected && !entries.some((e) => key(e) === key(this.selected))) this.selected = null;
    else if (this.selected) this.selected = entries.find((e) => key(e) === key(this.selected));
    this.render();
  },

  day(created) {
    const date = new Date(created);
    const today = new Date();
    const yesterday = new Date(Date.now() - 86400000);
    if (date.toDateString() === today.toDateString()) return "Сегодня";
    if (date.toDateString() === yesterday.toDateString()) return "Вчера";
    return date.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
  },

  render() {
    const nodes = [];
    let group = null;
    for (const entry of this.entries) {
      const label = entry.pinned ? "Закреплённые" : this.day(entry.created);
      if (label !== group) {
        group = label;
        nodes.push(el("li", { class: "day" }, label));
      }
      const time = new Date(entry.created).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      const active = this.selected && this.selected.id === entry.id && this.selected.created === entry.created;
      nodes.push(el("li", { class: active ? "entry active" : "entry", onclick: () => { this.selected = entry; this.render(); } },
        el("div", { class: "preview" }, entry.text.split("\n").slice(0, 2).join("\n")),
        el("div", { class: "meta" },
          entry.pinned ? "📌" : null,
          el("span", {}, entry.kind === "ocr" ? "Распознавание" : "Печать"),
          el("span", {}, time),
          entry.model ? el("span", {}, `· ${entry.model}`) : null,
          entry.runs > 1 ? el("span", {}, `· ${entry.runs} ответа`) : null,
          entry.status !== "completed" ? el("span", {}, `· ${entry.status === "cancelled" ? "отменено" : "ошибка"}`) : null)));
    }
    if (nodes.length === 0) {
      nodes.push(el("li", { class: "muted", style: "padding: 12px" }, $("#h-search").value ? "Ничего не найдено" : "История пуста"));
    }
    $("#h-list").replaceChildren(...nodes);
    this.renderDetail();
  },

  renderDetail() {
    const entry = this.selected;
    if (!entry) {
      $("#h-detail").replaceChildren(el("p", { class: "muted" }, "Выберите запись"));
      return;
    }
    const created = new Date(entry.created).toLocaleString("ru-RU");
    const args = { id: entry.id, created: entry.created };
    $("#h-detail").replaceChildren(
      el("div", { class: "row" },
        el("b", {}, entry.kind === "ocr" ? "Распознавание" : "Печать"),
        el("span", { class: "muted" }, `${created}${entry.model ? ` · ${entry.model}` : ""}`)),
      el("pre", {}, entry.text),
      el("div", { class: "row" },
        el("button", { class: "primary", onclick: () => call("copy_text", { text: entry.text }).then(() => toast("Скопировано")) }, "Скопировать"),
        entry.kind === "ocr"
          ? el("button", { onclick: () => call("history_open", args) }, entry.runs > 1 ? "Открыть ответы моделей" : "Открыть в редакторе")
          : null,
        el("button", { onclick: async () => {
          if ((await call("history_pin", { ...args, pinned: !entry.pinned })) !== undefined) this.load();
        } }, entry.pinned ? "Открепить" : "Закрепить"),
        el("span", { class: "spacer" }),
        el("button", { class: "ghost danger", onclick: () => this.remove(args) }, "Удалить")));
  },

  async remove(args) {
    if ((await call("history_delete", args)) === undefined) return;
    this.selected = null;
    this.load();
    toast("Запись удалена", { action: "Вернуть", onAction: async () => {
      if ((await call("history_undo")) !== undefined) this.load();
    } });
  },
};

App.pages.history = History;
