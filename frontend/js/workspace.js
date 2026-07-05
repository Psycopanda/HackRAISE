// Workspace view: a top bar (session code) + split screen. Left: a multi-file
// project editor (explorer, tabs, editor / Markdown preview / change-review
// diff). Right: the private, streamed chat with the user's personal agent.
// Everything flows over one WebSocket.

import { Socket } from "./socket.js";
import { WS_BASE } from "./config.js";
import { OUT, agentIdFor } from "./protocol.js";
import { CollaborativeEditor } from "./editor.js";
import { initResizer } from "./resizer.js";
import { renderExplorer } from "./explorer.js";
import { renderMarkdown } from "./markdown.js";
import { renderCodeBlock } from "./highlight.js";
import { diffLines, diffStats } from "./diff.js";
import {
  qs,
  showView,
  appendChat,
  startAgentMessage,
  bindComposer,
  setTyping,
  toast,
  copyToClipboard,
} from "./ui.js";

export function initWorkspace() {
  // Chat
  const messages = qs("#workspace-messages");
  const status = qs("#workspace-status");
  const composer = qs("#form-workspace-chat");
  const input = qs("#workspace-input");
  const agentIndicator = qs("#agent-indicator");

  // Editor
  const textarea = qs("#editor-textarea");
  const preview = qs("#editor-preview");
  const diffEl = qs("#editor-diff");
  const tabsEl = qs("#tabs");
  const explorerEl = qs("#explorer");
  const metaEl = qs("#editor-meta");
  const saveBtn = qs("#btn-save");
  const previewBtn = qs("#btn-preview");
  const toggleExplorerBtn = qs("#btn-toggle-explorer");

  // "+" menu
  const newBtn = qs("#btn-new");
  const newMenuList = qs("#new-menu-list");
  const newFileBtn = qs("#btn-new-file");
  const newFolderBtn = qs("#btn-new-folder");

  // Change review
  const proposalStripEl = qs("#proposal-strip");
  const proposalBar = qs("#proposal-bar");
  const proposalBarLabel = qs("#proposal-bar-label");
  const proposalApplyBtn = qs("#btn-proposal-apply");
  const proposalRejectBtn = qs("#btn-proposal-reject");
  const proposalLaterBtn = qs("#btn-proposal-later");

  // Top bar
  const codeEl = qs("#ws-code");
  const titleEl = qs("#ws-title");
  const copyCodeBtn = qs("#btn-copy-code");

  let socket = null;
  let myAgentId = null;
  let myUserId = null;
  let pseudo = null;
  let accessCode = null;
  let previewMode = false;
  let initialized = false;
  let pendingOpenName = null;
  let streamHandle = null;

  const collapsed = new Set(); // collapsed folder paths
  const proposals = new Map(); // task_id -> proposal
  let activeProposalTaskId = null;

  const editor = new CollaborativeEditor(textarea, {
    onSend: (payload) => socket?.send(payload),
    onChange: render,
  });

  initResizer(qs("#resizer"), qs("#pane-editor"), qs("#workspace"));

  // --- Chat composer ---
  const composerCtl = bindComposer(input, (text) => {
    appendChat(messages, { role: "user", sender: "Vous", text });
    socket?.send(OUT.chat(text, pseudo));
  });
  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    composerCtl.submit();
  });

  // --- "+" menu (new file / folder) ---
  newBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    newMenuList.classList.toggle("hidden");
  });
  document.addEventListener("click", () => newMenuList.classList.add("hidden"));

  newFileBtn.addEventListener("click", () => {
    const name = prompt("Nom du nouveau fichier (ex : notes.md, src/app.js) :");
    if (name == null) return;
    const clean = name.trim();
    if (!clean) return;
    pendingOpenName = clean;
    socket?.send({ type: "create_file", name: clean, sender: pseudo });
  });

  newFolderBtn.addEventListener("click", () => {
    const name = prompt("Nom du nouveau dossier (ex : src) :");
    if (name == null) return;
    const clean = name.trim().replace(/\/+$/, "");
    if (!clean) return;
    socket?.send({ type: "create_folder", name: clean, sender: pseudo });
  });

  saveBtn.addEventListener("click", () => {
    const f = editor.getActive();
    if (!f) return;
    editor.flush(); // push the latest content to the file first
    socket?.send({ type: "save_to_context", file_id: f.fileId, name: f.name, sender: pseudo });
    saveBtn.disabled = true;
  });

  previewBtn.addEventListener("click", () => {
    previewMode = !previewMode;
    render();
  });

  toggleExplorerBtn.addEventListener("click", () => {
    explorerEl.classList.toggle("explorer--hidden");
  });

  copyCodeBtn.addEventListener("click", async () => {
    if (!accessCode) return;
    const ok = await copyToClipboard(accessCode);
    if (ok) {
      copyCodeBtn.textContent = "Copié !";
      toast(`Code ${accessCode} copié dans le presse-papier.`);
      setTimeout(() => (copyCodeBtn.textContent = "Copier & inviter"), 1500);
    } else {
      toast(`Copie impossible. Code de session : ${accessCode}`);
    }
  });

  // --- Change-review actions ---
  proposalApplyBtn.addEventListener("click", () => resolveActive("apply"));
  proposalRejectBtn.addEventListener("click", () => resolveActive("reject"));
  proposalLaterBtn.addEventListener("click", () => {
    activeProposalTaskId = null;
    render();
  });

  function resolveActive(decision) {
    if (!isReviewing()) return;
    socket?.send({ type: "resolve_proposal", task_id: activeProposalTaskId, decision });
  }

  // --- Lifecycle ---
  function start(joinResult) {
    pseudo = joinResult.pseudo;
    myUserId = joinResult.user_id;
    myAgentId = agentIdFor(joinResult.user_id);
    messages.innerHTML = "";
    initialized = false;
    previewMode = false;
    streamHandle = null;
    proposals.clear();
    activeProposalTaskId = null;
    collapsed.clear();
    showView("view-workspace");

    socket = new Socket(`${WS_BASE}${joinResult.session_ws_url}`, { reconnect: true });
    wire(socket);
    socket.connect();

    appendChat(messages, {
      role: "agent",
      sender: "Agent",
      text: `Bonjour ${pseudo}. Je suis votre agent personnel : demandez-moi l'état du projet, une modification ou une suppression — vous validerez chaque changement.`,
      markdown: true,
    });
  }

  function wire(sock) {
    sock.on("snapshot", onSnapshot);

    // Streamed conversational responses
    sock.on("agent_message_start", () => {
      setTyping(status, false);
      if (!streamHandle) streamHandle = startAgentMessage(messages, "Agent");
    });
    sock.on("agent_message_delta", (m) => {
      if (!streamHandle) streamHandle = startAgentMessage(messages, "Agent");
      streamHandle.delta(m.content || "");
    });
    sock.on("agent_message_end", () => {
      if (streamHandle) {
        streamHandle.finish();
        streamHandle = null;
      }
    });
    // Non-streamed agent messages (proposal notices, results)
    sock.on("agent_message", (m) => {
      setTyping(status, false);
      appendChat(messages, { role: "agent", sender: "Agent", text: m.content, markdown: true });
    });

    sock.on("agent_status", onAgentStatus);

    // Files
    sock.on("file_update", (m) =>
      editor.applyRemote({
        fileId: m.file?.file_id,
        name: m.file?.name,
        type: m.file?.type,
        language: m.file?.language,
        content: m.file?.content,
        version: m.file?.version,
      })
    );
    sock.on("file_created", onFileCreated);
    sock.on("file_deleted", onFileDeleted);
    sock.on("edit_ack", (m) => editor.ackSaved({ fileId: m.file_id, version: m.version }));
    sock.on("edit_conflict", (m) =>
      editor.reconcileConflict({
        fileId: m.file?.file_id,
        content: m.file?.content,
        version: m.file?.version,
      })
    );
    sock.on("save_ack", (m) => {
      editor.markContextSaved(m.file_id);
      toast("Modifications enregistrées dans le contexte");
    });

    // Approval workflow
    sock.on("change_proposal", (m) => {
      proposals.set(m.task_id, m);
      activeProposalTaskId = m.task_id;
      render();
    });
    sock.on("proposal_resolved", onProposalResolved);

    sock.on("user_joined", (m) =>
      toast(`${m.user?.display_name || "Un collaborateur"} a rejoint la session`)
    );
    sock.on("error", (m) => toast(`Erreur : ${m.message}`));
  }

  // --- Socket handlers ---
  function onSnapshot(m) {
    if (m.access_code) {
      accessCode = m.access_code;
      codeEl.textContent = accessCode;
    }
    if (titleEl) titleEl.textContent = m.title || "";

    for (const f of m.files || []) {
      editor.registerFile({
        fileId: f.file_id,
        name: f.name,
        type: f.type,
        language: f.language,
        content: f.content,
        version: f.version,
      });
    }
    if (Array.isArray(m.proposals)) {
      for (const p of m.proposals) proposals.set(p.task_id, { type: "change_proposal", ...p });
    }
    if (!initialized) {
      const files = m.files || [];
      const first = files.find((f) => baseName(f.name) !== ".keep") || files[0];
      if (first) editor.openTab(first.file_id, { activate: true });
      initialized = true;
    }
    render();
  }

  function onFileCreated(m) {
    const f = m.file || {};
    editor.registerFile({
      fileId: f.file_id,
      name: f.name,
      type: f.type,
      language: f.language,
      content: f.content,
      version: f.version,
    });
    const isKeep = baseName(f.name) === ".keep";
    const mine = (m.by && m.by === myUserId) || (pendingOpenName && f.name === pendingOpenName);
    if (mine && !isKeep) {
      activeProposalTaskId = null;
      editor.openTab(f.file_id, { activate: true });
      pendingOpenName = null;
    } else if (!isKeep) {
      toast(`Nouveau fichier : ${f.name}`);
    }
    render();
  }

  function onFileDeleted(m) {
    if (m.file_id) {
      editor.removeFile(m.file_id);
    } else if (m.name) {
      const f = editor.getFileByName(m.name);
      if (f) editor.removeFile(f.fileId);
    }
    render();
  }

  function onProposalResolved(m) {
    proposalApplyBtn.disabled = false;
    proposalRejectBtn.disabled = false;
    proposals.delete(m.task_id);
    if (activeProposalTaskId === m.task_id) activeProposalTaskId = null;
    render();
  }

  function onAgentStatus(m) {
    if (m.status === "thinking") {
      setTyping(status, true);
      agentIndicator.textContent = "réflexion…";
    } else if (m.status === "modifying") {
      setTyping(status, true);
      agentIndicator.textContent = "préparation…";
    } else {
      setTyping(status, false);
      agentIndicator.textContent = "en ligne";
    }
  }

  // --- Explorer file actions ---
  function onDeletePath(path) {
    if (!window.confirm(`Supprimer « ${path} » ? Cette action est immédiate.`)) return;
    socket?.send({ type: "delete_path", path });
  }

  function onToggleDir(path) {
    if (collapsed.has(path)) collapsed.delete(path);
    else collapsed.add(path);
    render();
  }

  // --- Rendering ---
  function render() {
    renderTabs();
    renderExplorer(explorerEl, editor.list(), editor.activeId, {
      onOpen: (id) => {
        activeProposalTaskId = null;
        editor.openTab(id);
      },
      onDelete: onDeletePath,
      onToggle: onToggleDir,
      collapsed,
    });
    renderStrip();
    renderActive();
  }

  function renderTabs() {
    tabsEl.innerHTML = "";
    const reviewing = isReviewing();
    for (const id of editor.openTabs) {
      const f = editor.getFile(id);
      if (!f) continue;

      const tab = document.createElement("div");
      tab.className = "tab" + (id === editor.activeId && !reviewing ? " tab--active" : "");

      const label = document.createElement("span");
      label.className = "tab__label";
      label.textContent = baseName(f.name);
      label.addEventListener("click", () => {
        activeProposalTaskId = null;
        editor.setActive(id);
      });
      if (f.contextDirty) {
        const dot = document.createElement("span");
        dot.className = "tab__dirty";
        label.appendChild(dot);
      }

      const close = document.createElement("button");
      close.className = "tab__close";
      close.textContent = "×";
      close.title = "Fermer";
      close.addEventListener("click", (event) => {
        event.stopPropagation();
        editor.closeTab(id);
      });

      tab.append(label, close);
      tabsEl.appendChild(tab);
    }
  }

  function renderStrip() {
    proposalStripEl.innerHTML = "";
    if (proposals.size === 0) {
      proposalStripEl.classList.add("hidden");
      return;
    }
    proposalStripEl.classList.remove("hidden");
    for (const [tid, p] of proposals) {
      const chip = document.createElement("button");
      chip.className = "proposal-chip" + (tid === activeProposalTaskId ? " proposal-chip--active" : "");
      const dot = document.createElement("span");
      dot.className = "proposal-chip__dot";
      const label = document.createElement("span");
      const kindLabel = p.kind === "delete" ? "Suppr." : p.kind === "create" ? "Créer" : "Modif.";
      label.textContent = `${kindLabel} ${baseName(p.file_name)}`;
      chip.append(dot, label);
      chip.addEventListener("click", () => {
        activeProposalTaskId = tid;
        render();
      });
      proposalStripEl.appendChild(chip);
    }
  }

  function isReviewing() {
    return Boolean(activeProposalTaskId && proposals.has(activeProposalTaskId));
  }

  function renderActive() {
    if (isReviewing()) {
      const p = proposals.get(activeProposalTaskId);
      buildDiffInto(diffEl, p);
      diffEl.classList.remove("hidden");
      preview.classList.add("hidden");
      textarea.classList.add("hidden");
      proposalBar.classList.remove("hidden");
      proposalBarLabel.textContent = proposalLabel(p);
      previewBtn.classList.add("hidden");
      metaEl.textContent = p.file_name;
      saveBtn.disabled = true;
      return;
    }

    diffEl.classList.add("hidden");
    proposalBar.classList.add("hidden");

    const f = editor.getActive();
    metaEl.textContent = f ? `${f.name} · v${f.version}` : "";
    saveBtn.disabled = !(f && f.contextDirty);

    const isMarkdown = !!f && (f.type === "markdown" || /\.md$/i.test(f.name));
    const isCode = !!f && !isMarkdown && (f.type === "code" || !!f.language);
    const canPreview = isMarkdown || isCode;
    previewBtn.classList.toggle("hidden", !canPreview);
    if (!canPreview) previewMode = false;
    previewBtn.textContent = previewMode ? "Éditer" : "Aperçu";

    if (previewMode && f) {
      if (isMarkdown) {
        preview.classList.add("markdown-body");
        preview.innerHTML = renderMarkdown(f.local);
      } else {
        preview.classList.remove("markdown-body");
        preview.innerHTML = renderCodeBlock(f.local, f.language || f.name);
      }
      preview.classList.remove("hidden");
      textarea.classList.add("hidden");
    } else {
      preview.classList.add("hidden");
      textarea.classList.remove("hidden");
    }
  }

  // --- Diff building (green additions / red deletions) ---
  function buildDiffInto(container, proposal) {
    container.innerHTML = "";
    if (proposal.kind === "delete") {
      const files =
        proposal.files && proposal.files.length
          ? proposal.files
          : [{ name: proposal.file_name, previous_content: proposal.previous_content || "" }];
      for (const entry of files) {
        if (baseName(entry.name) === ".keep") continue;
        addFileHeader(container, "− " + entry.name);
        renderDiffLines(container, diffLines(entry.previous_content || "", ""));
      }
      return;
    }
    const prev = proposal.kind === "create" ? "" : proposal.previous_content || "";
    const next = proposal.proposed_content || "";
    addFileHeader(container, (proposal.kind === "create" ? "+ " : "~ ") + proposal.file_name);
    const lines = diffLines(prev, next);
    if (!lines.length) {
      const empty = document.createElement("div");
      empty.className = "diff__empty";
      empty.textContent = "(aucun changement)";
      container.appendChild(empty);
    } else {
      renderDiffLines(container, lines);
    }
  }

  function addFileHeader(container, text) {
    const header = document.createElement("div");
    header.className = "diff__filehdr";
    header.textContent = text;
    container.appendChild(header);
  }

  function renderDiffLines(container, lines) {
    for (const line of lines) {
      const row = document.createElement("div");
      row.className = "diff__line diff__line--" + line.type;
      const sign = document.createElement("span");
      sign.className = "diff__sign";
      sign.textContent = line.type === "add" ? "+" : line.type === "del" ? "−" : " ";
      const text = document.createElement("span");
      text.className = "diff__text";
      text.textContent = line.text;
      row.append(sign, text);
      container.appendChild(row);
    }
  }

  function proposalLabel(proposal) {
    const kindLabel =
      proposal.kind === "delete"
        ? "Suppression"
        : proposal.kind === "create"
        ? "Création"
        : "Modification";
    let added = 0;
    let removed = 0;
    if (proposal.kind === "delete") {
      for (const entry of proposal.files || []) {
        removed += (entry.previous_content || "").split("\n").length;
      }
    } else {
      const stats = diffStats(
        diffLines(
          proposal.kind === "create" ? "" : proposal.previous_content || "",
          proposal.proposed_content || ""
        )
      );
      added = stats.added;
      removed = stats.removed;
    }
    return `${kindLabel} · ${proposal.file_name} · +${added} −${removed}`;
  }

  function baseName(name) {
    const parts = String(name).split("/");
    return parts[parts.length - 1] || name;
  }

  return { start };
}
