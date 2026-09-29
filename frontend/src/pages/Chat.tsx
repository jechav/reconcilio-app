import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Plus, Receipt, SendHorizonal } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";

import {
  ApiError,
  createChatSession,
  listChatMessages,
  listChatSessions,
  postChatMessage,
  type ChatMessageOut,
  type ChatSessionOut,
} from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { getSession } from "@/session";

const sessionDateFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

function sessionLabel(session: ChatSessionOut): string {
  return session.title ?? sessionDateFormat.format(new Date(session.created_at));
}

function shortId(id: string | null): string {
  return (id ?? "").slice(0, 8);
}

function citationLabel(citation: ChatMessageOut["citations"][number]): string {
  return citation.source_type === "transaction"
    ? `Transaction ${shortId(citation.transaction_id)}`
    : `Document ${shortId(citation.document_id)}`;
}

function MessageBubble({ message }: { message: ChatMessageOut }) {
  const isUser = message.role === "user";
  return (
    <li
      data-role={message.role}
      className={cn("flex flex-col gap-2", isUser ? "items-end" : "items-start")}
    >
      <span className="text-xs font-semibold text-gray-500">{isUser ? "You" : "Assistant"}</span>
      <p
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-6 whitespace-pre-wrap",
          isUser
            ? "rounded-tr-sm bg-navy-600 text-white"
            : "rounded-tl-sm border bg-white text-gray-900",
        )}
      >
        {message.content}
      </p>
      {message.citations.length > 0 && (
        <ul
          aria-label={`Sources for message ${message.id}`}
          className="flex max-w-[85%] flex-wrap gap-1.5"
        >
          {message.citations.map((citation, index) => {
            const Icon = citation.source_type === "transaction" ? Receipt : FileText;
            return (
              <li
                key={`${citation.source_type}-${citation.source_id}-${index}`}
                className="flex items-center gap-1.5 rounded-full border bg-gray-50 px-2.5 py-1 text-xs text-gray-700"
              >
                <Icon className="size-3 text-gray-500" aria-hidden />
                {citationLabel(citation)}
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}

export function Chat() {
  const queryClient = useQueryClient();
  const session = getSession();
  const token = session?.access_token ?? "";
  const enabled = session !== null;

  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const sessions = useQuery({
    queryKey: ["chat", "sessions"],
    queryFn: () => listChatSessions(token),
    enabled,
  });
  const messages = useQuery({
    queryKey: ["chat", "messages", activeSessionId],
    queryFn: () => listChatMessages(token, activeSessionId!),
    enabled: enabled && activeSessionId !== null,
  });

  const addSession = (created: ChatSessionOut) => {
    queryClient.setQueryData<ChatSessionOut[]>(["chat", "sessions"], (prev) => [
      created,
      ...(prev ?? []),
    ]);
    queryClient.setQueryData<ChatMessageOut[]>(["chat", "messages", created.id], []);
    setActiveSessionId(created.id);
  };

  const newChat = useMutation({
    mutationFn: () => createChatSession(token),
    onSuccess: addSession,
  });

  const ask = useMutation({
    mutationFn: async (content: string) => {
      let sessionId = activeSessionId;
      if (!sessionId) {
        const created = await createChatSession(token);
        addSession(created);
        sessionId = created.id;
      }
      const added = await postChatMessage(token, sessionId, content);
      return { sessionId, added };
    },
    onSuccess: ({ sessionId, added }) => {
      queryClient.setQueryData<ChatMessageOut[]>(["chat", "messages", sessionId], (prev) => [
        ...(prev ?? []),
        ...added,
      ]);
      setQuestion("");
    },
  });

  const messageCount = messages.data?.length ?? 0;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messageCount, ask.isPending]);

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  function handleAsk(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || ask.isPending) return;
    ask.mutate(question);
  }

  const failure = sessions.error ?? messages.error ?? newChat.error ?? ask.error ?? null;
  const fallback = sessions.error
    ? "Failed to load chat sessions."
    : messages.error
      ? "Failed to load messages."
      : newChat.error
        ? "Failed to start a new chat."
        : "Failed to send message.";
  const errorMessage = failure ? (failure instanceof ApiError ? failure.message : fallback) : null;
  const list = messages.data ?? [];

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
          Ask your books
        </h1>
        <p className="text-sm text-gray-500">
          Ask a question about your Organization&apos;s Transactions and Documents. Answers cite
          their sources.
        </p>
      </header>

      {errorMessage && (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
          {errorMessage}
        </p>
      )}

      <div className="flex min-h-0 flex-1 gap-6">
        <aside className="flex w-64 shrink-0 flex-col gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={() => newChat.mutate()}
            disabled={newChat.isPending}
            className="h-10 justify-start"
          >
            <Plus aria-hidden />
            New chat
          </Button>
          <section aria-label="Chat sessions" className="min-h-0 flex-1 overflow-y-auto">
            <ul className="flex flex-col gap-1">
              {(sessions.data ?? []).map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => setActiveSessionId(s.id)}
                    aria-current={activeSessionId === s.id}
                    className={cn(
                      "w-full truncate rounded-lg px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100",
                      activeSessionId === s.id && "bg-navy-50 font-semibold text-navy-600",
                    )}
                  >
                    {sessionLabel(s)}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl border bg-gray-50">
          <section
            aria-label="Chat messages"
            ref={scrollRef}
            className="flex-1 overflow-y-auto p-6"
          >
            {messages.isFetching && list.length === 0 && (
              <p className="text-sm text-gray-500">Loading…</p>
            )}
            {!messages.isFetching && list.length === 0 && !ask.isPending && (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
                <p className="text-base font-semibold text-gray-900">
                  Ask anything about your books
                </p>
                <p className="max-w-sm text-sm text-gray-500">
                  For example: &ldquo;How much did I spend on travel last month?&rdquo;
                </p>
              </div>
            )}
            <ul className="flex flex-col gap-5">
              {list.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}
              {ask.isPending && (
                <li className="text-sm text-gray-500" aria-live="polite">
                  Thinking…
                </li>
              )}
            </ul>
          </section>

          <form
            aria-label="Ask a question"
            onSubmit={handleAsk}
            className="flex items-end gap-3 border-t bg-white p-4"
          >
            <div className="flex flex-1 flex-col gap-1.5">
              <Label htmlFor="chat-question" className="sr-only">
                Question
              </Label>
              <Input
                id="chat-question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="How much did I spend on travel last month?"
                className="h-11"
              />
            </div>
            <Button type="submit" disabled={ask.isPending} className="h-11">
              {ask.isPending ? "Asking…" : "Ask"}
              {!ask.isPending && <SendHorizonal aria-hidden />}
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
