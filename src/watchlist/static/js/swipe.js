const THRESHOLD = 110;
const VISIBLE_CARDS = 3;
const REFILL_AT = 4;
const LEAVE_MS = 320;

const seen = new Set();
let drag = null;
let refilling = false;
let exhausted = false;

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

function showError() {
  const error = document.getElementById("swipe-error");
  if (error) {
    error.hidden = false;
  }
}

function csrfToken() {
  return JSON.parse(document.body.getAttribute("hx-headers"))["X-CSRF-Token"];
}

async function sendDecision(card, liked) {
  const { mediaType, tmdbId, name, posterPath, subtitle } = card.dataset;
  try {
    const response = await fetch("/swipe/decisions", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken() },
      body: new URLSearchParams({
        media_type: mediaType,
        tmdb_id: tmdbId,
        liked: String(liked),
        name,
        poster_path: posterPath,
        subtitle,
      }),
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

function decide(card, liked) {
  const direction = liked ? 1 : -1;
  card.classList.remove("is-dragging");
  card.classList.add("is-leaving");
  card.style.setProperty(liked ? "--like" : "--skip", "1");
  card.style.transform = `translate(${direction * 140}vw, 40px) rotate(${direction * 28}deg)`;
  setTimeout(() => {
    card.remove();
    showDoneWhenEmpty();
  }, LEAVE_MS);
  layout();
  sendDecision(card, liked);
  refill();
}

function endDrag(accept) {
  if (!drag) {
    return;
  }
  const { card, dx } = drag;
  drag = null;
  card.classList.remove("is-dragging");
  if (accept && Math.abs(dx) >= THRESHOLD) {
    decide(card, dx > 0);
    return;
  }
  card.style.transform = "";
  card.style.removeProperty("--like");
  card.style.removeProperty("--skip");
}

document.addEventListener("pointerdown", (event) => {
  const card = event.target.closest(".swipe-card");
  if (!card || card !== topCard() || event.button !== 0 || event.target.closest("a")) {
    return;
  }
  drag = { card, pointer: event.pointerId, startX: event.clientX, startY: event.clientY, dx: 0 };
  card.setPointerCapture(event.pointerId);
  card.classList.add("is-dragging");
});

document.addEventListener("pointermove", (event) => {
  if (!drag || event.pointerId !== drag.pointer) {
    return;
  }
  drag.dx = event.clientX - drag.startX;
  const dy = (event.clientY - drag.startY) * 0.3;
  drag.card.style.transform = `translate(${drag.dx}px, ${dy}px) rotate(${drag.dx / 18}deg)`;
  drag.card.style.setProperty("--like", String(Math.max(0, drag.dx / THRESHOLD)));
  drag.card.style.setProperty("--skip", String(Math.max(0, -drag.dx / THRESHOLD)));
});

document.addEventListener("pointerup", () => endDrag(true));
document.addEventListener("pointercancel", () => endDrag(false));

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-swipe]");
  const card = topCard();
  if (button && card) {
    decide(card, button.dataset.swipe === "like");
  }
});

document.addEventListener("keydown", (event) => {
  const card = topCard();
  if (!card || event.target.matches("input[type=search], input[type=text], textarea, select")) {
    return;
  }
  if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
    event.preventDefault();
    decide(card, event.key === "ArrowRight");
  }
});

document.addEventListener("htmx:afterSettle", (event) => {
  if (event.detail.target.id === "swipe-area") {
    exhausted = false;
    layout();
  }
});
