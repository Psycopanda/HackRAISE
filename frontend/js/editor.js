// Collaborative multi-file editor component.
//
// Backed by a <textarea> but encapsulated so it can host a richer code editor
// later. Defining rule: it NEVER blocks, disables or greys out the input.
// Remote updates (other users or the user's own agent) are merged without ever
// interrupting local typing. It manages several open files (tabs) sharing one
// active buffer shown in the textarea.

import { OUT } from "./protocol.js";

const DEBOUNCE_MS = 350;

export class CollaborativeEditor {
  /**
   * @param {HTMLTextAreaElement} textarea
   * @param {{ onSend: (payload: object) => void, onChange?: () => void }} options
   */
  constructor(textarea, { onSend, onChange }) {
    this.textarea = textarea;
    this.onSend = onSend;
    this.onChange = onChange || (() => {});

    /** @type {Map<string, object>} fileId -> model */
    this.files = new Map();
    this.openTabs = []; // ordered fileIds shown as tabs
    this.activeId = null;

    this._timer = null;
    this._suppressInput = false;

    this.textarea.addEventListener("input", () => this._onInput());
  }

  // --- File registry ------------------------------------------------------

  /** Add or refresh a file's server state (snapshot / file_update / created). */
  registerFile({ fileId, name, type, language, content, version }) {
    if (!fileId) return;
    let f = this.files.get(fileId);
    if (!f) {
      f = {
        fileId,
        name: name || "sans-nom",
        type: type || "text",
        language: language || null,
        server: content ?? "",
        local: content ?? "",
        version: version ?? 0,
        dirty: false, // local edits not yet confirmed saved to the file doc
        contextDirty: false, // edits not yet committed to the shared context
      };
      this.files.set(fileId, f);
    } else {
      if (name) f.name = name;
      if (type) f.type = type;
      if (language) f.language = language;
      if (typeof version === "number") f.version = version;
      if (content != null) {
        f.server = content;
        if (!f.dirty) {
          f.local = content;
          if (this.activeId === fileId) this._loadActiveIntoTextarea();
        }
      }
    }
    this.onChange();
  }

  hasFile(id) {
    return this.files.has(id);
  }
  getFile(id) {
    return this.files.get(id) || null;
  }
  list() {
    return [...this.files.values()];
  }
  getActive() {
    return this.activeId ? this.files.get(this.activeId) : null;
  }

  // --- Tabs ---------------------------------------------------------------

  openTab(fileId, { activate = true } = {}) {
    if (!this.files.has(fileId)) return;
    if (!this.openTabs.includes(fileId)) this.openTabs.push(fileId);
    if (activate) this.setActive(fileId);
    else this.onChange();
  }

  setActive(fileId) {
    if (!this.files.has(fileId)) return;
    this._flushTextareaToActive();
    this.activeId = fileId;
    if (!this.openTabs.includes(fileId)) this.openTabs.push(fileId);
    this._loadActiveIntoTextarea();
    this.onChange();
  }

  closeTab(fileId) {
    const idx = this.openTabs.indexOf(fileId);
    if (idx === -1) return;
    this.openTabs.splice(idx, 1);
    if (this.activeId === fileId) {
      const next = this.openTabs[idx] || this.openTabs[idx - 1] || null;
      if (next) {
        this.setActive(next);
      } else {
        this.activeId = null;
        this._setValue("");
        this.onChange();
      }
    } else {
      this.onChange();
    }
  }

  /** Remove a file entirely (e.g. after a deletion is applied). */
  removeFile(fileId) {
    if (!this.files.has(fileId)) return;
    this.files.delete(fileId);
    const idx = this.openTabs.indexOf(fileId);
    if (idx !== -1) this.openTabs.splice(idx, 1);
    if (this.activeId === fileId) {
      const next = this.openTabs[idx] || this.openTabs[idx - 1] || this.openTabs[0] || null;
      this.activeId = next;
      this._loadActiveIntoTextarea();
    }
    this.onChange();
  }

  getFileByName(name) {
    for (const f of this.files.values()) {
      if (f.name === name) return f;
    }
    return null;
  }

  // --- Sync from server ---------------------------------------------------

  /** Authoritative update from the server (another user or an agent). */
  applyRemote({ fileId, name, content, version, type, language }) {
    this.registerFile({ fileId, name, type, language, content, version });
  }

  /** The server acknowledged our own successful write. */
  ackSaved({ fileId, version }) {
    const f = this.files.get(fileId);
    if (!f) return;
    if (typeof version === "number") f.version = version;
    if (this.activeId === fileId) this._flushTextareaToActive();
    if (f.local === f.server) f.dirty = false;
    this.onChange();
  }

  /** Our write was rejected (stale version): adopt version and re-push if needed. */
  reconcileConflict({ fileId, content, version }) {
    const f = this.files.get(fileId);
    if (!f) return;
    if (typeof version === "number") f.version = version;
    f.server = content ?? f.server;
    if (this.activeId === fileId) this._flushTextareaToActive();
    if (f.local !== f.server) this._scheduleSend(0);
    else f.dirty = false;
    this.onChange();
  }

  /** Clear the "uncommitted to context" flag after a successful save. */
  markContextSaved(fileId) {
    const f = this.files.get(fileId);
    if (!f) return;
    f.contextDirty = false;
    this.onChange();
  }

  /** Send any pending edits of the active file immediately. */
  flush() {
    clearTimeout(this._timer);
    this._send();
  }

  isActiveContextDirty() {
    const f = this.getActive();
    return !!(f && f.contextDirty);
  }

  // --- Internals ----------------------------------------------------------

  _onInput() {
    if (this._suppressInput) return;
    const f = this.getActive();
    if (!f) return;
    f.local = this.textarea.value;
    f.dirty = true;
    f.contextDirty = true;
    this._scheduleSend(DEBOUNCE_MS);
    this.onChange();
  }

  _flushTextareaToActive() {
    const f = this.getActive();
    if (f) f.local = this.textarea.value;
  }

  _loadActiveIntoTextarea() {
    const f = this.getActive();
    this._setValue(f ? f.local : "");
  }

  _scheduleSend(delay) {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._send(), delay);
  }

  _send() {
    const f = this.getActive();
    if (!f) return;
    f.server = f.local; // optimistic baseline
    this.onSend(OUT.textEdit(f.fileId, f.local, f.version));
  }

  _setValue(value) {
    const el = this.textarea;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const atEnd = start === el.value.length && end === el.value.length;

    this._suppressInput = true;
    el.value = value;
    this._suppressInput = false;

    if (document.activeElement === el) {
      const p = atEnd ? value.length : Math.min(start, value.length);
      const q = atEnd ? value.length : Math.min(end, value.length);
      try {
        el.setSelectionRange(p, q);
      } catch (_) {
        /* ignore */
      }
    }
  }
}
