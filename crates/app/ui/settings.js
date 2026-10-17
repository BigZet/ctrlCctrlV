// The settings page, written to the same settings.json as the Python app.
const SettingsPage = {
  settings: null,

  init() {
    $("#s-save").addEventListener("click", () => this.save());
    $("#s-reset").addEventListener("click", () => this.render());
    $("#s-form").addEventListener("submit", (event) => { event.preventDefault(); this.save(); });
  },

  shown() {
    this.load();
  },

  changed() {
    if (App.page === "settings") this.load();
  },

  async load() {
    const view = await call("settings_get");
    if (!view) return;
    this.settings = view.settings;
    this.render();
  },

  render() {
    $("#s-form").replaceChildren(
      this.group("Распознавание", [
        el("p", { class: "muted small", style: "margin: 0" },
          "Какая модель распознаёт по горячей клавише и какие проверяют результат — в разделе ",
          el("button", { class: "link", type: "button", onclick: () => App.show("models") }, "Модели"), "."),
        this.select("capture_format", "Формат", [["auto", "Автоматически"], ["text", "Текст"], ["csv", "Таблица (CSV)"]]),
        this.number("max_tokens", "Предел ответа, токенов", 128, 8192, 1, "Общий для всех моделей"),
        this.check("show_preview", "Открывать окно результата", "Иначе текст только копируется"),
      ]),
      this.group("Горячие клавиши", [
        this.hotkey("capture_hotkey", "Распознать область"),
        this.hotkey("typing_hotkey", "Напечатать буфер"),
        this.hotkey("cancel_hotkey", "Остановить"),
      ]),
      this.group("Печать", [
        this.number("character_delay", "Задержка между символами, мс", 1, 200, 1000),
        this.number("newline_delay", "Задержка после строки, мс", 0, 2000, 1000),
        this.number("spaces_per_tab", "Пробелов вместо Tab", 0, 8, 1, "0 — печатать Tab"),
        this.check("autoindent", "Компенсировать автоотступ", "Shift+Home перед строкой, если редактор сам ставит отступ"),
      ]),
      this.group("Локальный движок", [
        this.select("acceleration", "Ускорение", [["gpu", "GPU"], ["cpu", "Только CPU"]]),
        this.number("idle_minutes", "Выгружать после простоя, мин", 1, 120, 1),
      ]));
  },

  group(title, fields) {
    return el("fieldset", {}, el("legend", {}, title), fields);
  },

  label(text, hint) {
    return el("span", {}, text, hint ? el("small", {}, hint) : null);
  },

  select(name, text, options, hint) {
    const value = this.settings[name];
    const known = options.some(([id]) => id === value);
    const list = known ? options : [[value, value], ...options];
    return el("label", { class: "field" }, this.label(text, hint),
      el("select", { name }, list.map(([id, label]) => el("option", { value: id, selected: id === value }, label))));
  },

  /// `scale` converts the stored value for display (seconds shown as milliseconds).
  number(name, text, min, max, scale, hint) {
    const value = Math.round(this.settings[name] * scale * 1000) / 1000;
    return el("label", { class: "field" }, this.label(text, hint),
      el("input", { name, type: "number", min, max, step: scale === 1 ? 1 : "any", value, "data-scale": scale }));
  },

  check(name, text, hint) {
    return el("label", { class: "field check" }, this.label(text, hint),
      el("input", { name, type: "checkbox", checked: this.settings[name] }));
  },

  hotkey(name, text) {
    const input = el("input", { name, value: this.settings[name], readonly: true, title: "Нажмите и введите новое сочетание" });
    input.addEventListener("focus", () => input.classList.add("recording"));
    input.addEventListener("blur", () => input.classList.remove("recording"));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Tab") return;
      event.preventDefault();
      // Physical keys, so the Russian layout records the same shortcut.
      const code = event.code;
      const key = code.startsWith("Key") ? code.slice(3).toLowerCase()
        : code.startsWith("Digit") ? code.slice(5)
        : code === "Escape" ? "esc" : null;
      if (!key) return;
      const parts = [];
      if (event.ctrlKey) parts.push("ctrl");
      if (event.altKey) parts.push("alt");
      if (event.metaKey) parts.push("cmd");
      if (event.shiftKey) parts.push("shift");
      if (!parts.some((p) => p !== "shift")) {
        toast("Добавьте Ctrl, Alt или Cmd к сочетанию", { error: true });
        return;
      }
      input.value = [...parts, key].join("+");
    });
    return el("label", { class: "field" }, this.label(text, "Нажмите поле и сочетание"), input);
  },

  collect() {
    const settings = { ...this.settings };
    for (const input of $("#s-form").querySelectorAll("[name]")) {
      const name = input.name;
      if (input.type === "checkbox") settings[name] = input.checked;
      else if (input.type === "number") {
        const scale = Number(input.dataset.scale);
        const number = Number(input.value);
        settings[name] = scale === 1 ? Math.round(number) : number / scale;
      } else settings[name] = input.value.trim();
    }
    return settings;
  },

  async save() {
    const settings = this.collect();
    try {
      await invoke("settings_save", { settings });
      this.settings = settings;
      toast("Настройки сохранены");
    } catch (error) {
      // A partial apply (taken hotkey) still saved the file.
      toast(String(error), { error: true, timeout: 7000 });
      if (String(error).startsWith("Настройки сохранены")) this.settings = settings;
    }
  },
};

App.pages.settings = SettingsPage;
