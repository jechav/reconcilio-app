import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Dashboard } from "../pages/Dashboard";
import { saveSession } from "../session";

function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
      </Routes>
    </MemoryRouter>,
  );
}

const session = {
  access_token: "test-token",
  token_type: "bearer",
  user: { id: "u1", email: "owner@example.com" },
  organization: { id: "o1", name: "Acme Tax" },
  role: "owner" as const,
};

const summary = {
  start_date: "2026-08-01",
  end_date: "2026-08-31",
  income_total: "1000.00",
  expenses_total: "-200.00",
  net_total: "800.00",
  categories: [
    {
      category_id: "cat-1",
      category_name: "Travel",
      income: "0",
      expenses: "-200.00",
      transaction_count: 1,
    },
  ],
};

const flags = {
  start_date: "2026-08-01",
  end_date: "2026-08-31",
  unmatched_bank_transactions: [
    {
      id: "txn-bank-1",
      document_id: "doc-bank-1",
      line_number: 1,
      description: "Mystery charge",
      amount: "-20.00",
      txn_date: "2026-08-05",
      confidence: 0.9,
      status: "resolved",
      category_id: null,
      category_confidence: null,
    },
  ],
  unmatched_expense_transactions: [],
};

const travelTransactions = [
  {
    id: "txn-travel-1",
    document_id: "doc-travel-1",
    line_number: 1,
    description: "Airline Co",
    amount: "-200.00",
    txn_date: "2026-08-10",
    confidence: 0.9,
    status: "resolved",
    category_id: "cat-1",
    category_confidence: 1.0,
  },
];

const document = {
  id: "doc-travel-1",
  filename: "airline-receipt.pdf",
  content_type: "application/pdf",
  size_bytes: 2048,
  doc_type: "invoice_or_receipt",
  status: "done",
  created_at: "2026-08-10T00:00:00Z",
  updated_at: "2026-08-10T00:00:00Z",
};

function mockFetchImplementation(url: string) {
  if (url.includes("/dashboard/summary/transactions")) {
    return Promise.resolve({ ok: true, json: async () => travelTransactions });
  }
  if (url.includes("/dashboard/summary")) {
    return Promise.resolve({ ok: true, json: async () => summary });
  }
  if (url.includes("/dashboard/flags")) {
    return Promise.resolve({ ok: true, json: async () => flags });
  }
  if (url.includes("/documents/doc-travel-1")) {
    return Promise.resolve({ ok: true, json: async () => document });
  }
  if (url.includes("/documents/doc-bank-1")) {
    return Promise.resolve({
      ok: true,
      json: async () => ({ ...document, id: "doc-bank-1", filename: "bank-statement.csv" }),
    });
  }
  return Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: "not found" }) });
}

beforeEach(() => {
  localStorage.clear();
  saveSession(session);
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => mockFetchImplementation(String(input))),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("dashboard", () => {
  it("renders the income/expense summary grouped by category", async () => {
    renderDashboard();

    await waitFor(() => expect(screen.getByText(/Travel/)).toBeInTheDocument());

    const summarySection = screen.getByLabelText("Income and expense summary");
    expect(within(summarySection).getByText("-200.00")).toBeInTheDocument();
    expect(screen.getByText(/Income: 1000.00/)).toBeInTheDocument();

    const flagsSection = screen.getByLabelText("Missing documentation flags");
    expect(within(flagsSection).getByText(/Mystery charge/)).toBeInTheDocument();
  });

  it("drills down from a summary line to its transactions and the source document", async () => {
    const user = userEvent.setup();
    renderDashboard();

    await waitFor(() => expect(screen.getByText("Travel")).toBeInTheDocument());

    await user.click(screen.getByText("Travel"));

    await waitFor(() => expect(screen.getByText(/Airline Co/)).toBeInTheDocument());

    const drillDown = screen.getByLabelText("Category drill-down");
    await user.click(within(drillDown).getByRole("button", { name: /view document/i }));

    await waitFor(() =>
      expect(screen.getByTestId("document-preview")).toHaveTextContent("airline-receipt.pdf"),
    );
  });

  it("drills down from a missing-documentation flag to its source document", async () => {
    const user = userEvent.setup();
    renderDashboard();

    await waitFor(() => expect(screen.getByText(/Mystery charge/)).toBeInTheDocument());

    const flagsSection = screen.getByLabelText("Missing documentation flags");
    await user.click(within(flagsSection).getByRole("button", { name: /view document/i }));

    await waitFor(() => expect(screen.getByTestId("document-preview")).toBeInTheDocument());
  });
});
