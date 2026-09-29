// Keyboard navigation, the filter and the copy buttons. Without it the site is plain links.

const status = document.getElementById("status-msg");
const idle = status.textContent;
const say = (text) => (status.textContent = text || idle);

function copy(button) {
  const text = button.dataset.copy;
  navigator.clipboard.writeText(text).then(
    () => {
      button.classList.add("copied");
      say(`copied: ${text}`);
      clearTimeout(button.timer);
      button.timer = setTimeout(() => button.classList.remove("copied"), 1500);
    },
    () => say("could not copy"),
  );
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button.copy");
  if (button) copy(button);
});

// Icon buttons say what they do in the status bar.
document.addEventListener("pointerover", (event) => {
  const tip = event.target.closest?.("[data-tip]");
  if (tip) say(tip.dataset.tip);
});

const go = (href) => href && (location.href = href);
let keys = {};

const index = document.querySelector(".index");
if (index) {
  const filter = document.getElementById("filter");
  const tabs = [...index.querySelectorAll(".tab")];
  const groups = [...index.querySelectorAll(".group")];
  const empty = index.querySelector(".empty");
  let active = 0;
  let current = null;

  const visible = () => [...groups[active].querySelectorAll(".row:not([hidden])")];
  const select = (row, scroll = true) => {
    current?.classList.remove("sel");
    current = row;
    if (!row) return say(filter.value ? "" : groups[active].dataset.name);
    row.classList.add("sel");
    if (scroll) row.scrollIntoView({ block: "nearest" });
    say(row.querySelector(".pip").dataset.copy);
  };
  const move = (step) => {
    const shown = visible();
    const at = shown.indexOf(current);
    const next = at < 0 ? (step > 0 ? 0 : shown.length - 1) : (at + step + shown.length) % shown.length;
    select(shown[next] ?? null);
  };
  const show = (i) => {
    active = (i + groups.length) % groups.length;
    groups.forEach((group, j) => group.classList.toggle("active", j === active));
    tabs.forEach((tab, j) => tab.setAttribute("aria-selected", j === active));
    tabs[active].scrollIntoView({ block: "nearest", inline: "nearest" });
    empty.hidden = !groups[active].querySelector(".row") || visible().length > 0;
    select(filter.value ? (visible()[0] ?? null) : null);
  };

  filter.addEventListener("input", () => {
    const query = filter.value.trim().toLowerCase();
    groups.forEach((group, i) => {
      const rows = [...group.querySelectorAll(".row")];
      rows.forEach((row) => (row.hidden = !row.dataset.search.includes(query)));
      const matches = rows.filter((row) => !row.hidden).length;
      tabs[i].querySelector(".count").textContent = String(matches).padStart(2, "0");
      tabs[i].classList.toggle("none", matches === 0);
    });
    const first = tabs.findIndex((tab) => !tab.classList.contains("none"));
    show(visible().length || first < 0 ? active : first);
  });

  tabs.forEach((tab, i) => tab.addEventListener("click", () => show(i)));
  index.querySelectorAll(".row").forEach((row) => {
    row.addEventListener("mousemove", () => current !== row && select(row, false));
    row.addEventListener("click", (event) => {
      if (!event.target.closest("a, button")) go(row.querySelector("a").href);
    });
  });

  keys = {
    "/": () => filter.focus(),
    h: () => show(active - 1),
    ArrowLeft: () => show(active - 1),
    l: () => show(active + 1),
    ArrowRight: () => show(active + 1),
    j: () => move(1),
    ArrowDown: () => move(1),
    k: () => move(-1),
    ArrowUp: () => move(-1),
    Enter: () => go(current?.querySelector("a").href),
    d: () => go("/dependencies/"),
    c: () => current && copy(current.querySelector(".pip")),
    g: () => current && copy(current.querySelector(".clone")),
    r: () => go(current?.querySelector(".repo").href),
    Escape: () => {
      filter.value = "";
      filter.dispatchEvent(new Event("input"));
      filter.blur();
    },
  };
} else if (document.querySelector(".deps-view")) {
  keys = { u: () => go("/") };
} else {
  keys = {
    "[": () => go(document.querySelector(".pager .prev")?.href),
    "]": () => go(document.querySelector(".pager .next")?.href),
    j: () => scrollBy({ top: 120 }),
    k: () => scrollBy({ top: -120 }),
    u: () => go("/"),
    c: () => copy(document.querySelector("aside .pip")),
    g: () => copy(document.querySelector("aside .clone")),
    r: () => go(document.querySelector("aside .repo").href),
  };
}

document.addEventListener("keydown", (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const field = event.target.closest?.("input, textarea");
  if (field && !["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(event.key)) return;
  if (event.key === "Enter" && event.target.closest?.("a, button")) return;
  const action = keys[event.key];
  if (!action) return;
  event.preventDefault();
  action();
});
