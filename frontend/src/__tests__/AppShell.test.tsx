import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { App } from "../App";
import { saveSession } from "../session";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
});

describe("app shell", () => {
  it("redirects to login without a session", () => {
    renderAt("/upload");
    expect(screen.getByRole("heading", { name: /log in/i })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: /main/i })).not.toBeInTheDocument();
  });

  it("shows navigation, the org and the role for a signed-in user, and logs out", async () => {
    saveSession({
      access_token: "t",
      token_type: "bearer",
      user: { id: "u1", email: "owner@example.com" },
      organization: { id: "o1", name: "Acme Tax" },
      role: "owner",
    });
    const user = userEvent.setup();

    renderAt("/upload");

    const nav = screen.getByRole("navigation", { name: /main/i });
    expect(nav).toHaveTextContent("Dashboard");
    expect(screen.getByRole("link", { name: "Upload" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("Acme Tax")).toBeInTheDocument();
    expect(screen.getByText("owner")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /log out/i }));

    expect(await screen.findByRole("heading", { name: /log in/i })).toBeInTheDocument();
    expect(localStorage.getItem("reconcilio.session")).toBeNull();
  });
});
