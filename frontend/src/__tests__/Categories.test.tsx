import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Categories } from "../pages/Categories";
import { saveSession } from "../session";

function renderCategories() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/categories"]}>
        <Routes>
          <Route path="/categories" element={<Categories />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const ownerSession = {
  access_token: "test-token",
  token_type: "bearer",
  user: { id: "u1", email: "owner@example.com" },
  organization: { id: "o1", name: "Acme Tax" },
  role: "owner" as const,
};

let categories: { id: string; name: string; created_at: string }[];

function respond(status: number, body?: unknown) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: async () => body,
  });
}

function fakeApi(url: string, init?: RequestInit) {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;
  const id = /\/categories\/([^/?]+)/.exec(url)?.[1];

  if (url.endsWith("/categories") && method === "GET") return respond(200, categories);
  if (url.endsWith("/categories") && method === "POST") {
    if (categories.some((c) => c.name === body.name)) {
      return respond(409, { detail: "Category already exists" });
    }
    const created = {
      id: `cat-${categories.length + 1}`,
      name: body.name,
      created_at: "2026-09-01T00:00:00Z",
    };
    categories = [...categories, created].sort((a, b) => a.name.localeCompare(b.name));
    return respond(201, created);
  }
  if (id && method === "PATCH") {
    categories = categories.map((c) => (c.id === id ? { ...c, name: body.name } : c));
    return respond(
      200,
      categories.find((c) => c.id === id),
    );
  }
  if (id && method === "DELETE") {
    categories = categories.filter((c) => c.id !== id);
    return respond(204);
  }
  return respond(404, { detail: "not found" });
}

beforeEach(() => {
  localStorage.clear();
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

describe("categories", () => {
  it("lists categories and lets an owner manage them", async () => {
    saveSession(ownerSession);
    renderCategories();

    expect(await screen.findByText("Travel")).toBeInTheDocument();
    expect(screen.getByText("Office Supplies")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add category" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rename Travel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Travel" })).toBeInTheDocument();
  });

  it("adds a category", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderCategories();
    await screen.findByText("Travel");

    await user.click(screen.getByRole("button", { name: "Add category" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Category name"), "Client gifts");
    await user.click(within(dialog).getByRole("button", { name: "Create category" }));

    expect(await screen.findByText("Client gifts")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/categories"),
      expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Client gifts" }) }),
    );
  });

  it("validates an empty name without calling the API", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderCategories();
    await screen.findByText("Travel");

    await user.click(screen.getByRole("button", { name: "Add category" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Create category" }));

    expect(await within(dialog).findByText("Enter a category name.")).toBeInTheDocument();
    const posts = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
      (call) => (call[1] as RequestInit | undefined)?.method === "POST",
    );
    expect(posts).toHaveLength(0);
  });

  it("shows the API error when the name already exists", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderCategories();
    await screen.findByText("Travel");

    await user.click(screen.getByRole("button", { name: "Add category" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Category name"), "Travel");
    await user.click(within(dialog).getByRole("button", { name: "Create category" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Category already exists");
  });

  it("renames a category", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderCategories();
    await screen.findByText("Travel");

    await user.click(screen.getByRole("button", { name: "Rename Travel" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Category name");
    expect(input).toHaveValue("Travel");
    await user.clear(input);
    await user.type(input, "Business travel");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Business travel")).toBeInTheDocument();
    expect(screen.queryByText("Travel")).not.toBeInTheDocument();
  });

  it("deletes a category after confirmation", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderCategories();
    await screen.findByText("Travel");

    await user.click(screen.getByRole("button", { name: "Delete Travel" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/become\s+uncategorized/);
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(screen.queryByText("Travel")).not.toBeInTheDocument());
    expect(screen.getByText("Office Supplies")).toBeInTheDocument();
  });

  it("is read-only for a regular member", async () => {
    saveSession({ ...ownerSession, role: "member" });
    renderCategories();

    expect(await screen.findByText("Travel")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add category" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rename Travel" })).not.toBeInTheDocument();
    expect(screen.getByText(/only an owner or admin can add/i)).toBeInTheDocument();
  });
});
