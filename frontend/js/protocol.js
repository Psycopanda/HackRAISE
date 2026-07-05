// Standardised message envelope for CoVibe.
//
// Application-level model: every message is an object of the form
//     { type, sender, content, ... }
// where the three logical categories are: chat_message | file_update | agent_status.
//
// The FastAPI backend emits a slightly richer set of wire types; `normalize()`
// folds them back onto these three categories so the UI code stays simple,
// while `OUT` builds the outgoing frames the backend expects (each also carries
// `sender` and `content` to honour the shared envelope).

export const OUT = {
  chat: (content, sender) => ({ type: "chat", sender, content }),

  textEdit: (fileId, content, baseVersion, sender) => ({
    type: "text_edit",
    sender,
    content,
    file_id: fileId,
    base_version: baseVersion,
  }),

  cursor: (fileId, position) => ({ type: "cursor", file_id: fileId, position }),
  ping: () => ({ type: "ping" }),
};

/** Map a raw backend message onto { category, sender, content, meta, raw }. */
export function normalize(msg) {
  switch (msg.type) {
    case "system_message":
      return { category: "chat_message", sender: "Agent système", content: msg.content, raw: msg };
    case "agent_message":
      return { category: "chat_message", sender: "Agent", content: msg.content, raw: msg };
    case "agent_status":
      return { category: "agent_status", sender: "Agent", content: msg.status, meta: msg, raw: msg };
    case "task_claimed":
      return { category: "agent_status", sender: msg.task?.agent_id, content: "intention", meta: msg.task, raw: msg };
    case "task_completed":
      return { category: "agent_status", sender: msg.task?.agent_id, content: "completed", meta: msg.task, raw: msg };
    case "task_aborted":
      return { category: "agent_status", sender: msg.task?.agent_id, content: "aborted", meta: msg.task, raw: msg };
    case "file_update":
      return { category: "file_update", sender: msg.sender || "Agent", content: msg.file?.content, meta: msg.file, raw: msg };
    default:
      return { category: msg.type, sender: msg.sender, content: msg.content, meta: msg, raw: msg };
  }
}

/** The backend derives a personal agent id from the user id as `agent:<id>`. */
export function agentIdFor(userId) {
  return `agent:${userId}`;
}
