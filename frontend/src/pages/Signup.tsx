import { useState } from "react";
import type { FormEvent } from "react";

import { signup } from "../api/client";
import { useAuthSubmit } from "../useAuthSubmit";

export function Signup() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const { error, submitting, run } = useAuthSubmit();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void run(() => signup(email, password, orgName));
  }

  return (
    <form onSubmit={handleSubmit} aria-label="Sign up">
      <h1>Sign up</h1>

      <label htmlFor="org-name">Organization name</label>
      <input
        id="org-name"
        name="org_name"
        type="text"
        required
        value={orgName}
        onChange={(event) => setOrgName(event.target.value)}
      />

      <label htmlFor="signup-email">Email</label>
      <input
        id="signup-email"
        name="email"
        type="email"
        required
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />

      <label htmlFor="signup-password">Password</label>
      <input
        id="signup-password"
        name="password"
        type="password"
        required
        minLength={8}
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />

      {error && <p role="alert">{error}</p>}

      <button type="submit" disabled={submitting}>
        {submitting ? "Signing up…" : "Sign up"}
      </button>
    </form>
  );
}
