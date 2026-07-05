// Thin async HTTP client for the FastAPI REST endpoints.

import { HTTP_BASE } from "./config.js";

async function request(path, { method = "GET", body } = {}) {
  const res = await fetch(`${HTTP_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data && data.detail) detail = data.detail;
    } catch (_) {
      /* ignore non-JSON error bodies */
    }
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  return res.json();
}

export const Api = {
  createSession: (title) =>
    request("/api/sessions", { method: "POST", body: { title } }),

  joinSession: (accessCode, displayName) =>
    request("/api/sessions/join", {
      method: "POST",
      body: { access_code: accessCode, display_name: displayName },
    }),

  getContext: (sessionId) => request(`/api/sessions/${sessionId}/context`),
  getFiles: (sessionId) => request(`/api/sessions/${sessionId}/files`),
  health: () => request("/api/health"),
};
