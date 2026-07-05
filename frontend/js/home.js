// Home view: create a session or join one with a PIN + mandatory pseudo.

import { Api } from "./api.js";
import { qs } from "./ui.js";

export function initHome({ onCreate, onJoined }) {
  const createBtn = qs("#btn-create");
  const joinForm = qs("#form-join");
  const pinInput = qs("#input-pin");
  const pseudoInput = qs("#input-pseudo");
  const errorEl = qs("#join-error");
  const uploadBtn = qs("#btn-upload");
  const uploadInput = qs("#input-upload");
  const uploadStatus = qs("#upload-status");

  let uploadedText = null;
  let uploadedFilename = null;

  uploadBtn.addEventListener("click", () => uploadInput.click());

  uploadInput.addEventListener("change", async () => {
    const file = uploadInput.files[0];
    if (!file) return;

    uploadedText = null;
    uploadedFilename = null;
    uploadStatus.classList.remove("upload-status--error");
    uploadStatus.textContent = `Analyse de « ${file.name} »…`;
    uploadBtn.disabled = true;
    try {
      const res = await Api.extractUpload(file);
      uploadedText = res.text || null;
      uploadedFilename = uploadedText ? file.name : null;
      uploadStatus.textContent = uploadedText
        ? `« ${file.name} » prêt à être utilisé.`
        : `« ${file.name} » n'a fourni aucun texte exploitable.`;
    } catch (err) {
      uploadStatus.classList.add("upload-status--error");
      uploadStatus.textContent = `Échec de l'analyse : ${err.message}`;
    } finally {
      uploadBtn.disabled = false;
    }
  });

  createBtn.addEventListener("click", async () => {
    errorEl.textContent = "";
    createBtn.disabled = true;
    try {
      const res = await Api.createSession("Nouvelle session CoVibe");
      onCreate({ ...res, uploadedText, uploadedFilename });
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
