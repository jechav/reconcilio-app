import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, type TokenResponse } from "./api/client";
import { saveSession } from "./session";

export function useAuthSubmit() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function run(action: () => Promise<TokenResponse>) {
    setError(null);
    setSubmitting(true);
    try {
      const session = await action();
      saveSession(session);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return { error, submitting, run };
}
