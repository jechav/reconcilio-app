import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Home } from "../pages/Home";
import { Login } from "../pages/Login";
import { Signup } from "../pages/Signup";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
      </Routes>
    </MemoryRouter>,
  );
}

const tokenResponse = {
  access_token: "test-token",
  token_type: "bearer",
  user: { id: "u1", email: "owner@example.com" },
  organization: { id: "o1", name: "Acme Tax" },
  role: "owner",
};

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("signup flow", () => {
  it("submits the form and lands on the org home page", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => tokenResponse,
    });
    const user = userEvent.setup();

    renderAt("/signup");
    await user.type(screen.getByLabelText(/organization name/i), "Acme Tax");
    await user.type(screen.getByLabelText(/email/i), "owner@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-horse");
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/signup"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          email: "owner@example.com",
          password: "correct-horse",
          org_name: "Acme Tax",
        }),
      }),
    );
    await waitFor(() => expect(screen.getByText("Acme Tax")).toBeInTheDocument());
    expect(screen.getByText(/owner@example.com/)).toBeInTheDocument();
  });

  it("shows the API error message on a failed signup", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ detail: "Email already registered" }),
    });
    const user = userEvent.setup();

    renderAt("/signup");
    await user.type(screen.getByLabelText(/organization name/i), "Acme Tax");
    await user.type(screen.getByLabelText(/email/i), "owner@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-horse");
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Email already registered");
  });
});

describe("login flow", () => {
  it("submits credentials and lands on the org home page", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => tokenResponse,
    });
    const user = userEvent.setup();

    renderAt("/login");
    await user.type(screen.getByLabelText(/email/i), "owner@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-horse");
    await user.click(screen.getByRole("button", { name: /log in/i }));

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/login"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ email: "owner@example.com", password: "correct-horse" }),
      }),
    );
    await waitFor(() => expect(screen.getByText("Acme Tax")).toBeInTheDocument());
  });

  it("shows an error message on invalid credentials", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Incorrect email or password" }),
    });
    const user = userEvent.setup();

    renderAt("/login");
    await user.type(screen.getByLabelText(/email/i), "owner@example.com");
    await user.type(screen.getByLabelText(/password/i), "wrong");
    await user.click(screen.getByRole("button", { name: /log in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password");
  });
});
