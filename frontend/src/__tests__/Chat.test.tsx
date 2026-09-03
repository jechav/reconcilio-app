import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Chat } from "../pages/Chat";
import { saveSession } from "../session";

function renderChat() {
  return render(
    <MemoryRouter initialEntries={["/chat"]}>
      <Routes>
        <Route path="/chat" element={<Chat />} />
      </Routes>
    </MemoryRouter>,
  );
}

const ownerSession = {
  access_token: "test-token",
  token_type: "bearer",
  user: { id: "u1", email: "owner@example.com" },
  organization: { id: "o1", name: "Acme Tax" },
  role: "owner" as const,
};

const existingSession = {
  id: "session-1",
  user_id: "u1",
  title: null,
  created_at: "2026-08-20T10:00:00Z",
};

const userMessage = {
  id: "msg-1",
  session_id: "session-1",
  role: "user" as const,
  content: "How much did I spend on flights?",
  citations: [],
  created_at: "2026-08-20T10:01:00Z",
};

const assistantMessage = {
  id: "msg-2",
  session_id: "session-1",
  role: "assistant" as const,
  content: "You spent $770 on flights (see Transaction abc, Transaction def).",
  citations: [
    { source_type: "transaction", source_id: "txn-1", document_id: "doc-1", transaction_id: "txn-1" },
    { source_type: "transaction", source_id: "txn-2", document_id: "doc-2", transaction_id: "txn-2" },
  ],
  created_at: "2026-08-20T10:01:05Z",
};

function mockFetchImplementation(url: string, init?: RequestInit) {
  if (url.endsWith("/chat/sessions") && (!init || init.method === undefined)) {
    return Promise.resolve({ ok: true, json: async () => [existingSession] });
  }
  if (url.endsWith("/chat/sessions") && init?.method === "POST") {
    return Promise.resolve({ ok: true, json: async () => existingSession });
  }
  if (url.includes(`/chat/sessions/${existingSession.id}/messages`) && init?.method === "POST") {
    return Promise.resolve({ ok: true, json: async () => [userMessage, assistantMessage] });
  }
  if (url.includes(`/chat/sessions/${existingSession.id}/messages`)) {
    return Promise.resolve({ ok: true, json: async () => [] });
  }
  return Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: "not found" }) });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => mockFetchImplementation(String(input), init)),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("chat", () => {
  it("lists existing chat sessions", async () => {
    saveSession(ownerSession);
    renderChat();

    await waitFor(() => expect(screen.getByRole("button", { name: /8\/20\/2026|20\/08\/2026/i })).toBeInTheDocument());
  });

  it("asks a question and renders the cited answer", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderChat();

    await waitFor(() => expect(screen.getByLabelText("Question")).toBeInTheDocument());

    await user.type(screen.getByLabelText("Question"), "How much did I spend on flights?");
    await user.click(screen.getByRole("button", { name: /^ask$/i }));

    await waitFor(() => expect(screen.getByText(/you spent \$770 on flights/i)).toBeInTheDocument());

    const sources = screen.getByLabelText(`Sources for message ${assistantMessage.id}`);
    expect(sources).toBeInTheDocument();
    expect(sources.textContent).toContain("Transaction txn-1");
    expect(sources.textContent).toContain("Transaction txn-2");
  });

  it("redirects to login when no session exists", () => {
    render(
      <MemoryRouter initialEntries={["/chat"]}>
        <Routes>
          <Route path="/chat" element={<Chat />} />
          <Route path="/login" element={<div>Login page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Login page")).toBeInTheDocument();
  });
});
