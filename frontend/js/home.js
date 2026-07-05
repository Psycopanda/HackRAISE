// Home view: create a session or join one with a PIN + mandatory pseudo.

import { Api } from "./api.js";
import { qs } from "./ui.js";

export function initHome({ onCreate, onJoined }) {
  const createBtn = qs("#btn-create");
  const joinForm = qs("#form-join");
  const pinInput = qs("#input-pin");
  const pseudoInput = qs("#input-pseudo");
  const errorEl = qs("#join-error");

  createBtn.addEventListener("click", async () => {
    errorEl.textContent = "";
    createBtn.disabled = true;
    try {
      const res = await Api.createSession("Nouvelle session VibeCode");
      onCreate(res); // { session_id, user_id, status, system_ws_url }
    } catch (err) {
      errorEl.textContent = `Impossible de créer la session : ${err.message}`;
    } finally {
      createBtn.disabled = false;
    }
  });

  joinForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorEl.textContent = "";

    const pin = pinInput.value.trim().toUpperCase();
    const pseudo = pseudoInput.value.trim();

    if (!pin) {
      errorEl.textContent = "Entrez un code PIN.";
      return;
    }
    if (!pseudo) {
      errorEl.textContent = "Le pseudo est obligatoire.";
      pseudoInput.focus();
      return;
    }

    const submitBtn = joinForm.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    try {
      const res = await Api.joinSession(pin, pseudo);
      onJoined({ ...res, pseudo });
    } catch (err) {
      if (err.status === 404) errorEl.textContent = "Code PIN invalide.";
      else if (err.status === 409)
        errorEl.textContent = "La session n'est pas encore prête à être rejointe.";
      else errorEl.textContent = `Erreur : ${err.message}`;
    } finally {
      submitBtn.disabled = false;
    }
  });
}
