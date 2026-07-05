// Create-session view: chat with the temporary system agent over WebSocket to
// build the master context, then reveal the generated PIN.

import { Socket } from "./socket.js";
import { WS_BASE } from "./config.js";
import { OUT } from "./protocol.js";
import { Api } from "./api.js";
import {
  qs,
  showView,
  appendChat,
  bindComposer,
  setTyping,
  copyToClipboard,
} from "./ui.js";

export function initCreate({ onEnterWorkspace, onBack }) {
  const messages = qs("#create-messages");
  const status = qs("#create-status");
  const composer = qs("#form-create-chat");
  const input = qs("#create-input");
  const pinPanel = qs("#pin-panel");
  const pinValue = qs("#pin-value");
  const pinSummary = qs("#pin-summary");
  const copyBtn = qs("#btn-copy-pin");
  const enterForm = qs("#form-enter");
  const creatorPseudo = qs("#input-creator-pseudo");
  const backBtn = qs("#btn-create-back");

  let socket = null;
  let pin = null;

  const composerCtl = bindComposer(input, (text) => {
    appendChat(messages, { role: "user", sender: "Vous", text });
    socket?.send(OUT.chat(text, "Vous"));
    setTyping(status, true);
  });
  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    composerCtl.submit();
  });

  backBtn.addEventListener("click", () => {
    cleanup();
    onBack();
  });

  copyBtn.addEventListener("click", async () => {
    if (!pin) return;
    const ok = await copyToClipboard(pin);
    if (ok) {
      copyBtn.textContent = "Copié";
      setTimeout(() => (copyBtn.textContent = "Copier"), 1500);
    }
  });

  enterForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const pseudo = creatorPseudo.value.trim();
    if (!pseudo) {
      creatorPseudo.focus();
      return;
    }
    const btn = enterForm.querySelector("button");
    btn.disabled = true;
    try {
      const res = await Api.joinSession(pin, pseudo);
      cleanup();
      onEnterWorkspace({ ...res, pseudo });
    } catch (err) {
      btn.disabled = false;
    }
  });

  function start({ system_ws_url }) {
    reset();
    showView("view-create");
    socket = new Socket(`${WS_BASE}${system_ws_url}`, { reconnect: false });
    socket.on("system_message", (m) => {
      setTyping(status, false);
      appendChat(messages, { role: "agent", sender: "Agent système", text: m.content });
    });
    socket.on("agent_status", () => setTyping(status, true));
    socket.on("master_context_ready", (m) => onReady(m));
    socket.on("error", (m) => {
      setTyping(status, false);
      appendChat(messages, { role: "agent", sender: "Système", text: `Erreur : ${m.message}` });
    });
    socket.connect();
  }

  function onReady(m) {
    setTyping(status, false);
    pin = m.access_code;
    pinValue.textContent = pin;
    if (m.master_context?.objective) {
      pinSummary.textContent = m.master_context.objective;
      pinSummary.classList.remove("hidden");
    }
    composer.classList.add("hidden");
    pinPanel.classList.remove("hidden");
    creatorPseudo.focus();
  }

  function reset() {
    messages.innerHTML = "";
    pinPanel.classList.add("hidden");
    pinSummary.classList.add("hidden");
    composer.classList.remove("hidden");
    setTyping(status, false);
    pin = null;
  }

  function cleanup() {
    if (socket) {
      socket.close();
      socket = null;
    }
  }

  return { start };
}
