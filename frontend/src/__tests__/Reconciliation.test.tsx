import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { TransactionOut } from "../api/client";
import { Reconciliation } from "../pages/Reconciliation";
import { TransactionDetail } from "../pages/TransactionDetail";
import { saveSession } from "../session";

function renderAt(path: string, ui: React.ReactNode, route: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={route} element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const session = {
  access_token: "test-token",
  token_type: "bearer",
  user: { id: "u1", email: "owner@example.com" },
  organization: { id: "o1", name: "Acme Tax" },
  role: "owner" as const,
};

function txn(
  id: string,
  description: string,
  amount: string,
  date: string,
  extra: Partial<TransactionOut> = {},
): TransactionOut {
  return {
    id,
    document_id: `doc-${id}`,
    line_number: 1,
    description,
    amount,
    txn_date: date,
    confidence: 0.9,
    status: "resolved",
    category_id: null,
    category_confidence: null,
    ...extra,
  };
}

let transactions: TransactionOut[];
let bankIds: string[];
let expenseIds: string[];
let matches: {
  id: string;
  bank_transaction_id: string;
  expense_transaction_id: string;
  match_type: string;
  confidence: number;
  actor: string;
  created_at: string;
}[];
let categories: { id: string; name: string; created_at: string }[];

function respond(status: number, body?: unknown) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body });
}

function fakeApi(url: string, init?: RequestInit) {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;
  const byId = (id: string) => transactions.find((t) => t.id === id);
  const matched = new Set(
    matches.flatMap((m) => [m.bank_transaction_id, m.expense_transaction_id]),
  );

  if (url.includes("/reconciliation/transactions/unmatched")) {
    const ids = url.includes("side=bank_statement") ? bankIds : expenseIds;
    return respond(200, ids.filter((id) => !matched.has(id)).map(byId));
  }
  if (url.endsWith("/reconciliation/matches") && method === "GET") return respond(200, matches);
  if (url.endsWith("/reconciliation/matches") && method === "POST") {
    const created = {
      id: `match-${matches.length + 1}`,
      bank_transaction_id: body.bank_transaction_id,
      expense_transaction_id: body.expense_transaction_id,
      match_type: "manual",
      confidence: 1,
      actor: "u1",
      created_at: "2026-09-01T00:00:00Z",
    };
    matches = [...matches, created];
    return respond(201, created);
  }
  const matchId = /\/reconciliation\/matches\/([^/]+)$/.exec(url)?.[1];
  if (matchId && method === "DELETE") {
    matches = matches.filter((m) => m.id !== matchId);
    return respond(204);
  }
  const txnId = /\/transactions\/([^/]+?)(\/category)?$/.exec(url);
  if (txnId && method === "PUT") {
    const updated = { ...byId(txnId[1])!, category_id: body.category_id, category_confidence: 1 };
    transactions = transactions.map((t) => (t.id === updated.id ? updated : t));
    return respond(200, updated);
  }
  if (txnId && method === "GET") return respond(200, byId(txnId[1]));
  if (url.endsWith("/categories")) return respond(200, categories);
  if (/\/documents\/doc-/.test(url)) {
    return respond(200, {
      id: "doc-b1",
      filename: "chase-statement-sep.pdf",
      content_type: "application/pdf",
      size_bytes: 10,
      doc_type: "bank_statement",
      status: "done",
      created_at: "x",
      updated_at: "x",
    });
  }
  return respond(404, { detail: "not found" });
}

