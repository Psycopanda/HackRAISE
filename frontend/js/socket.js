// Small event-driven WebSocket wrapper: typed handlers, outgoing queue,
// keep-alive ping and optional auto-reconnect.

export class Socket {
  constructor(url, { reconnect = true } = {}) {
    this.url = url;
    this.reconnect = reconnect;
    this.ws = null;
    this.handlers = new Map();
    this.queue = [];
    this.pingTimer = null;
    this._closedByUser = false;
  }

  connect() {
    this.ws = new WebSocket(this.url);

    this.ws.addEventListener("open", () => {
      this._emit("open");
      this._flush();
      this._startPing();
    });

    this.ws.addEventListener("message", (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      this._emit("message", data);
      if (data && data.type) this._emit(data.type, data);
    });

    this.ws.addEventListener("close", () => {
      this._stopPing();
      this._emit("close");
      if (this.reconnect && !this._closedByUser) {
        setTimeout(() => this.connect(), 1200);
      }
    });

    this.ws.addEventListener("error", (err) => this._emit("error", err));
    return this;
  }

  /** Register a handler for a message `type` (or lifecycle: open/close/error). */
  on(type, fn) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type).add(fn);
    return this;
  }

  off(type, fn) {
    this.handlers.get(type)?.delete(fn);
  }

  send(obj) {
    const payload = JSON.stringify(obj);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(payload);
    else this.queue.push(payload);
  }

  close() {
    this._closedByUser = true;
    this._stopPing();
    this.ws?.close();
  }

  _emit(type, payload) {
    this.handlers.get(type)?.forEach((fn) => {
      try {
        fn(payload);
      } catch (err) {
        console.error("[Socket handler error]", err);
      }
    });
  }

  _flush() {
    while (this.queue.length && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(this.queue.shift());
    }
  }

  _startPing() {
    this._stopPing();
    this.pingTimer = setInterval(() => this.send({ type: "ping" }), 25000);
  }

  _stopPing() {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }
}
