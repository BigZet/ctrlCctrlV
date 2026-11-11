// The first-run wizard: a few choices that map onto settings.json. Opens by
// itself on the first launch and later from the Settings page.
const SetupPage = {
  settings: null,
  hasKey: false,

  /// Settings a workplace choice sets; everything else keeps its value.
  presets: {
    desktop: {
      typing_method: "unicode", layout_switch: "sync", delete_autoclose: false, hotkey_mode: "system",
    },
    vdi: {
      typing_method: "layout", layout_switch: "sync", layout_delay: 2.0, delete_autoclose: true, hotkey_mode: "rawinput",
    },
    vdiNoSync: {
      typing_method: "layout", layout_switch: "hotkeys", layout_delay: 2.0, delete_autoclose: true, hotkey_mode: "rawinput",
    },
  },

  init(snapshot) {
    $("#w-done").addEventListener("click", () => this.finish(true));
    $("#w-skip").addEventListener("click", () => this.finish(false));
    $("#w-form").addEventListener("submit", (event) => { event.preventDefault(); this.finish(true); });
    if (!snapshot.setup_done) App.show("setup");
  },

  async shown() {
    const view = await call("settings_get");
    if (!view) return;
    this.settings = view.settings;
    this.hasKey = view.hasKey;
    this.render();
  },

  /// The preset the current settings match, for the checked radio.
  current() {
    const s = this.settings;
    if (s.typing_method !== "layout") return "desktop";
    return s.layout_switch === "hotkeys" ? "vdiNoSync" : "vdi";
  },

  render() {
    const s = this.settings;
    const choice = (name, value, checked, title, hint) =>
      el("label", { class: "choice" }, el("input", { type: "radio", name, value, checked }),
        el("span", {}, title, el("small", {}, hint)));
    const workplace = this.current();
    const windowsOnly = navigator.platform.startsWith("Win");
    $("#w-form").replaceChildren(
      el("p", { class: "muted", style: "margin: 0" },
        "Несколько вопросов, чтобы приложение сразу работало там, где вы печатаете. Всё это можно потом изменить в настройках."),
      el("fieldset", {}, el("legend", {}, "Где вы печатаете"),
        el("div", { class: "choices" },
          choice("workplace", "desktop", workplace === "desktop", "На этом компьютере",
            "Обычные редакторы и программы. Текст печатается как символы Unicode."),
          choice("workplace", "vdi", workplace === "vdi", "В удалённом рабочем столе (Citrix, RDP, Hyper-V)",
            "Печать настоящими клавишами по раскладке, Delete после скобок и кавычек, горячие клавиши читаются напрямую с клавиатуры. "
            + "Раскладка в сессии переключается вместе с раскладкой компьютера: в Citrix включите «Allow dynamic sync»."),
          choice("workplace", "vdiNoSync", workplace === "vdiNoSync", "В удалённом рабочем столе, который не повторяет раскладку",
            "То же, но раскладку в сессии переключает сочетание: назначьте внутри сессии Ctrl+Shift+1 для английского и Ctrl+Shift+2 для русского.")),
        windowsOnly ? null : el("p", { class: "muted small", style: "margin: 0" }, "Режимы для удалённых рабочих столов работают только на Windows.")),
      el("fieldset", {}, el("legend", {}, "Горячие клавиши"),
        SettingsPage.hotkey("capture_hotkey", "Распознать область", s),
        SettingsPage.hotkey("typing_hotkey", "Напечатать буфер", s),
        SettingsPage.hotkey("cancel_hotkey", "Остановить", s)),
      el("fieldset", {}, el("legend", {}, "Распознавание"),
        el("div", { class: "choices" },
          choice("provider", "local", s.provider === "local", "Локальная модель",
            "Бесплатно и без сети; модель скачивается при первом распознавании (около 1–2 ГБ)."),
          choice("provider", "openrouter", s.provider === "openrouter", "OpenRouter",
            this.hasKey ? "Ключ уже сохранён." : "Нужен API-ключ; введите его ниже или позже в разделе «Модели».")),
        el("label", { class: "field" }, el("span", {}, "Ключ OpenRouter", el("small", {}, this.hasKey ? "Оставьте пустым, чтобы не менять" : "sk-or-…")),
          el("input", { name: "api_key", type: "password", placeholder: this.hasKey ? "••••••••" : "" })),
        el("label", { class: "field check" }, el("span", {}, "Открывать окно результата", el("small", {}, "Иначе текст только копируется")),
          el("input", { name: "show_preview", type: "checkbox", checked: s.show_preview }))));
  },

  async finish(save) {
    if (save) {
      const form = $("#w-form");
      const settings = { ...this.settings, ...this.presets[form.workplace.value] };
      settings.provider = form.provider.value;
      settings.show_preview = form.show_preview.checked;
      for (const name of ["capture_hotkey", "typing_hotkey", "cancel_hotkey"]) settings[name] = form[name].value.trim();
      const key = form.api_key.value.trim();
      if (key && (await call("api_key_save", { key })) === undefined) return;
      if ((await call("settings_save", { settings })) === undefined) return;
      toast("Настройки сохранены");
    }
    await call("setup_done");
    App.show("result");
  },
};

App.pages.setup = SetupPage;
