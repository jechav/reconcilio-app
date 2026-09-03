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

async function parseErrorDetail(response: Response, path: string): Promise<string> {
  const detail = await response
    .json()
    .then((data: { detail?: string }) => data.detail)
    .catch(() => undefined);
  return detail ?? `Request to ${path} failed with status ${response.status}`;
}

async function postJson<T>(path: string, body: unknown, token?: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(await parseErrorDetail(response, path));
  }

  return response.json() as Promise<T>;
}

async function getJson<T>(path: string, token: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!response.ok) {
    throw new ApiError(await parseErrorDetail(response, path));
  }

  return response.json() as Promise<T>;
}

export function signup(email: string, password: string, orgName: string): Promise<TokenResponse> {
  return postJson<TokenResponse>("/auth/signup", { email, password, org_name: orgName });
}

export function login(email: string, password: string): Promise<TokenResponse> {
  return postJson<TokenResponse>("/auth/login", { email, password });
}

export type DocumentType = "invoice_or_receipt" | "bank_statement";
export type DocumentStatus =
  | "queued"
  | "processing"
  | "needs_review"
  | "done"
  | "failed";

export interface DocumentOut {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  doc_type: DocumentType;
  status: DocumentStatus;
  created_at: string;
  updated_at: string;
}

export interface DocumentUploadResponse {
  document: DocumentOut;
  upload_url: string;
}

export function requestDocumentUpload(
  token: string,
  file: File,
  docType: DocumentType,
): Promise<DocumentUploadResponse> {
  return postJson<DocumentUploadResponse>(
    "/documents",
    {
      filename: file.name,
      content_type: file.type || "application/octet-stream",
      size_bytes: file.size,
      doc_type: docType,
    },
    token,
  );
}

export async function putFileToUploadUrl(uploadUrl: string, file: File): Promise<void> {
  const response = await fetch(uploadUrl, {
    method: "PUT",
    body: file,
  });
  if (!response.ok) {
    throw new ApiError(`Upload to storage failed with status ${response.status}`);
  }
}

export function completeDocumentUpload(token: string, documentId: string): Promise<DocumentOut> {
  return postJson<DocumentOut>(`/documents/${documentId}/complete`, {}, token);
}

export function getDocument(token: string, documentId: string): Promise<DocumentOut> {
  return getJson<DocumentOut>(`/documents/${documentId}`, token);
}
