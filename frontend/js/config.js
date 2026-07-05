// Central configuration.
//
// Resolves the backend HTTP + WebSocket base URLs. When this frontend is
// served by FastAPI (same origin) everything is automatic. If you open it from
// another origin (e.g. VS Code Live Server), set API_OVERRIDE below.

const API_OVERRIDE = ""; // e.g. "http://localhost:8000"

function resolveHttpBase() {
  if (API_OVERRIDE) return API_OVERRIDE.replace(/\/$/, "");
  const { protocol, host } = window.location;
  if (protocol === "http:" || protocol === "https:") return `${protocol}//${host}`;
  return "http://localhost:8000"; // opened via file://
}

export const HTTP_BASE = resolveHttpBase();
export const WS_BASE = HTTP_BASE.replace(/^http/, "ws");
