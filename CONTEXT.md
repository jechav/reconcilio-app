# Reconcilio

AI-assisted document processing for income tax prep: extracts, categorizes, and reconciles invoices, receipts, and bank statements for small business owners / self-employed tenants.

## Language

**Organization**:
The tenant. A single flat unit of data isolation — every `User`, `Document`, `Category`, and `Transaction` belongs to exactly one Organization. There is no separate "Workspace" concept nesting inside it.
_Avoid_: Workspace, Tenant (use Organization), Account

**Document**:
A source file uploaded by a user — an invoice, receipt, or bank statement (PDF/image/CSV/OFX) — stored in MinIO with pipeline status. One Document produces one or more Transactions.
_Avoid_: File, Upload

**Transaction**:
A single normalized line item — one invoice/receipt or one bank-statement line — linked to the Document it came from, a Category, a confidence score, and a review status. A Document produces one Transaction (invoice/receipt) or many (a multi-line bank statement).
_Avoid_: Record, Entry, Line item

**Category**:
A flat, tenant-defined label a Transaction is assigned to. Exactly one Category per Transaction. Categories carry no built-in tax logic or jurisdiction rules — they are a plain user-defined bucket.
_Avoid_: Tax code, Deduction type, Tag (Category is singular per Transaction, not a tag)

**ReconciliationMatch**:
A one-to-one link between an invoice/receipt Transaction and a bank-statement Transaction, with a match confidence. Reconciliation runs incrementally whenever a new Document finishes extraction, matching within a rolling date window — it is not tied to a fixed calendar period. A Transaction that doesn't cleanly match one-to-one is left unmatched and flagged for review rather than split-matched.
_Avoid_: Period, Reconciliation period (reconciliation has no fixed period boundary)

**ExtractionResult**:
The per-Document raw output of the extraction pipeline — per-field values, confidence scores, and which method produced each field (`ocr`, `llm`, or `structured_parse` for CSV/OFX, which is always confidence 1.0). Every Document, regardless of ingestion path, produces at least one ExtractionResult — this keeps "which field came from where" uniform for the audit trail.
_Avoid_: OCR result (extraction may include LLM-refined or structured-parsed fields, not just OCR)

**Period**:
An ad-hoc, user-selected date range used only as a report filter on the dashboard and export. It is not a stored entity and does not trigger any backend job — reconciliation has no period concept (see ReconciliationMatch).
_Avoid_: Reconciliation period, Tax period

**OrgMembership**:
A User's relationship to one Organization, carrying a role: **owner** (billing, settings, invites), **admin** (everything except billing), or **member** (upload, categorize, view). Role governs what a member can *do*, not what they can *see* — every member has uniform visibility into all of the Organization's data.
_Avoid_: Permission, Access level

**Expense-source Transaction**:
The umbrella domain type for a Transaction extracted from an invoice or receipt Document — both are treated identically by categorization and reconciliation, with no separate paid/unpaid state. An invoice that never matches a bank Transaction surfaces as unmatched, which is itself the tax-risk signal (looks unpaid, or paid through an untracked channel).
_Avoid_: Invoice (as distinct from Receipt) — the two are not modeled as different states or types in v1

**Manual match**:
A ReconciliationMatch created or removed directly by a user rather than by the matching algorithm, recorded with the acting user as the actor (as opposed to `system`) and logged in the audit trail like any other edit. A manual match is not required to satisfy the algorithm's amount/date/vendor criteria.
_Avoid_: Override (use Manual match)
