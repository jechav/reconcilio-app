import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Navigate } from "react-router-dom";

import {
  ApiError,
  type ChatMessageOut,
  type ChatSessionOut,
  createChatSession,
  listChatMessages,
  listChatSessions,
  postChatMessage,
} from "../api/client";
import { getSession } from "../session";

function citationLabel(citation: ChatMessageOut["citations"][number]): string {
  return citation.source_type === "transaction"
    ? `Transaction ${citation.transaction_id}`
    : `Document ${citation.document_id}`;
}

function ChatMessageItem({ message }: { message: ChatMessageOut }) {
  return (
    <li data-role={message.role}>
      <p>
        <strong>{message.role === "user" ? "You" : "Assistant"}:</strong> {message.content}
      </p>
      {message.citations.length > 0 && (
        <ul aria-label={`Sources for message ${message.id}`}>
          {message.citations.map((citation, index) => (
            <li key={`${citation.source_type}-${citation.source_id}-${index}`}>{citationLabel(citation)}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function Chat() {
  const session = getSession();
  const [sessions, setSessions] = useState<ChatSessionOut[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageOut[]>([]);
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);

  if (!session) {
    return <Navigate to="/login" replace />;
  }
  const token = session.access_token;

  useEffect(() => {
    void (async () => {
      try {
        const existing = await listChatSessions(token);
        setSessions(existing);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Failed to load chat sessions.");
      }
    })();
    // Intentionally load once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadMessages(sessionId: string) {
    setError(null);
    setLoading(true);
    try {
      const data = await listChatMessages(token, sessionId);
      setMessages(data);
      setActiveSessionId(sessionId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load messages.");
    } finally {
      setLoading(false);
    }
  }

  async function handleNewSession() {
    setError(null);
    try {
      const newSession = await createChatSession(token);
      setSessions((prev) => [newSession, ...prev]);
      setMessages([]);
      setActiveSessionId(newSession.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start a new chat.");
    }
  }

  async function handleAsk(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) {
      return;
    }
    setError(null);
    setSending(true);
    try {
      let sessionId = activeSessionId;
      if (!sessionId) {
        const newSession = await createChatSession(token);
        setSessions((prev) => [newSession, ...prev]);
        sessionId = newSession.id;
        setActiveSessionId(sessionId);
      }
      const newMessages = await postChatMessage(token, sessionId, question);
      setMessages((prev) => [...prev, ...newMessages]);
      setQuestion("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to send message.");
    } finally {
      setSending(false);
    }
  }

  return (
    <div>
      <h1>Chat</h1>
      <p>Ask a question about your Organization's Transactions and Documents. Answers cite their sources.</p>

      {error && <p role="alert">{error}</p>}

      <button type="button" onClick={() => void handleNewSession()}>
        New chat
      </button>

      <section aria-label="Chat sessions">
        <ul>
          {sessions.map((s) => (
            <li key={s.id}>
              <button type="button" onClick={() => void loadMessages(s.id)} aria-current={activeSessionId === s.id}>
                {s.title ?? new Date(s.created_at).toLocaleString()}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Chat messages">
        {loading && <p>Loading…</p>}
        <ul>
          {messages.map((message) => (
            <ChatMessageItem key={message.id} message={message} />
          ))}
        </ul>
      </section>

      <form aria-label="Ask a question" onSubmit={(event) => void handleAsk(event)}>
        <label htmlFor="chat-question">Question</label>
        <input
          id="chat-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="How much did I spend on travel last month?"
        />
        <button type="submit" disabled={sending}>
          {sending ? "Asking…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
