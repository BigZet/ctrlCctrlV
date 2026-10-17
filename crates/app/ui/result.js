// The result window: the capture, the editable text and every model's answer.
// The window stays open: more models can recognize the image again or check
// the edited text, and each answer shows how it differs from the current text.
const Result = {
  session: null,
  runs: new Map(),
  /// The run whose streamed text fills the editor until the user edits it.
  follow: null,
  original: "",
  /// Who produced the text in the editor, stored in history with it.
  textModel: "",
  kind: localStorage.getItem("recheck-kind") || "review",
  /// Keys of the models checked by default, kept in the app's ui-state.
  picked: new Set(),
  choices: [],
  settings: null,
  busy: false,
  diffTimer: null,
  tokens: new Map(),

  async init(snapshot) {
    const text = $("#r-text");
    text.addEventListener("input", () => {
      this.follow = null;
      this.refreshEditor();
      this.scheduleDiffs();
    });
    text.addEventListener("scroll", () => { $("#r-gutter").scrollTop = text.scrollTop; });
    text.addEventListener("keydown", (event) => {
      // Tab indents instead of leaving the editor.
      if (event.key === "Tab" && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        document.execCommand("insertText", false, "\t");
      }
    });
    $("#r-copy").addEventListener("click", () => this.copy());
    $("#r-revert").addEventListener("click", () => this.setText(this.original, this.session?.runs[0]?.model || ""));
    for (const button of document.querySelectorAll("#r-kind button")) {
      button.addEventListener("click", () => this.setKind(button.dataset.kind));
    }
    $("#r-run").addEventListener("click", () => this.run());
    $("#r-stop").addEventListener("click", () => invoke("run_action", { action: "cancel" }));
    $("#r-compare").addEventListener("click", () => this.openCompare());
    $("#r-compare-close").addEventListener("click", () => { $("#r-compare-view").hidden = true; });
    $("#r-models-hint button").addEventListener("click", () => App.show("models"));
    listen("ocr-session", ({ payload }) => this.open(payload));
    listen("ocr-run", ({ payload }) => this.update(payload));
    this.setKind(this.kind);
    await this.loadChoices();
    if (snapshot.session) this.open(snapshot.session);
    else this.showEmpty();
  },

  changed() {
    this.loadChoices();
  },

  status(value) {
    this.busy = value.phase !== "idle";
    $("#r-stop").hidden = value.phase !== "recognizing";
    this.refreshRunButton();
  },

  showEmpty() {
    $("#r-empty").hidden = false;
    $("#r-grid").hidden = true;
    const [capture] = [...document.querySelectorAll("#hotkeys kbd")].map((k) => k.textContent);
    $("#r-empty-hint").textContent = capture ? `Сочетание: ${capture}` : "";
  },

  open(session) {
    App.show("result");
    $("#r-compare-view").hidden = true;
    this.session = session;
    this.runs = new Map(session.runs.map((run) => [run.id, run]));
    const first = session.runs[0];
    const streaming = first && (first.state === "running" || first.state === "waiting");
    this.follow = streaming ? first.id : null;
    this.original = first?.state === "done" ? first.text : "";
    $("#r-empty").hidden = true;
    $("#r-grid").hidden = false;
    $("#r-shot").hidden = !session.image;
    if (session.image) $("#r-image").src = session.image;
    this.setText(session.text, first?.model || "", { keepFollow: true });
    $("#r-runs").replaceChildren();
    for (const run of session.runs) this.renderRun(run);
    this.refreshRunButton();
  },

  setText(text, model, { keepFollow = false } = {}) {
    if (!keepFollow) this.follow = null;
    $("#r-text").value = text;
    this.textModel = model;
    this.refreshEditor();
    this.scheduleDiffs(0);
  },

  refreshEditor() {
    const text = $("#r-text").value;
    const lines = text.split("\n").length;
    $("#r-gutter").textContent = Array.from({ length: lines }, (_, i) => i + 1).join("\n");
    $("#r-gutter").scrollTop = $("#r-text").scrollTop;
    $("#r-count").textContent = text ? `${lines} стр. · ${[...text].length} симв.` : "";
    const first = this.session?.runs[0];
    $("#r-meta").textContent = first ? `${first.label} · ${first.provider === "local" ? "локально" : "OpenRouter"}` : "";
  },

  update(run) {
    if (!this.session || run.capture !== this.session.capture) return;
    const known = this.runs.has(run.id);
    this.runs.set(run.id, run);
    if (!known) this.session.runs.push(run);
    else this.session.runs[this.session.runs.findIndex((r) => r.id === run.id)] = run;
    if (this.follow === run.id) {
      $("#r-text").value = run.text;
      this.textModel = run.model;
      this.refreshEditor();
      if (run.state !== "running" && run.state !== "waiting") {
        this.follow = null;
        if (run.state === "done") this.original = run.text;
      }
    }
    this.renderRun(run);
  },

  scheduleDiffs(delay = 300) {
    clearTimeout(this.diffTimer);
    this.diffTimer = setTimeout(() => {
      for (const run of this.runs.values()) if (run.state === "done") this.renderRun(run);
    }, delay);
  },

  // Rounds and run cards.
  roundNode(round) {
    let group = $(`#r-runs [data-round="${round}"]`);
    if (group) return group;
    group = el("div", { class: "round", "data-round": round },
      el("div", { class: "round-title" }, round === 1 ? "Первое распознавание" : `Раунд ${round}`));
    // Newest rounds on top.
    const later = [...document.querySelectorAll("#r-runs .round")].find((g) => Number(g.dataset.round) < round);
    $("#r-runs").insertBefore(group, later || null);
    return group;
  },

  async renderRun(run) {
    // Renders can overlap while a diff is computed; only the newest one lands.
    const token = (this.tokens.get(run.id) || 0) + 1;
    this.tokens.set(run.id, token);
    const group = this.roundNode(run.round);
    const kind = run.kind === "review" ? "проверка" : "распознавание";
    const where = run.provider === "local" ? "Локально" : run.provider === "openrouter" ? "Облако" : "История";
    const details = [kind, run.elapsedMs ? formatSeconds(run.elapsedMs) : null].filter(Boolean).join(" · ");
    const card = el("article", { class: `run ${run.state}`, "data-run": run.id },
      el("header", {},
        el("span", { class: "dot" }),
        el("b", { title: run.model }, run.label),
        el("span", { class: "badge" }, where),
        el("span", { class: "spacer" }),
        el("span", { class: "muted small" }, details)));
    const body = el("div", { class: "body" });
    card.append(body);
    if (run.state === "waiting") body.append(el("span", { class: "muted" }, "Ждёт своей очереди"));
    else if (run.state === "running") body.append(el("pre", { class: "stream" }, run.text));
    else if (run.state === "failed") body.append(el("div", { class: "error" }, run.error || "Ошибка"));
    else if (run.state === "cancelled") body.append(el("div", { class: "muted" }, "Остановлено"));
    else await this.renderChanges(run, body, card);
    if (this.tokens.get(run.id) !== token) return;
    const existing = $(`#r-runs [data-run="${run.id}"]`);
    if (existing) existing.replaceWith(card);
    else {
      // Keep the order the runs were started in.
      const next = [...group.querySelectorAll(".run")].find((node) => Number(node.dataset.run) > run.id);
      group.insertBefore(card, next || null);
    }
    $("#r-compare").disabled = this.doneRuns().length === 0;
  },

  async renderChanges(run, body, card) {
    const base = $("#r-text").value;
    const changes = (await call("compare", { base, proposed: run.text })) || [];
    if (changes.length === 0) {
      body.append(el("div", { class: "same" }, run.text ? "✓ Совпадает с текущим текстом" : "Модель не нашла текст"));
      return;
    }
    body.append(el("div", { class: "muted small" }, `Отличий от текущего текста: ${changes.length}`));
    const shown = changes.slice(0, 40);
    const boxes = shown.map((change, index) => {
      const box = el("input", { type: "checkbox", checked: true, "data-index": index });
      const range = change.end - change.start <= 1 ? `Строка ${change.start + 1}` : `Строки ${change.start + 1}–${change.end}`;
      body.append(el("label", { class: "change" }, box,
        el("span", { class: "where" }, `${range} · ${change.label}`),
        this.changeLines(change)));
      return box;
    });
    if (changes.length > shown.length) {
      body.append(el("div", { class: "more" }, `И ещё ${changes.length - shown.length}: примените весь ответ или сравните рядом`));
    }
    const apply = el("button", { class: "primary", onclick: async () => {
      if ($("#r-text").value !== base) return this.renderRun(run);
      const selected = boxes.filter((box) => box.checked).map((box) => Number(box.dataset.index));
      const text = await call("apply_changes", { base, proposed: run.text, selected });
      if (text !== undefined) this.setText(text, run.model);
    } }, "Применить отмеченные");
    for (const box of boxes) {
      box.addEventListener("change", () => {
        const count = boxes.filter((b) => b.checked).length;
        apply.disabled = count === 0;
        apply.textContent = count === boxes.length ? "Применить отмеченные" : `Применить отмеченные (${count})`;
      });
    }
    card.append(el("footer", {},
      apply,
      el("button", { onclick: () => this.setText(run.text, run.model) }, "Взять весь ответ"),
      el("span", { class: "spacer" }),
      el("button", { class: "ghost", title: "Скопировать ответ этой модели", onclick: () => call("copy_text", { text: run.text }).then(() => toast("Ответ скопирован")) }, "Копировать")));
  },

  /// The old and new side of a change, with character marks when available.
  changeLines(change) {
    const block = (cls, lines, segments) => {
      const node = el("div", { class: `line ${cls}` });
      if (segments) {
        const parts = segments.map((s) => ({ ...s, text: s.text }));
        const last = parts[parts.length - 1];
        if (last) last.text = last.text.replace(/\r?\n$/, "");
        for (const part of parts) node.append(part.changed ? el("mark", {}, part.text) : part.text);
      } else {
        node.textContent = lines.join("").replace(/\r?\n$/, "");
      }
      return node;
    };
    const lines = el("div", { class: "lines" });
    if (change.old.length) lines.append(block("del", change.old, change.inline?.[0]));
    if (change.new.length) lines.append(block("ins", change.new, change.inline?.[1]));
    return lines;
  },

  doneRuns() {
    return [...this.runs.values()].filter((run) => run.state === "done" && run.text);
  },

  async openCompare() {
    const base = $("#r-text").value;
    const runs = this.doneRuns().slice(-6);
    const results = await Promise.all(runs.map((run) => call("compare", { base, proposed: run.text })));
    const touched = new Set();
    const columns = runs.map((run, index) => {
      const changes = results[index] || [];
      const marked = new Map();
      let offset = 0;
      for (const change of changes) {
        for (let line = change.start; line < Math.max(change.end, change.start + 1); line++) touched.add(line);
        const single = change.old.length === 1 && change.new.length === 1 && change.inline;
        change.new.forEach((_, i) => marked.set(change.start + offset + i, single ? change.inline[1] : true));
        offset += change.new.length - (change.end - change.start);
      }
      const text = el("div", { class: "text" });
      run.text.replace(/\n$/, "").split("\n").forEach((line, i) => {
        const mark = marked.get(i);
        const node = el("div", { class: mark ? "line ins" : "line" });
        if (Array.isArray(mark)) {
          for (const part of mark) {
            const value = part.text.replace(/\r?\n$/, "");
            node.append(part.changed ? el("mark", {}, value) : value);
          }
        } else {
          node.textContent = line;
        }
        text.append(node);
      });
      return el("div", { class: "column" },
        el("header", {}, el("b", { title: run.model }, run.label),
          el("span", { class: "muted small" }, changes.length ? `${changes.length} отл.` : "совпадает"),
          el("button", { class: "icon", title: "Взять этот ответ", onclick: () => {
            this.setText(run.text, run.model);
            $("#r-compare-view").hidden = true;
          } }, "Взять")),
        text);
    });
    const current = el("div", { class: "text" },
      base.replace(/\n$/, "").split("\n").map((line, i) => el("div", { class: touched.has(i) ? "line touched" : "line" }, line)));
    $("#r-columns").replaceChildren(
      el("div", { class: "column" }, el("header", {}, el("b", {}, "Текущий текст")), current), ...columns);
    $("#r-compare-view").hidden = false;
  },

  // Re-checks.
  setKind(kind) {
    this.kind = kind;
    localStorage.setItem("recheck-kind", kind);
    for (const button of document.querySelectorAll("#r-kind button")) {
      button.classList.toggle("active", button.dataset.kind === kind);
    }
    $("#r-kind-hint").textContent = kind === "review"
      ? "Модели сверят текущий текст со снимком и предложат исправления. Облачные работают параллельно, локальные по очереди."
      : "Модели распознают снимок с нуля, чтобы сравнить их ответы.";
    this.renderChoices();
  },

  async loadChoices() {
    const [library, catalog, view] = await Promise.all([call("models_list"), call("catalog_list"), call("settings_get")]);
    if (!library || !catalog || !view) return;
    this.settings = view.settings;
    this.choices = modelChoices(library, catalog, view);
    this.picked = new Set(view.reviewModels.map(choiceKey));
    this.renderChoices();
  },

  renderChoices() {
    const key = choiceKey;
    const nodes = this.choices.map((choice) => {
      const usable = this.kind === "ocr" || choice.reviews;
      const on = usable && this.picked.has(key(choice));
      return el("button", {
        class: on ? "chip on" : "chip",
        disabled: !usable,
        title: usable ? choice.model : "Эта модель только распознаёт, проверять текст она не умеет",
        onclick: () => this.togglePicked(choice),
      }, on ? "✓ " : "", choice.label,
        el("span", { class: "where" }, choice.provider === "local" ? "лок" : "облако"),
        usable ? null : el("span", { class: "where" }, "только OCR"));
    });
    if (nodes.length === 0) {
      nodes.push(el("span", { class: "muted small" },
        "Нет доступных моделей. Установите локальную или добавьте ключ и избранные модели OpenRouter в разделе «Модели»."));
    }
    $("#r-models").replaceChildren(...nodes);
    this.refreshRunButton();
  },

  /// The selection is the same list the models page edits, so it survives
  /// restarts and the next capture starts with it.
  async togglePicked(choice) {
    const key = choiceKey(choice);
    if (this.picked.has(key)) this.picked.delete(key);
    else this.picked.add(key);
    this.renderChoices();
    const models = this.choices.filter((c) => this.picked.has(choiceKey(c))).map(({ provider, model }) => ({ provider, model }));
    const saved = await call("review_models_set", { models });
    if (saved) {
      this.picked = new Set(saved.map(choiceKey));
      this.renderChoices();
    }
  },

  selectedChoices() {
    return this.choices
      .filter((c) => (this.kind === "ocr" || c.reviews) && this.picked.has(`${c.provider}:${c.model}`))
      .map(({ provider, model }) => ({ provider, model }));
  },

  refreshRunButton() {
    const button = $("#r-run");
    const count = this.selectedChoices().length;
    const image = Boolean(this.session?.image);
    button.disabled = this.busy || !image || count === 0;
    button.textContent = count > 1 ? `Запустить (${count})` : "Запустить";
    button.title = image ? "" : "Снимок не хранится в истории: перепроверка доступна для новых снимков";
  },

  async run() {
    if (!this.session) return;
    await call("recheck", {
      capture: this.session.capture,
      models: this.selectedChoices(),
      kind: this.kind,
      text: $("#r-text").value,
    });
  },

  async copy() {
    if (!this.session) return;
    const ok = await call("use_text", { capture: this.session.capture, text: $("#r-text").value, model: this.textModel });
    if (ok !== undefined) toast("Скопировано и сохранено в истории");
  },
};

App.pages.result = Result;
