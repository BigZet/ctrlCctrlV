// The typing panel. It never takes focus, so its buttons act on the window
// being typed into: "Продолжить" types the rest into whatever window is focused.
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const $ = (id) => document.getElementById(id);
const number = (n) => n.toLocaleString("ru-RU");
let state = "typing";

const titles = {
  countdown: (v) => `Печать через ${v.countdown} с`,
  typing: () => "Печатаю",
  paused: () => "Пауза",
  done: () => "Готово",
  stopped: () => "Остановлено",
};

function render(view) {
  state = view.state;
  const panel = $("panel");
  panel.dataset.state = view.state;
  panel.classList.toggle("error", view.error);
  $("title").textContent = titles[view.state](view);
  $("line").textContent = view.lines > 1 ? `строка ${view.line} из ${view.lines}` : "";
  $("count").textContent = `${number(view.done)} из ${number(view.total)} симв.`;
  $("fill").style.width = `${view.total ? (100 * view.done) / view.total : 0}%`;
  const finished = view.state === "done" || view.state === "stopped";
  const toggle = $("toggle");
  toggle.textContent = view.state === "paused" ? "Продолжить" : "Пауза";
  toggle.disabled = finished;
  $("stop").disabled = finished;
  let hint = "";
  if (view.state === "paused") {
    const reason = view.message && view.message !== "Пауза" ? view.message.replace(/^Пауза: /, "") : "";
    const why = reason ? `${reason[0].toUpperCase()}${reason.slice(1)}. ` : "";
    hint = `${why}Перейдите в окно и нажмите «Продолжить» или ${view.hotkey}`;
  } else if (view.state === "countdown") {
    hint = "Переключитесь в окно, куда печатать";
  } else if (view.state === "typing") {
    hint = "Не трогайте клавиатуру и мышь, пока идёт печать";
  } else if (view.state === "stopped") {
    hint = view.message;
  }
  $("hint").textContent = hint;
}

$("toggle").addEventListener("click", () => {
  invoke("typing_control", { action: state === "paused" ? "resume" : "pause" });
});
$("stop").addEventListener("click", () => invoke("typing_control", { action: "stop" }));

listen("typing", ({ payload }) => render(payload));
invoke("typing_state").then((view) => view && render(view));
