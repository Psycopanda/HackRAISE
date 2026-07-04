"use client";

import { useRef, useState, useEffect } from "react";
import { v4 as uuidv4 } from "uuid";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Star, Message as MessageIcon, AiUserCircle, UserPlus } from "pixelarticons/react";

let pdfjsLib: any = null;

// Initialize pdfjs only in browser
if (typeof window !== "undefined") {
  import("pdfjs-dist").then((module) => {
    pdfjsLib = module;
    pdfjsLib.GlobalWorkerOptions.workerSrc = "/pdf.worker.mjs";
  });
}
import {
  onAuthStateChangeListener,
  signInWithGoogle,
  logOut,
  loadConversations,
  loadMessages,
  saveMessage,
  shareConversation,
  subscribeToMessages,
  type Conversation,
  type Message,
} from "@/app/lib/firebase";
import { initSession, connectSessionSocket, type BackendEvent } from "@/app/lib/backend";

export default function ChatPage() {
  const [userId, setUserId] = useState<string | null>(null);
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [userPhoto, setUserPhoto] = useState<string | null>(null);
  const [userName, setUserName] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentConversationId, setCurrentConversationId] = useState<
    string | null
  >(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isDarkMode, setIsDarkMode] = useState(false);
  const [shareConvoId, setShareConvoId] = useState<string | null>(null);
  const [shareEmail, setShareEmail] = useState("");
  const [shareStatus, setShareStatus] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Array<{type: string; data: string; filename: string; mimeType: string; textContent?: string}>>([]);
  const [backendSessionId, setBackendSessionId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const wsSessionRef = useRef<string | null>(null);
  const pendingReplyRef = useRef<(() => void) | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Set up real-time listener for current conversation
  useEffect(() => {
    if (!currentConversationId) return;

    const unsubscribe = subscribeToMessages(currentConversationId, (messages) => {
      setMessages(messages);
    });

    return () => unsubscribe();
  }, [currentConversationId]);

  // Initialize and manage theme
  useEffect(() => {
    // Always start with light mode
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "light";
    setIsDarkMode(false);
  }, []);

  // Update theme when isDarkMode changes
  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add("dark");
      document.documentElement.style.colorScheme = "dark";
      localStorage.setItem("darkMode", "true");
    } else {
      document.documentElement.classList.remove("dark");
      document.documentElement.style.colorScheme = "light";
      localStorage.setItem("darkMode", "false");
    }
  }, [isDarkMode]);

  // Initialize Firebase auth
  useEffect(() => {
    const unsubscribe = onAuthStateChangeListener(async (user) => {
      if (user && user.email) {
        setUserId(user.uid);
        setUserEmail(user.email);
        setUserPhoto(user.photoURL);
        setUserName(user.displayName);
        const convos = await loadConversations(user.email);
        setConversations(convos);
      }
      setIsLoading(false);
    });

    return () => unsubscribe();
  }, []);

  // Opens the backend WebSocket for a given conversation + session, wiring up
  // the message handler that persists agent replies to Firestore. One live
  // socket == one live SystemAgent/PersonalAgent on the backend, so it must
  // be reused across messages rather than reopened per-send.
  const openSocket = (convoId: string, sessionCode: string): Promise<void> => {
    if (wsRef.current && wsSessionRef.current !== sessionCode) {
      wsRef.current.close();
      wsRef.current = null;
    }

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      return Promise.resolve();
    }

    const ws = connectSessionSocket(sessionCode, userId!);
    wsSessionRef.current = sessionCode;
    ws.onmessage = (event) => {
      try {
        const data: BackendEvent = JSON.parse(event.data);
        if (data.event === "agent_reply" || data.event === "session_activated") {
          if (userId && userEmail) {
            saveMessage(
              userId,
              userEmail,
              userName,
              userPhoto,
              convoId,
              {
                role: "assistant",
                content: data.message,
                createdAt: new Date(),
                authorName: "AI",
                authorEmail: "ai@assistant.local",
              },
              undefined,
              sessionCode
            );
          }
          pendingReplyRef.current?.();
          pendingReplyRef.current = null;
        }
      } catch (err) {
        console.error("Failed to parse backend event:", err);
      }
    };
    wsRef.current = ws;
    return new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("Backend WebSocket connection failed"));
    });
  };

  // Ensures a backend session + socket exist for a conversation, creating a
  // new session only if one doesn't already exist (lazy fallback path).
  const ensureSession = async (
    convoId: string,
    existingSessionId: string | null
  ): Promise<string> => {
    let sessionCode = existingSessionId;

    if (wsRef.current && wsSessionRef.current === sessionCode && wsRef.current.readyState === WebSocket.OPEN) {
      return sessionCode!;
    }

    if (!sessionCode) {
      const init = await initSession();
      sessionCode = init.session_code;
      setBackendSessionId(sessionCode);
    }

    await openSocket(convoId, sessionCode);
    return sessionCode;
  };

  const closeSession = () => {
    wsRef.current?.close();
    wsRef.current = null;
    wsSessionRef.current = null;
    pendingReplyRef.current = null;
  };

  // Close the backend socket when the component unmounts
  useEffect(() => {
    return () => closeSession();
  }, []);

  const handleNewChat = async () => {
    closeSession();
    const convoId = uuidv4();
    setCurrentConversationId(convoId);
    setMessages([]);
    setInput("");
    setBackendSessionId(null);

    if (!userId) return;
    try {
      const init = await initSession();
      setBackendSessionId(init.session_code);
      await openSocket(convoId, init.session_code);
    } catch (err) {
      console.error("Failed to start backend session:", err);
    }
  };

  const handleSelectConversation = async (convoId: string) => {
    if (!userId) return;
    closeSession();
    const convo = conversations.find((c) => c.id === convoId);
    const sessionId = convo?.backendSessionId ?? null;
    setBackendSessionId(sessionId);
    setCurrentConversationId(convoId);
    setInput("");

    if (sessionId) {
      ensureSession(convoId, sessionId).catch((err) =>
        console.error("Failed to reconnect backend session:", err)
      );
    }

    // Subscribe to real-time message updates
    const unsubscribe = subscribeToMessages(convoId, (messages) => {
      setMessages(messages);
    });

    // Store unsubscribe for cleanup
    return unsubscribe;
  };

  const handleShareConversation = async (convoId: string) => {
    if (!shareEmail.trim()) return;
    try {
      await shareConversation(convoId, shareEmail);
      setShareStatus(`Added ${shareEmail}`);
      setShareEmail("");
      if (userEmail) {
        const convos = await loadConversations(userEmail);
        setConversations(convos);
      }
    } catch (error) {
      console.error("Error sharing conversation:", error);
      setShareStatus("Failed to add member");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming || !userId || !userEmail) return;

    // Create or use current conversation
    let convoId = currentConversationId;
    if (!convoId) {
      convoId = uuidv4();
      setCurrentConversationId(convoId);
    }

    // Don't include document text in displayed message (only send to model)
    const userMessage: Message = {
      role: "user",
      content: input,
      createdAt: new Date(),
      authorEmail: userEmail,
      authorName: userName,
      authorPhoto: userPhoto,
      attachments: attachments.length > 0 ? attachments : undefined,
    };

    setMessages((prev) => [...prev, userMessage]);
    const messageToSend = input;
    setInput("");
    setAttachments([]);
    setIsStreaming(true);

    try {
      const sessionCode = await ensureSession(convoId, backendSessionId);

      // Save user message to Firestore. Passing the backend session id here is
      // only used the first time this conversation's doc is created; it's a
      // no-op on subsequent messages.
      await saveMessage(
        userId,
        userEmail,
        userName,
        userPhoto,
        convoId,
        userMessage,
        messageToSend.slice(0, 50),
        sessionCode
      );

      const docAttachments = attachments
        .filter((att) => att.textContent)
        .map((att) => ({ filename: att.filename, textContent: att.textContent }));

      await new Promise<void>((resolve) => {
        pendingReplyRef.current = resolve;
        wsRef.current!.send(
          JSON.stringify({
            action: "agent_chat",
            message: messageToSend,
            attachments: docAttachments.length > 0 ? docAttachments : undefined,
          })
        );
      });

      // Reload conversations to show updated timestamps
      const updatedConvos = await loadConversations(userEmail);
      setConversations(updatedConvos);
    } catch (error) {
      console.error("Error:", error);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Sorry, something went wrong. Please try again.",
        },
      ]);
    } finally {
      setIsStreaming(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as React.FormEvent);
    }
  };

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.currentTarget.files;
    if (!files) return;

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const reader = new FileReader();

      reader.onload = async (event) => {
        const base64 = (event.target?.result as string)?.split(",")[1] || "";
        let type = "document";
        let textContent = "";

        if (file.type.startsWith("image/")) {
          type = "image";
          addAttachment(type, base64, file.name, file.type, textContent);
        } else if (file.type === "text/plain" || file.name.endsWith(".txt")) {
          const textReader = new FileReader();
          textReader.onload = (textEvent) => {
            textContent = (textEvent.target?.result as string) || "";
            addAttachment("document", base64, file.name, file.type, textContent);
          };
          textReader.readAsText(file);
        } else if (file.type === "application/pdf" || file.name.endsWith(".pdf")) {
          // Extract PDF text so model can read it
          try {
            if (!pdfjsLib) {
              console.error("PDF library not loaded yet");
              addAttachment(type, base64, file.name, file.type, "");
              return;
            }

            const arrayBuffer = await file.arrayBuffer();
            const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
            let pdfText = "";

            for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
              const page = await pdf.getPage(pageNum);
              const textContent = await page.getTextContent();
              pdfText += textContent.items.map((item: any) => item.str).join(" ") + "\n";
            }

            addAttachment(type, base64, file.name, file.type, pdfText);
          } catch (pdfError) {
            console.error("Error extracting PDF text:", pdfError);
            addAttachment(type, base64, file.name, file.type, "");
          }
        } else {
          console.warn(`Unsupported file type: ${file.type}. Supported: images, PDFs, .txt files`);
        }
      };

      reader.readAsDataURL(file);
    }

    // Reset input
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const addAttachment = (
    type: string,
    data: string,
    filename: string,
    mimeType: string,
    textContent: string
  ) => {
    setAttachments((prev) => [
      ...prev,
      {
        type,
        data,
        filename,
        mimeType,
        textContent, // Store extracted text
      },
    ]);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-white dark:bg-black">
        <div className="text-zinc-600 dark:text-zinc-400">
          Initializing...
        </div>
      </div>
    );
  }

  if (!userId) {
    return (
      <div className="flex items-center justify-center h-screen bg-white dark:bg-black">
        <div className="text-center">
          <h1 className="text-4xl font-bold mb-4 text-black dark:text-white">
            Chat with AI
          </h1>
          <p className="text-zinc-600 dark:text-zinc-400 mb-8">
            Sign in with Google to access your conversations
          </p>
          <button
            onClick={signInWithGoogle}
            className="px-6 py-3 bg-blue-500 text-white rounded-lg font-medium hover:bg-blue-600 transition-colors"
          >
            Sign in with Google
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-white dark:bg-black">
      {/* Sidebar */}
      <div className="w-64 border-r border-zinc-300 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900 flex flex-col overflow-hidden">
        <button
          onClick={handleNewChat}
          className="m-4 px-4 py-2 bg-zinc-200 dark:bg-zinc-700 text-black dark:text-white rounded-lg font-medium hover:bg-zinc-300 dark:hover:bg-zinc-600 transition-colors"
        >
          + New Chat
        </button>

        <div className="flex-1 overflow-y-auto">
          {conversations.length === 0 ? (
            <div className="px-4 py-8 text-center text-zinc-500 dark:text-zinc-400 text-sm">
              No conversations yet
            </div>
          ) : (
            <div className="space-y-2 p-2">
              {conversations.map((convo) => (
                <div key={convo.id}>
                  <div
                    className={`flex items-center gap-1 rounded-lg transition-colors ${
                      currentConversationId === convo.id
                        ? "bg-zinc-300 dark:bg-zinc-700"
                        : "hover:bg-zinc-200 dark:hover:bg-zinc-800"
                    }`}
                  >
                    <button
                      onClick={() => handleSelectConversation(convo.id)}
                      className={`flex-1 text-left px-3 py-2 text-sm truncate ${
                        currentConversationId === convo.id
                          ? "text-black dark:text-white"
                          : "text-zinc-900 dark:text-zinc-100"
                      }`}
                    >
                      {convo.title}
                      {convo.members && convo.members.length > 1 && (
                        <span className="ml-1 text-xs text-zinc-500 dark:text-zinc-400">
                          ({convo.members.length})
                        </span>
                      )}
                    </button>
                    <button
                      onClick={() => {
                        setShareConvoId(
                          shareConvoId === convo.id ? null : convo.id
                        );
                        setShareEmail("");
                        setShareStatus(null);
                      }}
                      title="Invite by Gmail"
                      className="p-2 text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-white"
                    >
                      <UserPlus className="w-4 h-4" />
                    </button>
                  </div>

                  {shareConvoId === convo.id && (
                    <div className="mt-1 mb-2 px-2 space-y-2">
                      <div className="flex gap-1">
                        <input
                          type="email"
                          value={shareEmail}
                          onChange={(e) => setShareEmail(e.target.value)}
                          placeholder="name@gmail.com"
                          className="flex-1 min-w-0 px-2 py-1 text-sm rounded-lg border border-zinc-300 dark:border-zinc-600 bg-white dark:bg-zinc-900 text-black dark:text-white placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-[#EAB464]"
                        />
                        <button
                          onClick={() => handleShareConversation(convo.id)}
                          disabled={!shareEmail.trim()}
                          className="px-3 py-1 text-sm bg-[#EAB464] text-black rounded-lg font-medium hover:bg-[#dba450] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                          Add
                        </button>
                      </div>
                      {shareStatus && (
                        <p className="text-xs text-zinc-500 dark:text-zinc-400">
                          {shareStatus}
                        </p>
                      )}
                      {convo.members && convo.members.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {convo.members.map((email) => (
                            <span
                              key={email}
                              className="text-xs px-2 py-0.5 rounded-full bg-zinc-200 dark:bg-zinc-700 text-zinc-700 dark:text-zinc-300"
                            >
                              {email}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {userId && (
          <div className="border-t border-zinc-300 dark:border-zinc-700 p-4 space-y-3">
            <div className="flex items-center gap-3">
              {userPhoto && (
                <img
                  src={userPhoto}
                  alt={userName || "User"}
                  className="w-10 h-10 rounded-full"
                />
              )}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate">
                  {userName || "User"}
                </p>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 truncate">
                  {userId.slice(0, 8)}...
                </p>
              </div>
            </div>
            <button
              onClick={logOut}
              className="w-full px-4 py-2 bg-zinc-200 dark:bg-zinc-700 text-black dark:text-white text-sm rounded-lg font-medium hover:bg-zinc-300 dark:hover:bg-zinc-600 transition-colors"
            >
              Sign out
            </button>
          </div>
        )}
      </div>

      {/* Main content - split screen */}
      <div className="flex-1 flex">
        {/* Chat area - left side */}
        <div className="flex-1 flex flex-col border-r border-zinc-300 dark:border-zinc-700">
          {/* Header with theme toggle */}
          <div className="border-b border-zinc-300 dark:border-zinc-700 bg-white dark:bg-black p-4 flex justify-between items-center">
            <div className="flex items-center gap-2">
              <MessageIcon className="w-5 h-5 text-zinc-600 dark:text-zinc-400" />
              <h2 className="text-lg font-semibold text-black dark:text-white">Chat</h2>
            </div>
            <button
              onClick={() => setIsDarkMode(!isDarkMode)}
              className="p-2 rounded-lg bg-zinc-200 dark:bg-zinc-800 text-black dark:text-white hover:bg-zinc-300 dark:hover:bg-zinc-700 transition-colors"
              title={isDarkMode ? "Switch to light mode" : "Switch to dark mode"}
            >
              {isDarkMode ? "☀️" : "🌙"}
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && (
            <div className="flex items-center justify-center h-full text-zinc-500 dark:text-zinc-400">
              <div className="text-center">
                <h1 className="text-2xl font-semibold mb-2 text-black dark:text-white">
                  Chat with AI
                </h1>
                <p>Start a conversation by typing a message below.</p>
              </div>
            </div>
          )}

          {messages.map((msg, idx) => {
            const isCurrentUser = msg.authorEmail === userEmail;
            const isOwnMessage = msg.role === "user" && isCurrentUser;

            return (
              <div
                key={idx}
                className={`flex flex-col gap-1 ${
                  isOwnMessage ? "items-end" : "items-start"
                }`}
              >
                {!isCurrentUser && (msg.authorName || msg.authorEmail) && (
                  <div className={`text-xs text-zinc-500 dark:text-zinc-400 px-2 ${isOwnMessage ? "text-right" : "text-left"}`}>
                    {msg.authorName || msg.authorEmail}
                  </div>
                )}
                <div className="flex gap-3">
                  {/* Avatar/Photo on left for non-own messages, AI icon for assistant */}
                  {!isOwnMessage && msg.role === "user" && (
                    msg.authorPhoto ? (
                      <div className="flex-shrink-0 pt-2">
                        <img
                          src={msg.authorPhoto}
                          alt={msg.authorName || msg.authorEmail || "User"}
                          className="w-6 h-6 rounded-full"
                        />
                      </div>
                    ) : (
                      <div className="flex-shrink-0 pt-2 w-6 h-6 rounded-full bg-zinc-300 dark:bg-zinc-600 flex items-center justify-center text-xs text-zinc-700 dark:text-zinc-300 font-semibold">
                        {(msg.authorName || msg.authorEmail || "U")[0].toUpperCase()}
                      </div>
                    )
                  )}
                  {msg.role === "assistant" && !isOwnMessage && (
                    <div className="flex-shrink-0 pt-2">
                      <AiUserCircle className="w-6 h-6 text-zinc-500 dark:text-zinc-500" />
                    </div>
                  )}

                  {/* Message bubble in center */}
                  <div
                    className={`max-w-xs lg:max-w-md px-4 py-2 rounded-lg ${
                      isOwnMessage
                        ? "bg-[#EAB464] text-black rounded-br-none"
                        : "bg-zinc-200 dark:bg-zinc-800 text-black dark:text-white rounded-bl-none"
                    }`}
                  >
                    <div className="prose prose-sm dark:prose-invert max-w-none break-words prose-p:my-1 prose-pre:my-2 prose-ul:my-1 prose-ol:my-1 prose-headings:my-2">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content)}
                      </ReactMarkdown>
                    </div>
                    {msg.attachments && msg.attachments.length > 0 && (
                      <div className="mt-2 space-y-2">
                        {msg.attachments.map((att: any, attIdx: number) => (
                          <div key={attIdx}>
                            {att.type === "image" ? (
                              <img
                                src={`data:${att.mimeType};base64,${att.data}`}
                                alt={att.filename}
                                className="max-w-xs rounded-lg"
                              />
                            ) : (
                              <button
                                onClick={() => {
                                  const link = document.createElement("a");
                                  link.href = `data:${att.mimeType};base64,${att.data}`;
                                  link.download = att.filename;
                                  link.click();
                                }}
                                className="flex items-center gap-2 text-sm font-medium hover:opacity-80 transition-opacity"
                              >
                                <span>📄 {att.filename}</span>
                                <span>›</span>
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Photo on right for own messages */}
                  {isOwnMessage && (msg.authorPhoto || userPhoto) && (
                    <div className="flex-shrink-0 pt-2">
                      <img
                        src={msg.authorPhoto || userPhoto || ""}
                        alt={msg.authorName || "You"}
                        className="w-6 h-6 rounded-full"
                      />
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {isStreaming && (
            <div className="flex justify-start">
              <div className="px-4 py-2 text-zinc-500 dark:text-zinc-400 text-sm">
                ⏳ Thinking...
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

          {/* Input */}
          <div className="border-t border-zinc-300 dark:border-zinc-700 bg-white dark:bg-black p-4 space-y-3">
            {/* Attachment previews */}
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {attachments.map((att, idx) => (
                  <div key={idx} className="relative">
                    {att.type === "image" ? (
                      <img
                        src={`data:${att.mimeType};base64,${att.data}`}
                        alt={att.filename}
                        className="h-16 w-16 rounded-lg object-cover"
                      />
                    ) : (
                      <button
                        type="button"
                        className="flex items-center gap-1 px-3 py-2 bg-zinc-200 dark:bg-zinc-800 hover:bg-zinc-300 dark:hover:bg-zinc-700 rounded-lg text-sm font-medium text-zinc-900 dark:text-zinc-100 transition-colors"
                      >
                        <span className="truncate">{att.filename}</span>
                        <span>›</span>
                      </button>
                    )}
                    <button
                      onClick={() => setAttachments((prev) => prev.filter((_, i) => i !== idx))}
                      className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full w-5 h-5 flex items-center justify-center text-xs hover:bg-red-600"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}

            <form onSubmit={handleSubmit} className="flex gap-2">
              <input
                ref={fileInputRef}
                type="file"
                onChange={handleFileSelect}
                multiple
                className="hidden"
                accept="image/*,.pdf,.txt,.doc,.docx"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="px-3 py-2 bg-zinc-200 dark:bg-zinc-800 text-black dark:text-white rounded-lg font-medium hover:bg-zinc-300 dark:hover:bg-zinc-700 transition-colors"
                title="Upload file"
              >
                📎
              </button>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type a message... (Shift+Enter for newline)"
                disabled={isStreaming}
                className="flex-1 px-4 py-2 rounded-lg border border-zinc-300 dark:border-zinc-600 bg-white dark:bg-zinc-900 text-black dark:text-white placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none disabled:opacity-50"
                rows={1}
              />
              <button
                type="submit"
                disabled={isStreaming || !input.trim()}
                className="px-4 py-2 bg-[#EAB464] text-black rounded-lg font-medium hover:bg-[#dba450] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                Send
              </button>
            </form>
          </div>
        </div>

        {/* Right side - Linear tickets and UI generation */}
        <div className="w-80 bg-zinc-50 dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-800 flex flex-col overflow-hidden">
          {/* Linear Tickets Section */}
          <div className="flex-1 border-b border-zinc-300 dark:border-zinc-700 overflow-y-auto p-4 space-y-3">
            <h3 className="font-semibold text-zinc-900 dark:text-white text-sm">Linear Tickets</h3>
            {[
              { id: "LIN-1", title: "Chat UI responsive design", status: "In Progress", priority: "High" },
              { id: "LIN-2", title: "Add real-time messaging", status: "Done", priority: "High" },
              { id: "LIN-3", title: "PDF upload support", status: "In Progress", priority: "Medium" },
            ].map((ticket) => (
              <div key={ticket.id} className="p-2 bg-white dark:bg-zinc-800 rounded-lg border border-zinc-200 dark:border-zinc-700 text-xs space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-zinc-900 dark:text-white">{ticket.id}</span>
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                    ticket.status === "Done" ? "bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300" :
                    "bg-yellow-100 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300"
                  }`}>
                    {ticket.status}
                  </span>
                </div>
                <p className="text-zinc-600 dark:text-zinc-400">{ticket.title}</p>
                <span className={`inline-block px-2 py-0.5 rounded text-xs ${
                  ticket.priority === "High" ? "bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300" :
                  "bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300"
                }`}>
                  {ticket.priority}
                </span>
              </div>
            ))}
          </div>

          {/* UI Generation Section */}
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            <h3 className="font-semibold text-zinc-900 dark:text-white text-sm">Generated UI</h3>
            {[
              { name: "Dashboard", version: "v2.1", status: "Ready", icon: "📊" },
              { name: "Settings Panel", version: "v1.3", status: "Building", icon: "⚙️" },
              { name: "User Profile", version: "v3.0", status: "Ready", icon: "👤" },
            ].map((ui) => (
              <div key={ui.name} className="p-2 bg-white dark:bg-zinc-800 rounded-lg border border-zinc-200 dark:border-zinc-700 text-xs space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span>{ui.icon}</span>
                    <span className="font-medium text-zinc-900 dark:text-white">{ui.name}</span>
                  </div>
                  <span className="text-zinc-500 dark:text-zinc-400">{ui.version}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                    ui.status === "Ready" ? "bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300" :
                    "bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300"
                  }`}>
                    {ui.status}
                  </span>
                  <button className="text-blue-600 dark:text-blue-400 hover:underline text-xs font-medium">
                    Preview →
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
