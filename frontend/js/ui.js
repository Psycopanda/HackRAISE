// DOM helpers: view switching, chat rendering, status badges, composer binding
// and non-blocking toasts.

import { renderMarkdown } from "./markdown.js";

export const qs = (sel, root = document) => root.querySelector(sel);
export const qsa = (sel, root = document) => [...root.querySelectorAll(sel)];

export function showView(id) {
  qsa(".view").forEach((view) => view.classList.toggle("hidden", view.id !== id));
}

export function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 180) + "px";
}

/**
 * Wire a textarea composer: Enter submits, Shift+Enter inserts a newline.
 * Returns a small controller exposing `submit()`.
 */
export function bindComposer(textarea, onSubmit) {
  const submit = () => {
    const value = textarea.value.trim();
    if (!value) return;
    onSubmit(value);
    textarea.value = "";
    autoGrow(textarea);
  };

  textarea.addEventListener("input", () => autoGrow(textarea));
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  });

  return { submit };
}

export function appendChat(container, { role, sender, text, markdown }) {
  const row = document.createElement("div");
  row.className = `msg msg--${role}`;

  const label = document.createElement("div");
  label.className = "msg__sender";
  label.textContent = sender;

  const bubble = document.createElement("div");
  bubble.className = "msg__bubble";
  if (markdown) bubble.innerHTML = renderMarkdown(text);
  else bubble.textContent = text;

  row.append(label, bubble);
  container.appendChild(row);
  scrollToBottom(container);
  return row;
}

// A streaming assistant message: the row is created lazily on the first delta,
// text is shown live, and Markdown is rendered once finished.
export function startAgentMessage(container, sender) {
  let row = null;
  let bubble = null;
  let buffer = "";

  const ensure = () => {
    if (row) return;
    row = document.createElement("div");
    row.className = "msg msg--agent";
    const label = document.createElement("div");
    label.className = "msg__sender";
    label.textContent = sender;
    bubble = document.createElement("div");
    bubble.className = "msg__bubble";
    row.append(label, bubble);
    container.appendChild(row);
  };

  return {
    delta(piece) {
      ensure();
      buffer += piece;
      bubble.textContent = buffer;
      scrollToBottom(container);
    },
    finish() {
      if (!row) return "";
      bubble.innerHTML = renderMarkdown(buffer);
      scrollToBottom(container);
      return buffer;
    },
    get started() {
      return row !== null;
    },
  };
}

/** A discreet, centered status/notification line (intention / done / aborted / info). */
export function appendEvent(container, { variant, text }) {
  const row = document.createElement("div");
  row.className = `event event--${variant}`;

  const dot = document.createElement("span");
  dot.className = "event__dot";

  const span = document.createElement("span");
  span.className = "event__text";
  span.textContent = text;

  row.append(dot, span);
  container.appendChild(row);
  scrollToBottom(container);
  return row;
}

export function setTyping(node, on) {
  node.classList.toggle("hidden", !on);
}

export function scrollToBottom(container) {
  container.scrollTop = container.scrollHeight;
}

export function toast(text, timeout = 4000) {
  const container = qs("#toast-container");
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  container.appendChild(el);
  requestAnimationFrame(() => el.classList.add("toast--show"));
  setTimeout(() => {
    el.classList.remove("toast--show");
    setTimeout(() => el.remove(), 250);
  }, timeout);
}

/**
 * Copy text to the clipboard, with a fallback for non-secure origins.
 *
 * `navigator.clipboard` is only available over HTTPS or on localhost; when the
 * app is opened over http://<lan-ip>:8000 (to invite people on the network) it
 * is undefined and writeText() throws. We then fall back to a hidden <textarea>
 * and document.execCommand("copy"). Returns true on success.
 */
export async function copyToClipboard(text) {
  const value = String(text ?? "");
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch (_) {
      /* fall through to the legacy path below */
    }
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}
