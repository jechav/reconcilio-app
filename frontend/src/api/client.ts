export interface UserOut {
  id: string;
  email: string;
}

export interface OrganizationOut {
  id: string;
  name: string;
  /** Decimal string in (0, 1]. Snapshot from login/signup; older stored sessions lack it. */
  confidence_threshold?: string;
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
export type DocumentStatus = "queued" | "processing" | "needs_review" | "done" | "failed";

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

export interface AuditLogEntryOut {
  id: string;
  entity_type: string;
  entity_id: string;
  actor: string;
  actor_email: string | null;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditLogFilters {
  entityType?: string;
  actor?: string;
  action?: string;
  startDate?: string;
  endDate?: string;
}

/** Chronological AuditLogEntry list for the caller's Organization,
 * owner/admin only (issue #10, AC1/AC3). Every filter is optional and
 * stacks with the others -- see backend/app/routers/audit.py. */
export function getAuditLog(
  token: string,
  filters: AuditLogFilters = {},
): Promise<AuditLogEntryOut[]> {
  const params = new URLSearchParams();
  if (filters.entityType) params.set("entity_type", filters.entityType);
  if (filters.actor) params.set("actor", filters.actor);
  if (filters.action) params.set("action", filters.action);
  if (filters.startDate) params.set("start_date", filters.startDate);
  if (filters.endDate) params.set("end_date", filters.endDate);
  const query = params.toString();
  return getJson<AuditLogEntryOut[]>(`/audit-log${query ? `?${query}` : ""}`, token);
}

export interface ChatSessionOut {
  id: string;
  user_id: string;
  title: string | null;
  created_at: string;
}

export interface ChatCitationOut {
  source_type: "document" | "transaction";
  source_id: string;
  document_id: string;
  transaction_id: string | null;
}

export type ChatRole = "user" | "assistant";

export interface ChatMessageOut {
  id: string;
  session_id: string;
  role: ChatRole;
  content: string;
  citations: ChatCitationOut[];
  created_at: string;
}

/** Creates a new ChatSession for the caller's Organization (issue #11, AC6). */
export function createChatSession(token: string): Promise<ChatSessionOut> {
  return postJson<ChatSessionOut>("/chat/sessions", {}, token);
}

/** Every ChatSession in the caller's Organization, newest first -- every
 * member has uniform visibility into the Organization's chat history, same
 * as any other Organization-scoped data (see CONTEXT.md, OrgMembership). */
export function listChatSessions(token: string): Promise<ChatSessionOut[]> {
  return getJson<ChatSessionOut[]>("/chat/sessions", token);
}

export function listChatMessages(token: string, sessionId: string): Promise<ChatMessageOut[]> {
  return getJson<ChatMessageOut[]>(`/chat/sessions/${sessionId}/messages`, token);
}

/** Asks the read-only chat agent one question in an existing session
 * (backend/app/chat/agent.py) and returns the new user + assistant message
 * pair, the assistant one carrying citations to the Documents/Transactions
 * its answer actually drew on (issue #11, AC5). */
export function postChatMessage(
  token: string,
  sessionId: string,
  content: string,
): Promise<ChatMessageOut[]> {
  return postJson<ChatMessageOut[]>(`/chat/sessions/${sessionId}/messages`, { content }, token);
}

export type ExportFormat = "csv" | "json";

export interface ExportDownload {
  blob: Blob;
  filename: string;
}

function filenameFromContentDisposition(header: string | null, fallback: string): string {
  if (!header) {
    return fallback;
  }
  const match = /filename="?([^"]+)"?/.exec(header);
  return match ? match[1] : fallback;
}

/** Downloads every Transaction in a date range as CSV or JSON, for an
 * accountant or tax software (issue #9). Every Transaction in range comes
 * back regardless of processing state, with `category`, `review_status`,
 * and `match_status` columns -- see backend/app/routers/export.py. */
export async function exportTransactions(
  token: string,
  startDate: string,
  endDate: string,
  format: ExportFormat,
): Promise<ExportDownload> {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate, format });
  const path = `/export/transactions?${params.toString()}`;
  const response = await fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new ApiError(await parseErrorDetail(response, path));
  }
  const blob = await response.blob();
  const filename = filenameFromContentDisposition(
    response.headers.get("Content-Disposition"),
    `transactions_${startDate}_${endDate}.${format}`,
  );
  return { blob, filename };
}

