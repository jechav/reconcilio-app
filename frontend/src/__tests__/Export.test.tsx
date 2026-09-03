import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Export } from "../pages/Export";
import { saveSession } from "../session";

function renderExport() {
  return render(
    <MemoryRouter initialEntries={["/export"]}>
      <Routes>
        <Route path="/export" element={<Export />} />
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

const csvBody = "id,document_id,txn_date,description,amount,category,review_status,match_status\n";

function mockFetchImplementation(url: string) {
  if (url.includes("/export/transactions") && url.includes("format=csv")) {
    return Promise.resolve({
      ok: true,
      headers: new Headers({
        "Content-Disposition": 'attachment; filename="transactions_2026-03-01_2026-03-31.csv"',
      }),
      blob: async () => new Blob([csvBody], { type: "text/csv" }),
    });
  }
  if (url.includes("/export/transactions") && url.includes("format=json")) {
    return Promise.resolve({
      ok: true,
      headers: new Headers({
        "Content-Disposition": 'attachment; filename="transactions_2026-03-01_2026-03-31.json"',
      }),
      blob: async () => new Blob(["[]"], { type: "application/json" }),
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
  vi.stubGlobal("URL.createObjectURL", vi.fn(() => "blob:mock-url"));
  vi.stubGlobal("URL.revokeObjectURL", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("export", () => {
  it("downloads a CSV export for the selected date range", async () => {
    const user = userEvent.setup();
    renderExport();

    await user.click(screen.getByRole("button", { name: /download csv/i }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/export/transactions?"),
        expect.objectContaining({ headers: { Authorization: "Bearer test-token" } }),
      ),
    );
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find((c) =>
      String(c[0]).includes("format=csv"),
    );
    expect(call).toBeTruthy();
  });

  it("downloads a JSON export for the selected date range", async () => {
    const user = userEvent.setup();
    renderExport();

    await user.click(screen.getByRole("button", { name: /download json/i }));

    await waitFor(() => {
      const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find((c) =>
        String(c[0]).includes("format=json"),
      );
      expect(call).toBeTruthy();
    });
  });

  it("shows an error message when the export request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({ ok: false, status: 422, json: async () => ({ detail: "start_date must not be after end_date" }) }),
      ),
    );
    const user = userEvent.setup();
    renderExport();

    await user.click(screen.getByRole("button", { name: /download csv/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("start_date must not be after end_date"),
    );
  });
});
