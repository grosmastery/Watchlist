const THRESHOLD = 110;
const VISIBLE_CARDS = 3;
const REFILL_AT = 4;
const LEAVE_MS = 320;

const seen = new Set();
const undoStack = [];
let drag = null;
let refilling = false;
let exhausted = false;
let pending = Promise.resolve();

const deckElement = () => document.getElementById("swipe-deck");
const cardKey = (card) => `${card.dataset.mediaType}:${card.dataset.tmdbId}`;
const cards = () => [...(deckElement()?.querySelectorAll(".swipe-card:not(.is-leaving)") ?? [])];
const topCard = () => cards().at(-1);

function layout() {
  const stack = cards().reverse();
  stack.forEach((card, index) => {
    seen.add(cardKey(card));
    card.dataset.depth = index < VISIBLE_CARDS ? String(index) : "hidden";
    card.toggleAttribute("inert", index !== 0);
  });
}

function showDoneWhenEmpty() {
  const deck = deckElement();
  if (!deck || cards().length || refilling) {
    return;
  }
  deck.hidden = true;
  document.getElementById("swipe-controls").hidden = true;
  document.getElementById("swipe-done").hidden = false;
}

function showDeck() {
  const deck = deckElement();
  if (!deck) {
    return;
  }
  deck.hidden = false;
  document.getElementById("swipe-controls").hidden = false;
  document.getElementById("swipe-done").hidden = true;
}

function updateUndo() {
  const button = document.querySelector("[data-swipe=undo]");
  if (button) {
    button.disabled = undoStack.length === 0;
  }
}

function enqueue(task) {
  pending = pending.then(task);
}

function showError() {
  const error = document.getElementById("swipe-error");
  if (error) {
    error.hidden = false;
  }
}

function csrfToken() {
  return JSON.parse(document.body.getAttribute("hx-headers"))["X-CSRF-Token"];
}

async function send(url, options) {
  try {
    const response = await fetch(url, {
      ...options,
      headers: { "X-CSRF-Token": csrfToken() },
    });
    if (!response.ok) {
      throw new Error(String(response.status));
    }
    const payload = await response.json();
    document.getElementById("liked-count").textContent = payload.liked_count;
  } catch {
    showError();
  }
}

function sendDecision(card, liked) {
  const { mediaType, tmdbId, name, posterPath, subtitle } = card.dataset;
  return send("/swipe/decisions", {
    method: "POST",
    body: new URLSearchParams({
      media_type: mediaType,
      tmdb_id: tmdbId,
      liked: String(liked),
      name,
      poster_path: posterPath,
      subtitle,
    }),
  });
}

function sendToList(card) {
  const { mediaType, tmdbId } = card.dataset;
  return send("/swipe/list", {
    method: "POST",
    body: new URLSearchParams({ media_type: mediaType, tmdb_id: tmdbId }),
  });
}

function sendUndo(card, action) {
  const { mediaType, tmdbId } = card.dataset;
  const base = action === "list" ? "/swipe/list" : "/swipe/decisions";
  return send(`${base}/${mediaType}/${tmdbId}`, { method: "DELETE" });
}

function resetCard(card) {
  card.style.transform = "";
  for (const name of ["--like", "--skip", "--list"]) {
    card.style.removeProperty(name);
  }
}

async function refill() {
  const deck = deckElement();
  if (!deck || refilling || exhausted || cards().length > REFILL_AT) {
    return;
  }
  refilling = true;
  let fresh = [];
  try {
    const response = await fetch(`/swipe/deck?${deck.dataset.query}`, {
      headers: { "HX-Request": "true" },
    });
    const html = new DOMParser().parseFromString(await response.text(), "text/html");
    fresh = [...html.querySelectorAll(".swipe-card")].filter((card) => !seen.has(cardKey(card)));
  } finally {
    refilling = false;
  }
  if (deckElement() !== deck) {
    return;
  }
  exhausted = fresh.length === 0;
  deck.prepend(...fresh);
  layout();
  showDoneWhenEmpty();
}

