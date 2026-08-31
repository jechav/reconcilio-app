import type { TokenResponse } from "./api/client";

const STORAGE_KEY = "reconcilio.session";

export function saveSession(session: TokenResponse): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

export function getSession(): TokenResponse | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw ? (JSON.parse(raw) as TokenResponse) : null;
}

export function clearSession(): void {
  localStorage.removeItem(STORAGE_KEY);
}
