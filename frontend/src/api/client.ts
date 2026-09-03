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

export type TransactionStatus = "needs_review" | "resolved";

export interface TransactionOut {
  id: string;
  document_id: string;
  line_number: number;
  description: string;
  amount: string;
  txn_date: string;
  confidence: number;
  status: TransactionStatus;
  category_id: string | null;
  category_confidence: number | null;
}

export interface CategorySummaryOut {
  category_id: string | null;
  category_name: string;
  income: string;
  expenses: string;
  transaction_count: number;
}

export interface DashboardSummaryOut {
  start_date: string;
  end_date: string;
  income_total: string;
  expenses_total: string;
  net_total: string;
  categories: CategorySummaryOut[];
}

export interface DashboardFlagsOut {
  start_date: string;
  end_date: string;
  unmatched_bank_transactions: TransactionOut[];
  unmatched_expense_transactions: TransactionOut[];
}

export function getDashboardSummary(
  token: string,
  startDate: string,
  endDate: string,
): Promise<DashboardSummaryOut> {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  return getJson<DashboardSummaryOut>(`/dashboard/summary?${params.toString()}`, token);
}

export function getDashboardSummaryTransactions(
  token: string,
  startDate: string,
  endDate: string,
  categoryId: string | null,
): Promise<TransactionOut[]> {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  if (categoryId) {
    params.set("category_id", categoryId);
  }
  return getJson<TransactionOut[]>(`/dashboard/summary/transactions?${params.toString()}`, token);
}

export function getDashboardFlags(
  token: string,
  startDate: string,
  endDate: string,
): Promise<DashboardFlagsOut> {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  return getJson<DashboardFlagsOut>(`/dashboard/flags?${params.toString()}`, token);
}
