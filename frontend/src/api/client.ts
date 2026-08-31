export interface UserOut {
  id: string;
  email: string;
}

export interface OrganizationOut {
  id: string;
  name: string;
}

export type OrgRole = "owner" | "admin" | "member";

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: UserOut;
  organization: OrganizationOut;
  role: OrgRole;
}

export class ApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const detail = await response
      .json()
      .then((data: { detail?: string }) => data.detail)
      .catch(() => undefined);
    throw new ApiError(detail ?? `Request to ${path} failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function signup(email: string, password: string, orgName: string): Promise<TokenResponse> {
  return postJson<TokenResponse>("/auth/signup", { email, password, org_name: orgName });
}

export function login(email: string, password: string): Promise<TokenResponse> {
  return postJson<TokenResponse>("/auth/login", { email, password });
}