beforeEach(() => {
  localStorage.clear();
  saveSession(session);
  transactions = [
    txn("b1", "AMZN Mktp US", "-214.90", "2026-09-03"),
    txn("e1", "Amazon Web Services", "214.90", "2026-09-02"),
    txn("e2", "Staples", "86.20", "2026-09-07"),
  ];
  bankIds = ["b1"];
  expenseIds = ["e1", "e2"];
  matches = [];
  categories = [
    { id: "cat-a", name: "Office Supplies", created_at: "2026-08-01T00:00:00Z" },
    { id: "cat-b", name: "Travel", created_at: "2026-08-02T00:00:00Z" },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => fakeApi(String(input), init)),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reconciliation", () => {
  it("summarizes unmatched items on each side", async () => {
    renderAt("/reconciliation", <Reconciliation />, "/reconciliation");

    expect(await screen.findByText("AMZN Mktp US")).toBeInTheDocument();
    const summary = screen.getByRole("group", { name: "Reconciliation summary" });
    expect(within(summary).getByText("Unmatched bank transactions")).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /unmatched invoices & receipts \(2\)/i }),
    ).toBeInTheDocument();
  });

  it("manually matches a bank transaction, ranking the closest candidate first", async () => {
    const user = userEvent.setup();
    renderAt("/reconciliation", <Reconciliation />, "/reconciliation");

    await user.click(await screen.findByRole("button", { name: "Match AMZN Mktp US" }));
    const dialog = await screen.findByRole("dialog");
    const options = await within(dialog).findAllByRole("radio");
    expect(options[0]).toHaveTextContent("Amazon Web Services");
    expect(options[0]).toHaveTextContent("Same amount");
    expect(within(dialog).getByRole("button", { name: "Confirm match" })).toBeDisabled();

    await user.click(options[0]);
    await user.click(within(dialog).getByRole("button", { name: "Confirm match" }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/reconciliation/matches"),
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ bank_transaction_id: "b1", expense_transaction_id: "e1" }),
        }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Every bank transaction is matched.")).toBeInTheDocument();
  });

  it("matches from the invoices side with the ids in the right slots", async () => {
    const user = userEvent.setup();
    renderAt("/reconciliation", <Reconciliation />, "/reconciliation");

    await user.click(await screen.findByRole("tab", { name: /unmatched invoices/i }));
    await user.click(await screen.findByRole("button", { name: "Match Amazon Web Services" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("radio", { name: /AMZN Mktp US/ }));
    await user.click(within(dialog).getByRole("button", { name: "Confirm match" }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/reconciliation/matches"),
        expect.objectContaining({
          body: JSON.stringify({ bank_transaction_id: "b1", expense_transaction_id: "e1" }),
        }),
      ),
    );
  });

  it("lists matches and unmatches one after confirmation", async () => {
    matches = [
      {
        id: "match-1",
        bank_transaction_id: "b1",
        expense_transaction_id: "e1",
        match_type: "automatic",
        confidence: 0.94,
        actor: "system",
        created_at: "2026-09-01T00:00:00Z",
      },
    ];
    const user = userEvent.setup();
    renderAt("/reconciliation", <Reconciliation />, "/reconciliation");

    await user.click(await screen.findByRole("tab", { name: /matches \(1\)/i }));
    expect(await screen.findByText("Auto 94%")).toBeInTheDocument();
    expect(await screen.findByText("AMZN Mktp US")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Unmatch" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Unmatch" }));

    expect(await screen.findByText("No matches yet.")).toBeInTheDocument();
  });

  it("shows the API error when a match is rejected", async () => {
    const user = userEvent.setup();
    renderAt("/reconciliation", <Reconciliation />, "/reconciliation");
    const original = fetch as unknown as ReturnType<typeof vi.fn>;
    const impl = original.getMockImplementation()!;
    original.mockImplementation((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith("/reconciliation/matches") && init?.method === "POST"
        ? respond(422, { detail: "Transaction is already matched" })
        : impl(input, init),
    );

    await user.click(await screen.findByRole("button", { name: "Match AMZN Mktp US" }));
    const dialog = await screen.findByRole("dialog");
    await user.click((await within(dialog).findAllByRole("radio"))[0]);
    await user.click(within(dialog).getByRole("button", { name: "Confirm match" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Transaction is already matched",
    );
  });
});

describe("transaction detail", () => {
  it("shows the transaction, its match status and source document", async () => {
    renderAt("/transactions/b1", <TransactionDetail />, "/transactions/:id");

    expect(await screen.findByRole("heading", { name: "AMZN Mktp US" })).toBeInTheDocument();
    expect(await screen.findByText("Unmatched")).toBeInTheDocument();
    expect(await screen.findByText("chase-statement-sep.pdf")).toBeInTheDocument();
    expect(screen.getByText("Currently uncategorized.", { exact: false })).toBeInTheDocument();
  });

  it("corrects the category", async () => {
    const user = userEvent.setup();
    renderAt("/transactions/b1", <TransactionDetail />, "/transactions/:id");

    const select = await screen.findByRole("combobox", { name: "Category" });
    await waitFor(() =>
      expect(within(select).getByRole("option", { name: "Travel" })).toBeInTheDocument(),
    );
    const save = screen.getByRole("button", { name: "Save category" });
    expect(save).toBeDisabled();

    await user.selectOptions(select, "Travel");
    await user.click(save);

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/transactions/b1/category"),
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ category_id: "cat-b" }),
        }),
      ),
    );
    expect(await screen.findByText(/Currently Travel/)).toBeInTheDocument();
  });

  it("reports a transaction that does not exist", async () => {
    renderAt("/transactions/nope", <TransactionDetail />, "/transactions/:id");

    expect(await screen.findByRole("alert")).toHaveTextContent(/not found|failed/i);
  });
});

describe("routing", () => {
  it("shows a 404 page inside the app shell for unknown routes", async () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/no-such-page"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });
});
