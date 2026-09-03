import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Navigate } from "react-router-dom";

import {
  ApiError,
  getDashboardFlags,
  getDashboardSummary,
  getDashboardSummaryTransactions,
  getDocument,
  type CategorySummaryOut,
  type DashboardFlagsOut,
  type DashboardSummaryOut,
  type DocumentOut,
  type TransactionOut,
} from "../api/client";
import { getSession } from "../session";

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultStartDate(): string {
  const d = new Date();
  d.setDate(d.getDate() - 30);
  return isoDate(d);
}

function defaultEndDate(): string {
  return isoDate(new Date());
}

export function Dashboard() {
  const session = getSession();
  const [startDate, setStartDate] = useState(defaultStartDate());
  const [endDate, setEndDate] = useState(defaultEndDate());
  const [summary, setSummary] = useState<DashboardSummaryOut | null>(null);
  const [flags, setFlags] = useState<DashboardFlagsOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [drillDownCategory, setDrillDownCategory] = useState<CategorySummaryOut | null>(null);
  const [drillDownTransactions, setDrillDownTransactions] = useState<TransactionOut[] | null>(null);
  const [documentPreview, setDocumentPreview] = useState<DocumentOut | null>(null);

  if (!session) {
    return <Navigate to="/login" replace />;
  }
  const token = session.access_token;

  async function loadDashboard() {
    setError(null);
    setLoading(true);
    setDrillDownCategory(null);
    setDrillDownTransactions(null);
    setDocumentPreview(null);
    try {
      const [summaryData, flagsData] = await Promise.all([
        getDashboardSummary(token, startDate, endDate),
        getDashboardFlags(token, startDate, endDate),
      ]);
      setSummary(summaryData);
      setFlags(flagsData);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load dashboard.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDashboard();
    // Intentionally load once on mount with the default date range;
    // subsequent loads happen via the filter form's onSubmit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleFilterSubmit(event: FormEvent) {
    event.preventDefault();
    await loadDashboard();
  }

  async function handleCategoryClick(category: CategorySummaryOut) {
    setDrillDownCategory(category);
    setDrillDownTransactions(null);
    setDocumentPreview(null);
    try {
      const transactions = await getDashboardSummaryTransactions(
        token,
        startDate,
        endDate,
        category.category_id,
      );
      setDrillDownTransactions(transactions);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load transactions.");
    }
  }

  async function handleViewDocument(documentId: string) {
    setDocumentPreview(null);
    try {
      const document = await getDocument(token, documentId);
      setDocumentPreview(document);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load document.");
    }
  }

  return (
    <div>
      <h1>Dashboard</h1>

      <form onSubmit={handleFilterSubmit} aria-label="Date range filter">
        <label htmlFor="start-date">Start date</label>
        <input
          id="start-date"
          type="date"
          value={startDate}
          onChange={(event) => setStartDate(event.target.value)}
        />
        <label htmlFor="end-date">End date</label>
        <input id="end-date" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Apply"}
        </button>
      </form>

      {error && <p role="alert">{error}</p>}

      {summary && (
        <section aria-label="Income and expense summary">
          <h2>Summary by category</h2>
          <p>
            Income: {summary.income_total} · Expenses: {summary.expenses_total} · Net: {summary.net_total}
          </p>
          <table>
            <thead>
              <tr>
                <th>Category</th>
                <th>Income</th>
                <th>Expenses</th>
                <th>Transactions</th>
              </tr>
            </thead>
            <tbody>
              {summary.categories.map((category) => (
                <tr key={category.category_id ?? "uncategorized"}>
                  <td>
                    <button type="button" onClick={() => handleCategoryClick(category)}>
                      {category.category_name}
                    </button>
                  </td>
                  <td>{category.income}</td>
                  <td>{category.expenses}</td>
                  <td>{category.transaction_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {summary.categories.length === 0 && <p>No transactions in this date range.</p>}
        </section>
      )}

      {drillDownCategory && (
        <section aria-label="Category drill-down">
          <h3>Transactions for {drillDownCategory.category_name}</h3>
          {drillDownTransactions === null && <p>Loading transactions…</p>}
          {drillDownTransactions && drillDownTransactions.length === 0 && (
            <p>No transactions found.</p>
          )}
          {drillDownTransactions && drillDownTransactions.length > 0 && (
            <ul>
              {drillDownTransactions.map((transaction) => (
                <li key={transaction.id}>
                  {transaction.txn_date} — {transaction.description} — {transaction.amount}{" "}
                  <button type="button" onClick={() => handleViewDocument(transaction.document_id)}>
                    View document
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {flags && (
        <section aria-label="Missing documentation flags">
          <h2>Missing documentation</h2>

          <h3>Unmatched bank transactions</h3>
          {flags.unmatched_bank_transactions.length === 0 && <p>None — every bank transaction is matched.</p>}
          <ul>
            {flags.unmatched_bank_transactions.map((transaction) => (
              <li key={transaction.id}>
                {transaction.txn_date} — {transaction.description} — {transaction.amount}{" "}
                <button type="button" onClick={() => handleViewDocument(transaction.document_id)}>
                  View document
                </button>
              </li>
            ))}
          </ul>

          <h3>Unmatched expense-source transactions</h3>
          {flags.unmatched_expense_transactions.length === 0 && (
            <p>None — every invoice/receipt transaction is matched.</p>
          )}
          <ul>
            {flags.unmatched_expense_transactions.map((transaction) => (
              <li key={transaction.id}>
                {transaction.txn_date} — {transaction.description} — {transaction.amount}{" "}
                <button type="button" onClick={() => handleViewDocument(transaction.document_id)}>
                  View document
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {documentPreview && (
        <section aria-label="Source document" data-testid="document-preview">
          <h3>Source document</h3>
          <p>
            {documentPreview.filename} ({documentPreview.status})
          </p>
        </section>
      )}
    </div>
  );
}
