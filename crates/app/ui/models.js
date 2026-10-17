// The models page: the only place models get their roles (the one behind the
// hotkey, the ones checked in the result window), local models with
// downloads, and the OpenRouter catalog.
const Models = {
  tab: "local",
  library: null,
  catalog: { models: [], favorites: [] },
  view: null,
  settings: null,
  progress: null,

  async init() {
    for (const button of document.querySelectorAll("#m-tabs button")) {
      button.addEventListener("click", () => this.setTab(button.dataset.tab));
    }
    $("#m-key-save").addEventListener("click", () => this.saveKey());
    $("#m-search").addEventListener("input", () => this.renderCatalog());
    $("#m-only-fav").addEventListener("change", () => this.renderCatalog());
    $("#m-refresh").addEventListener("click", () => this.refreshCatalog());
    listen("model-install", ({ payload }) => {
      this.progress = payload.finished ? null : payload;
      if (payload.finished) {
        toast(payload.error || `${payload.label} установлена`, { error: Boolean(payload.error) });
      }
      this.renderLocal();
    });
  },

  shown() {
    this.load();
  },

  changed() {
    if (App.page === "models") this.load();
  },

  setTab(tab) {
    this.tab = tab;
    for (const button of document.querySelectorAll("#m-tabs button")) {
      button.classList.toggle("active", button.dataset.tab === tab);
    }
    $("#m-local").hidden = tab !== "local";
    $("#m-remote").hidden = tab !== "remote";
    if (tab === "remote" && this.catalog.models.length === 0) this.refreshCatalog();
  },

  async load() {
    const [library, catalog, view] = await Promise.all([call("models_list"), call("catalog_list"), call("settings_get")]);
    if (library) this.library = library;
    if (catalog) this.catalog = catalog;
    if (view) {
      this.view = view;
      this.settings = view.settings;
      $("#m-key-state").textContent = view.hasKey
        ? "Ключ сохранён в системном хранилище. Чтобы удалить его, сохраните пустое поле."
        : "Ключ не сохранён. Он хранится в Credential Manager или Keychain, не в файлах.";
    }
    this.renderRoles();
    this.renderLocal();
    this.renderCatalog();
  },

  async useFor(field, value, extra = {}) {
    const settings = { ...this.settings, [field]: value, ...extra };
    if ((await call("settings_save", { settings })) !== undefined) toast("Настройки сохранены");
  },

  /// `local:id` or `openrouter:id` of the model behind the capture hotkey.
  hotkeyKey() {
    const s = this.settings;
    return s.provider === "openrouter" ? `openrouter:${s.remote_model}` : `local:${s.local_model}`;
  },

  pickedKeys() {
    return new Set((this.view?.reviewModels || []).map(choiceKey));
  },

  renderRoles() {
    if (!this.library || !this.view) return;
    const s = this.settings;
    const choices = modelChoices(this.library, this.catalog, this.view);
    const where = (c) => (c.provider === "local" ? "на компьютере" : "OpenRouter");
    const current = this.hotkeyKey();
    const options = choices.map((c) => el("option", { value: choiceKey(c), selected: choiceKey(c) === current }, `${c.label} · ${where(c)}`));
    if (!choices.some((c) => choiceKey(c) === current)) {
      const why = s.provider === "openrouter" ? "нужен ключ OpenRouter" : "не установлена";
      options.unshift(el("option", { value: current, selected: true }, `${current.slice(current.indexOf(":") + 1)} · ${why}`));
    }
    const select = el("select", { onchange: () => {
      const value = select.value;
      const provider = value.slice(0, value.indexOf(":"));
      const model = value.slice(value.indexOf(":") + 1);
      this.useFor(provider === "local" ? "local_model" : "remote_model", model, { provider });
    } }, options);
    const hotkey = el("div", { class: "row" }, select);
    if (s.provider === "openrouter") {
      hotkey.append(el("select", { title: "Протокол запроса к этой модели", onchange: (event) => this.useFor("remote_mode", event.target.value) },
        el("option", { value: "json", selected: s.remote_mode !== "transcription" }, "JSON с инструкцией"),
        el("option", { value: "transcription", selected: s.remote_mode === "transcription" }, "Только изображение (OCR-модели без инструкций)")));
    }
    const picked = this.pickedKeys();
    const chips = choices.map((c) => el("button", {
      class: picked.has(choiceKey(c)) ? "chip on" : "chip",
      title: c.reviews ? c.model : "Только распознаёт заново, проверять текст не умеет",
      onclick: () => this.togglePicked(c, choices),
    }, picked.has(choiceKey(c)) ? "✓ " : "", c.label,
      el("span", { class: "where" }, c.provider === "local" ? "лок" : "облако"),
      c.reviews ? null : el("span", { class: "where" }, "только OCR")));
    if (chips.length === 0) chips.push(el("span", { class: "muted small" }, "Установите локальную модель или добавьте ключ и отметьте ★ облачные."));
    $("#m-roles").replaceChildren(
      el("div", { class: "role" },
        el("b", {}, "По горячей клавише распознаёт", el("small", {}, `${s.capture_hotkey} → буфер обмена и окно результата`)),
        hotkey),
      el("div", { class: "role" },
        el("b", {}, "В окне результата отмечены", el("small", {}, "Запускаются кнопкой «Запустить»; там же их можно менять, выбор запоминается")),
        el("div", { class: "chips" }, chips)),
      el("p", { class: "muted small" },
        "В окне результата доступны все установленные локальные модели и облачные со звёздочкой ★ из каталога OpenRouter. ",
        "Облачные работают параллельно, локальные по очереди."));
  },

  async togglePicked(choice, choices) {
    const picked = this.pickedKeys();
    const key = choiceKey(choice);
    if (picked.has(key)) picked.delete(key);
    else picked.add(key);
    const models = choices.filter((c) => picked.has(choiceKey(c))).map(({ provider, model }) => ({ provider, model }));
    const saved = await call("review_models_set", { models });
    if (saved) {
      this.view.reviewModels = saved;
      this.renderRoles();
      this.renderLocal();
      this.renderCatalog();
    }
  },

  renderLocal() {
    const library = this.library;
    if (!library) return;
    const loaded = library.models.find((m) => m.id === library.loaded);
    $("#m-engine").replaceChildren(
      el("span", { class: "dot", style: library.loaded ? "" : "background: var(--border-strong)" }),
      el("div", {},
        el("b", {}, library.loaded ? `Загружена: ${loaded?.label || library.loaded}` : library.busy ? "Движок занят" : "Движок не запущен"),
        el("div", { class: "muted small" },
          `llama.cpp ${library.engineInstalled ? "установлен" : "скачается вместе с первой моделью"} · ${library.acceleration === "cpu" ? "CPU" : "GPU"}`)),
      el("span", { class: "spacer" }),
      el("button", { disabled: !library.loaded, onclick: () => call("engine_unload") }, "Выгрузить из памяти"));
    const cards = library.models.map((model) => {
      const installing = this.progress?.model === model.id;
      const tags = [
        el("span", { class: "tag" }, formatSize(model.size)),
        el("span", { class: "tag" }, model.reviews ? "распознаёт и проверяет" : "только распознаёт"),
        el("span", { class: "tag" }, model.license),
      ];
      if (model.installed) tags.unshift(el("span", { class: "tag ok" }, "✓ установлена"));
      if (this.hotkeyKey() === `local:${model.id}`) tags.push(el("span", { class: "tag on" }, "по горячей клавише"));
      if (this.pickedKeys().has(`local:${model.id}`)) tags.push(el("span", { class: "tag on" }, "в окне результата"));
      const actions = el("div", { class: "row" });
      if (installing) {
        const p = this.progress;
        actions.append(
          el("progress", { max: p.total || 1, value: p.done, style: "flex: 1" }),
          el("span", { class: "muted small" }, p.total ? `${Math.floor((p.done / p.total) * 100)} %` : ""),
          el("button", { class: "ghost", onclick: () => call("model_cancel_install") }, "Отмена"));
      } else if (!model.installed) {
        actions.append(el("button", { class: "primary", disabled: library.installing,
          onclick: () => call("model_install", { id: model.id }).then(() => this.load()) }, "Установить"));
      } else {
        actions.append(
          el("span", { class: "spacer" }),
          el("button", { class: "ghost danger", onclick: (event) => this.remove(model, event.currentTarget) }, "Удалить"));
      }
      return el("div", { class: "card model" },
        el("h3", {}, model.label),
        el("div", { class: "tags" }, tags),
        el("div", { class: "muted small", title: model.source }, model.source.replace(/^https:\/\//, "")),
        actions);
    });
    $("#m-list").replaceChildren(...cards,
      ...library.errors.map((error) => el("div", { class: "card error" }, error)));
  },

  /// The first click asks, the second deletes (webviews have no reliable confirm()).
  async remove(model, button) {
    if (!button.dataset.armed) {
      button.dataset.armed = "1";
      button.textContent = `Удалить ${formatSize(model.size)}?`;
      setTimeout(() => { delete button.dataset.armed; button.textContent = "Удалить"; }, 4000);
      return;
    }
    if ((await call("model_delete", { id: model.id })) !== undefined) toast("Модель удалена");
    this.load();
  },

  async saveKey() {
    const input = $("#m-key");
    if ((await call("api_key_save", { key: input.value })) === undefined) return;
    toast(input.value.trim() ? "Ключ сохранён" : "Ключ удалён");
    input.value = "";
    this.load();
  },

  async refreshCatalog() {
    const button = $("#m-refresh");
    button.disabled = true;
    button.textContent = "Загружаю…";
    const catalog = await call("catalog_refresh");
    button.disabled = false;
    button.textContent = "Обновить каталог";
    if (catalog) {
      this.catalog = catalog;
      this.renderCatalog();
    }
  },

  renderCatalog() {
    const query = $("#m-search").value.trim().toLowerCase();
    const onlyFavorites = $("#m-only-fav").checked;
    const favorites = new Set(this.catalog.favorites);
    const hotkey = this.settings ? this.hotkeyKey() : "";
    const picked = this.pickedKeys();
    const models = this.catalog.models.filter((m) =>
      (!onlyFavorites || favorites.has(m.id)) &&
      (!query || m.id.toLowerCase().includes(query) || m.name.toLowerCase().includes(query)));
    const rows = models.slice(0, 300).map((model) => {
      const favorite = favorites.has(model.id);
      return el("div", { class: "catalog-row" },
        el("button", { class: favorite ? "star on" : "star",
          title: favorite ? "Убрать из окна результата" : "Показывать в окне результата и в списках выше",
          onclick: async () => {
            const list = await call("catalog_favorite", { id: model.id, favorite: !favorite });
            if (list) {
              this.catalog.favorites = list;
              this.renderRoles();
              this.renderCatalog();
              Result.loadChoices();
            }
          } }, favorite ? "★" : "☆"),
        el("div", { class: "name" },
          el("div", {}, model.name,
            hotkey === `openrouter:${model.id}` ? el("span", { class: "tag on", style: "margin-left: 6px" }, "по горячей клавише") : null,
            picked.has(`openrouter:${model.id}`) ? el("span", { class: "tag on", style: "margin-left: 6px" }, "в окне результата") : null),
          el("div", { class: "id" }, model.id, model.structured ? "" : " · без JSON-режима")),
        el("div", { class: "price", title: "Цена за миллион токенов: вход / выход" }, `$${model.input_price} / $${model.output_price}`));
    });
    if (rows.length === 0) {
      rows.push(el("div", { class: "catalog-row muted" }, el("span"),
        this.catalog.models.length ? "Ничего не найдено" : "Каталог ещё не загружен"));
    }
    $("#m-catalog").replaceChildren(...rows);
  },
};

App.pages.models = Models;