const EXITS = {
  like: "translate(140vw, 40px) rotate(28deg)",
  skip: "translate(-140vw, 40px) rotate(-28deg)",
  list: "translate(0, -130vh) scale(0.9)",
};

function decide(card, action) {
  card.classList.remove("is-dragging");
  card.classList.add("is-leaving");
  card.style.setProperty(`--${action}`, "1");
  card.style.transform = EXITS[action];
  const timer = setTimeout(() => {
    card.remove();
    showDoneWhenEmpty();
  }, LEAVE_MS);
  undoStack.push({ card, timer, action });
  updateUndo();
  layout();
  enqueue(() => (action === "list" ? sendToList(card) : sendDecision(card, action === "like")));
  refill();
}

function undo() {
  const entry = undoStack.pop();
  const deck = deckElement();
  updateUndo();
  if (!entry || !deck) {
    return;
  }
  const { card, timer, action } = entry;
  clearTimeout(timer);
  showDeck();
  card.classList.remove("is-leaving");
  card.classList.add("is-dragging");
  deck.append(card);
  layout();
  card.getBoundingClientRect();
  card.classList.remove("is-dragging");
  resetCard(card);
  enqueue(() => sendUndo(card, action));
}

function endDrag(accept) {
  if (!drag) {
    return;
  }
  const { card, dx, dy } = drag;
  drag = null;
  card.classList.remove("is-dragging");
  const action = accept ? dragAction(dx, dy) : null;
  if (action) {
    decide(card, action);
    return;
  }
  resetCard(card);
}

function dragAction(dx, dy) {
  if (-dy >= THRESHOLD && -dy > Math.abs(dx)) {
    return "list";
  }
  if (Math.abs(dx) >= THRESHOLD) {
    return dx > 0 ? "like" : "skip";
  }
  return null;
}

document.addEventListener("pointerdown", (event) => {
  const card = event.target.closest(".swipe-card");
  if (!card || card !== topCard() || event.button !== 0 || event.target.closest("a")) {
    return;
  }
  drag = { card, pointer: event.pointerId, startX: event.clientX, startY: event.clientY, dx: 0, dy: 0 };
  card.setPointerCapture(event.pointerId);
  card.classList.add("is-dragging");
});

document.addEventListener("pointermove", (event) => {
  if (!drag || event.pointerId !== drag.pointer) {
    return;
  }
  drag.dx = event.clientX - drag.startX;
  drag.dy = event.clientY - drag.startY;
  const upward = Math.max(0, -drag.dy - Math.abs(drag.dx));
  const offsetY = drag.dy < 0 ? drag.dy : drag.dy * 0.3;
  drag.card.style.transform = `translate(${drag.dx}px, ${offsetY}px) rotate(${drag.dx / 18}deg)`;
  drag.card.style.setProperty("--like", String(Math.max(0, drag.dx / THRESHOLD)));
  drag.card.style.setProperty("--skip", String(Math.max(0, -drag.dx / THRESHOLD)));
  drag.card.style.setProperty("--list", String(upward / THRESHOLD));
});

document.addEventListener("pointerup", () => endDrag(true));
document.addEventListener("pointercancel", () => endDrag(false));

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-swipe]");
  if (!button) {
    return;
  }
  if (button.dataset.swipe === "undo") {
    undo();
    return;
  }
  const card = topCard();
  if (card) {
    decide(card, button.dataset.swipe);
  }
});

document.addEventListener("keydown", (event) => {
  if (!deckElement() || event.target.matches("input[type=search], input[type=text], textarea, select")) {
    return;
  }
  const card = topCard();
  const action = { ArrowRight: "like", ArrowLeft: "skip", ArrowUp: "list" }[event.key];
  if (card && action) {
    event.preventDefault();
    decide(card, action);
  } else if (event.key === "Backspace") {
    event.preventDefault();
    undo();
  }
});

document.addEventListener("htmx:afterSettle", (event) => {
  if (event.detail.target.id === "swipe-area") {
    exhausted = false;
    undoStack.length = 0;
    updateUndo();
    layout();
  }
});
