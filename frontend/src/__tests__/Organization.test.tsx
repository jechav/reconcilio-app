import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AcceptInvite } from "../pages/AcceptInvite";
import { Organization } from "../pages/Organization";
import { getSession, saveSession } from "../session";

function renderPage(element: React.ReactNode, path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={path} element={element} />
          <Route path="/" element={<div>Home page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const ownerSession = {
  access_token: "test-token",
  token_type: "bearer",
  user: { id: "u1", email: "owner@example.com" },
  organization: { id: "o1", name: "Acme Tax", confidence_threshold: "0.80" },
  role: "owner" as const,
};

let members: unknown[];

function respond(status: number, body?: unknown) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body });
}

function fakeApi(url: string, init?: RequestInit) {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;

  if (url.endsWith("/orgs/me/members") && method === "GET") return respond(200, members);
  if (url.endsWith("/orgs/me/members") && method === "POST") {
    const created = {
      id: "m-new",
      user: { id: "u-new", email: body.email },
      role: body.role,
      created_at: "2026-09-01T00:00:00Z",
    };
    members = [...members, created];
    return respond(201, created);
  }
  if (url.endsWith("/orgs/me/llm-usage")) {
    return respond(200, [{ provider: "anthropic", model: "claude-haiku-4-5", calls: 1284 }]);
  }
  if (url.endsWith("/orgs/me/settings") && method === "PATCH") {
    return respond(200, { id: "o1", name: "Acme Tax", confidence_threshold: "0.90" });
  }
  if (url.endsWith("/auth/accept-invite")) {
    if (body.email === "nobody@example.com") {
      return respond(404, { detail: "No pending invite for this email" });
    }
    return respond(200, { ...ownerSession, role: "member" });
  }
  return respond(404, { detail: "not found" });
}

beforeEach(() => {
  localStorage.clear();
  members = [
    {
      id: "m1",
      user: { id: "u1", email: "owner@example.com" },
      role: "owner",
      created_at: "2026-08-01T00:00:00Z",
    },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => fakeApi(String(input), init)),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("organization settings", () => {
  it("shows members and AI usage", async () => {
    saveSession(ownerSession);
    renderPage(<Organization />, "/organization");

    const membersSection = await screen.findByLabelText("Members");
    expect(await within(membersSection).findByText("owner@example.com")).toBeInTheDocument();
    expect(within(membersSection).getByText("Owner")).toBeInTheDocument();
    const usage = screen.getByLabelText("AI usage");
    expect(await within(usage).findByText("claude-haiku-4-5")).toBeInTheDocument();
    expect(within(usage).getByText("1,284")).toBeInTheDocument();
  });

  it("saves a new confidence threshold and updates the stored session", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderPage(<Organization />, "/organization");

    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Confidence threshold"), { target: { value: "0.9" } });
    expect(screen.getByText("0.90")).toBeInTheDocument();
    await user.click(saveButton);

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/orgs/me/settings"),
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ confidence_threshold: 0.9 }),
        }),
      ),
    );
    await waitFor(() => expect(getSession()?.organization.confidence_threshold).toBe("0.90"));
  });

  it("invites a member", async () => {
    const user = userEvent.setup();
    saveSession(ownerSession);
    renderPage(<Organization />, "/organization");

    await user.click(await screen.findByRole("button", { name: /invite member/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Email address"), "maria@example.com");
    await user.click(within(dialog).getByRole("radio", { name: "Admin" }));
    await user.click(within(dialog).getByRole("button", { name: "Add member" }));

    expect(await screen.findByText("Member added")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/orgs/me/members"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ email: "maria@example.com", role: "admin" }),
      }),
    );
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Done" }));
    expect(await screen.findByText("maria@example.com")).toBeInTheDocument();
  });

  it("is read-only for a non-owner", async () => {
    saveSession({ ...ownerSession, role: "admin" });
    renderPage(<Organization />, "/organization");

    expect(await screen.findByText("owner@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /invite member/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Confidence threshold")).toBeDisabled();
    expect(screen.getByText("Only the owner can change this.")).toBeInTheDocument();
  });
});

describe("accept invite", () => {
  it("sets a password and lands on the app", async () => {
    const user = userEvent.setup();
    renderPage(<AcceptInvite />, "/accept-invite");

    await user.type(screen.getByLabelText("Email"), "maria@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Join organization" }));

    expect(await screen.findByText("Home page")).toBeInTheDocument();
    expect(getSession()?.role).toBe("member");
  });

  it("shows the API error when there is no pending invite", async () => {
    const user = userEvent.setup();
    renderPage(<AcceptInvite />, "/accept-invite");

    await user.type(screen.getByLabelText("Email"), "nobody@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Join organization" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No pending invite for this email");
  });
});