async function sendJson<T>(
  method: "PATCH" | "PUT" | "DELETE",
  path: string,
  token: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(await parseErrorDetail(response, path));
  }

  return (response.status === 204 ? undefined : await response.json()) as T;
}

export interface CategoryOut {
  id: string;
  name: string;
  created_at: string;
}

export function listCategories(token: string): Promise<CategoryOut[]> {
  return getJson<CategoryOut[]>("/categories", token);
}

/** Owner/admin only (issue #5, AC2). */
export function createCategory(token: string, name: string): Promise<CategoryOut> {
  return postJson<CategoryOut>("/categories", { name }, token);
}

export function updateCategory(token: string, id: string, name: string): Promise<CategoryOut> {
  return sendJson<CategoryOut>("PATCH", `/categories/${id}`, token, { name });
}

/** Un-sets the Category on its Transactions; it never deletes them. */
export function deleteCategory(token: string, id: string): Promise<void> {
  return sendJson<void>("DELETE", `/categories/${id}`, token);
}

export interface MembershipOut {
  id: string;
  user: UserOut;
  role: OrgRole;
  created_at: string;
}

export interface LlmUsageOut {
  provider: string;
  model: string;
  calls: number;
}

export function listMembers(token: string): Promise<MembershipOut[]> {
  return getJson<MembershipOut[]>("/orgs/me/members", token);
}

/** Owner only. The invitee sets a password through `acceptInvite`. */
export function inviteMember(token: string, email: string, role: OrgRole): Promise<MembershipOut> {
  return postJson<MembershipOut>("/orgs/me/members", { email, role }, token);
}

export function acceptInvite(email: string, password: string): Promise<TokenResponse> {
  return postJson<TokenResponse>("/auth/accept-invite", { email, password });
}

/** Owner only: fields below this confidence are sent through llm_refine. */
export function updateOrgSettings(
  token: string,
  confidenceThreshold: number,
): Promise<OrganizationOut> {
  return sendJson<OrganizationOut>("PATCH", "/orgs/me/settings", token, {
    confidence_threshold: confidenceThreshold,
  });
}

export function getLlmUsage(token: string): Promise<LlmUsageOut[]> {
  return getJson<LlmUsageOut[]>("/orgs/me/llm-usage", token);
}

export type MatchType = "automatic" | "manual";

export interface ReconciliationMatchOut {
  id: string;
  bank_transaction_id: string;
  expense_transaction_id: string;
  match_type: MatchType;
  confidence: number;
  actor: string;
  created_at: string;
}

export function listMatches(token: string): Promise<ReconciliationMatchOut[]> {
  return getJson<ReconciliationMatchOut[]>("/reconciliation/matches", token);
}

/** Manually links two Transactions the algorithm missed (issue #6, AC6). */
export function createMatch(
  token: string,
  bankTransactionId: string,
  expenseTransactionId: string,
): Promise<ReconciliationMatchOut> {
  return postJson<ReconciliationMatchOut>(
    "/reconciliation/matches",
    { bank_transaction_id: bankTransactionId, expense_transaction_id: expenseTransactionId },
    token,
  );
}

export function deleteMatch(token: string, matchId: string): Promise<void> {
  return sendJson<void>("DELETE", `/reconciliation/matches/${matchId}`, token);
}

export function listUnmatchedTransactions(
  token: string,
  side: DocumentType,
): Promise<TransactionOut[]> {
  const params = new URLSearchParams({ side });
  return getJson<TransactionOut[]>(
    `/reconciliation/transactions/unmatched?${params.toString()}`,
    token,
  );
}

export function getTransaction(token: string, id: string): Promise<TransactionOut> {
  return getJson<TransactionOut>(`/transactions/${id}`, token);
}

/** A human correction is authoritative: it sets confidence to 1.0. */
export function correctTransactionCategory(
  token: string,
  id: string,
  categoryId: string,
): Promise<TransactionOut> {
  return sendJson<TransactionOut>("PUT", `/transactions/${id}/category`, token, {
    category_id: categoryId,
  });
}
