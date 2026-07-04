const BACKEND_HTTP_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

const BACKEND_WS_URL = BACKEND_HTTP_URL.replace(/^http/, "ws");

export interface InitSessionResponse {
  session_code: string;
  status: string;
}

export async function initSession(): Promise<InitSessionResponse> {
  const response = await fetch(`${BACKEND_HTTP_URL}/sessions/init`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("Failed to initialize backend session");
  return response.json();
}

export function connectSessionSocket(
  sessionCode: string,
  clientId: string
): WebSocket {
  return new WebSocket(
    `${BACKEND_WS_URL}/ws/${sessionCode}/${encodeURIComponent(clientId)}`
  );
}

export interface MasterContext {
  project_title?: string;
  project_goal?: string;
  language?: string;
  guidelines?: string[];
}

export type BackendEvent =
  | { event: "agent_reply"; status: string; message: string }
  | { event: "session_activated"; message: string; master_context: MasterContext }
  | { event: "file_modified"; client_id: string; filename: string; content: string }
  | { event: "context_updated"; message: string }
  | { event: "user_left"; client_id: string };
