import { useState } from "react";
import type { FormEvent } from "react";

import { login } from "../api/client";
import { useAuthSubmit } from "../useAuthSubmit";

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { error, submitting, run } = useAuthSubmit();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void run(() => login(email, password));
  }

  return (
    <form onSubmit={handleSubmit} aria-label="Log in">
      <h1>Log in</h1>

      <label htmlFor="login-email">Email</label>
      <input
        id="login-email"
        name="email"
        type="email"
        required
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />

      <label htmlFor="login-password">Password</label>
      <input
        id="login-password"
        name="password"
        type="password"
        required
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />

      {error && <p role="alert">{error}</p>}

      <button type="submit" disabled={submitting}>
        {submitting ? "Logging in…" : "Log in"}
      </button>
    </form>
  );
}
