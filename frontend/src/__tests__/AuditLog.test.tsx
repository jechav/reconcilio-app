import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuditLog } from "../pages/AuditLog";
import { saveSession } from "../session";

function renderAuditLog() {
  return render(
    <MemoryRouter initialEntries={["/audit-log"]}>
      <Routes>
        <Route path="/audit-log" element={<AuditLog />} />
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

const memberSession = {
  ...ownerSession,
  role: "member" as const,
};

const entries = [
  {
    id: "entry-1",
    entity_type: "transaction",
    entity_id: "txn-1",
    actor: "u1",
    actor_email: "owner@example.com",
    action: "transaction.category_corrected",
    before: { category_id: null },
    after: { category_id: "cat-1" },
    created_at: "2026-08-20T10:00:00Z",
  },
  {
    id: "entry-2",
    entity_type: "document",
    entity_id: "doc-1",
    actor: "system",
    actor_email: null,
    action: "document.extracted",
    before: { status: "processing" },
    after: { path: "ocr" },
    created_at: "2026-08-19T10:00:00Z",
  },
];

function mockFetchImplementation(url: string) {
  if (url.includes("/audit-log")) {
    return Promise.resolve({ ok: true, json: async () => entries });
  }
  return Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: "not found" }) });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => mockFetchImplementation(String(input))),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("audit log", () => {
  it("renders the chronological list of entries for an owner", async () => {
    saveSession(ownerSession);
    renderAuditLog();

    await waitFor(() => expect(screen.getByText("transaction.category_corrected")).toBeInTheDocument());
    expect(screen.getByText("document.extracted")).toBeInTheDocument();
    expect(screen.getByText("owner@example.com")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
  });

  it("expands an entry to show its before/after diff", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderAuditLog();

    await waitFor(() => expect(screen.getByText("transaction.category_corrected")).toBeInTheDocument());

    await user.click(screen.getByText("transaction.category_corrected"));

    const diff = await screen.findByLabelText("Before/after diff for transaction.category_corrected");
    expect(within(diff).getByText("category_id")).toBeInTheDocument();
    expect(within(diff).getByText("cat-1")).toBeInTheDocument();
  });

  it("sends filters to the API when the filter form is submitted", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderAuditLog();

    await waitFor(() => expect(screen.getByText("transaction.category_corrected")).toBeInTheDocument());

    await user.type(screen.getByLabelText("Entity type"), "transaction");
    await user.click(screen.getByRole("button", { name: /apply filters/i }));

    await waitFor(() => {
      const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
      const lastUrl = String(calls[calls.length - 1][0]);
      expect(lastUrl).toContain("entity_type=transaction");
    });
  });

  it("tells a non-owner/admin member the audit log is restricted", async () => {
    saveSession(memberSession);
    renderAuditLog();

    expect(screen.getByRole("alert")).toHaveTextContent(/only an owner or admin/i);
  });
});
