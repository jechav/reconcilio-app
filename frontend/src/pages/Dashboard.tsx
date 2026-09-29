import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate } from "react-router-dom";

import {
  ApiError,
  getDashboardFlags,
  getDashboardSummary,
  getDashboardSummaryTransactions,
  getDocument,
  type CategorySummaryOut,
  type TransactionOut,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatAmount, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { getSession } from "@/session";

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

function isZero(value: string): boolean {
  return Number(value) === 0;
}

function errorMessage(error: unknown, fallback: string): string | null {
  if (!error) return null;
  return error instanceof ApiError ? error.message : fallback;
}

function KpiCard({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "primary";
}) {
  const primary = tone === "primary";
  return (
    <div
      className={cn(
        "flex flex-1 flex-col gap-2 rounded-xl border p-5",
        primary ? "border-navy-600 bg-navy-600" : "bg-white",
      )}
    >
      <span className={cn("text-[13px]", primary ? "text-[#c9d8e6]" : "text-gray-500")}>
        {label}
      </span>
      <span
        className={cn(
          "text-3xl font-bold tracking-tight tabular-nums",
          primary ? "text-white" : "text-gray-900",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-gray-100", className)} />;
}

function FlagRow({
  transaction,
  onViewDocument,
}: {
  transaction: TransactionOut;
  onViewDocument: (documentId: string) => void;
}) {
  return (
    <li className="flex items-center gap-3 border-t border-black/5 py-2.5 first:border-t-0">
      <div className="min-w-0 flex-1">
        <Link
          to={`/transactions/${transaction.id}`}
          className="block truncate text-sm font-semibold text-gray-900 hover:underline"
        >
          {transaction.description}
        </Link>
        <p className="text-xs text-gray-700">{formatDate(transaction.txn_date)}</p>
      </div>
      <span className="text-sm tabular-nums text-gray-900">{formatAmount(transaction.amount)}</span>
      <button
        type="button"
        onClick={() => onViewDocument(transaction.document_id)}
        className="text-sm font-semibold text-navy-600 hover:underline"
      >
        View document
      </button>
    </li>
  );
}

function FlagGroup({
  title,
  emptyText,
  transactions,
  tone,
  onViewDocument,
}: {
  title: string;
  emptyText: string;
  transactions: TransactionOut[];
  tone: "amber" | "rose";
  onViewDocument: (documentId: string) => void;
}) {
  const empty = transactions.length === 0;
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-lg border-l-4 px-4 py-3.5",
        empty
          ? "border-teal-500 bg-teal-50"
          : tone === "amber"
            ? "border-amber-500 bg-amber-50"
            : "border-rose-500 bg-rose-50",
      )}
    >
      <h3 className="text-sm font-semibold text-gray-900">
        {title}
        {!empty && ` (${transactions.length})`}
      </h3>
      {empty ? (
        <p className="text-[13px] text-gray-700">{emptyText}</p>
      ) : (
        <ul>
          {transactions.map((transaction) => (
            <FlagRow
              key={transaction.id}
              transaction={transaction}
              onViewDocument={onViewDocument}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function Panel({
  label,
  title,
  action,
  children,
}: {
  label: string;
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-label={label} className="rounded-xl border bg-white">
      <div className="flex items-center justify-between px-6 pt-5 pb-3">
        <h2 className="text-base font-bold text-gray-900">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Dashboard() {
  const session = getSession();
  const token = session?.access_token ?? "";
  const enabled = session !== null;

  const [startDate, setStartDate] = useState(defaultStartDate);
  const [endDate, setEndDate] = useState(defaultEndDate);
  const [range, setRange] = useState(() => ({ start: startDate, end: endDate }));
  const [drillDownCategory, setDrillDownCategory] = useState<CategorySummaryOut | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);

  const summary = useQuery({
    queryKey: ["dashboard", "summary", range],
    queryFn: () => getDashboardSummary(token, range.start, range.end),
    enabled,
  });
  const flags = useQuery({
    queryKey: ["dashboard", "flags", range],
    queryFn: () => getDashboardFlags(token, range.start, range.end),
    enabled,
  });
  const drillDown = useQuery({
    queryKey: ["dashboard", "drill-down", range, drillDownCategory?.category_id ?? null],
    queryFn: () =>
      getDashboardSummaryTransactions(
        token,
        range.start,
        range.end,
        drillDownCategory?.category_id ?? null,
      ),
    enabled: enabled && drillDownCategory !== null,
  });
  const documentPreview = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => getDocument(token, documentId!),
    enabled: enabled && documentId !== null,
  });

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  function handleFilterSubmit(event: FormEvent) {
    event.preventDefault();
    setRange({ start: startDate, end: endDate });
    setDrillDownCategory(null);
    setDocumentId(null);
  }

  const error =
    errorMessage(summary.error, "Failed to load dashboard.") ??
    errorMessage(flags.error, "Failed to load dashboard.") ??
    errorMessage(drillDown.error, "Failed to load transactions.") ??
    errorMessage(documentPreview.error, "Failed to load document.");
  const loading = summary.isFetching || flags.isFetching;
  const data = summary.data;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
            Dashboard
          </h1>
          <p className="text-sm text-gray-500">
            Income, expenses and missing paperwork for the selected period
          </p>
        </div>
        <form
          aria-label="Date range filter"
          onSubmit={handleFilterSubmit}
          className="flex items-end gap-3"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="start-date" className="text-[13px] font-semibold text-gray-700">
              Start date
            </Label>
            <Input
              id="start-date"
              type="date"
              value={startDate}
              onChange={(event) => setStartDate(event.target.value)}
              className="h-10 w-40 bg-white"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="end-date" className="text-[13px] font-semibold text-gray-700">
              End date
            </Label>
            <Input
              id="end-date"
              type="date"
              value={endDate}
              onChange={(event) => setEndDate(event.target.value)}
              className="h-10 w-40 bg-white"
            />
          </div>
          <Button type="submit" disabled={loading} className="h-10">
            {loading ? "Loading…" : "Apply"}
          </Button>
        </form>
      </header>

      {error && (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
          {error}
        </p>
      )}

      <div aria-label="Totals" role="group" className="flex gap-4">
        {data ? (
          <>
            <KpiCard label="Income" value={formatAmount(data.income_total)} />
            <KpiCard label="Expenses" value={formatAmount(data.expenses_total)} />
            <KpiCard label="Net" value={formatAmount(data.net_total)} tone="primary" />
          </>
        ) : (
          [0, 1, 2].map((i) => <Skeleton key={i} className="h-[102px] flex-1 rounded-xl" />)
        )}
      </div>

      <div className="flex flex-col items-start gap-6 xl:flex-row">
        <div className="w-full xl:flex-[1.5]">
          <Panel label="Income and expense summary" title="Summary by category">
            {data ? (
              data.categories.length === 0 ? (
                <p className="px-6 pb-6 text-sm text-gray-500">
                  No transactions in this date range.
                </p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-y bg-gray-50 text-left text-xs font-semibold tracking-wide text-gray-500 uppercase">
                      <th className="px-6 py-2.5 font-semibold">Category</th>
                      <th className="px-3 py-2.5 text-right font-semibold">Income</th>
                      <th className="px-3 py-2.5 text-right font-semibold">Expenses</th>
                      <th className="px-6 py-2.5 text-right font-semibold">Txns</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.categories.map((category) => {
                      const selected = drillDownCategory?.category_id === category.category_id;
                      return (
                        <tr
                          key={category.category_id ?? "uncategorized"}
                          className={cn(
                            "border-b border-gray-100 last:border-b-0",
                            selected && "bg-navy-50",
                          )}
                        >
                          <td className="px-6 py-3.5">
                            <button
                              type="button"
                              aria-pressed={selected}
                              onClick={() => {
                                setDrillDownCategory(category);
                                setDocumentId(null);
                              }}
                              className="font-semibold text-navy-600 hover:underline"
                            >
                              {category.category_name}
                            </button>
                          </td>
                          <td className="px-3 py-3.5 text-right tabular-nums text-gray-900">
                            {isZero(category.income) ? "–" : formatAmount(category.income)}
                          </td>
                          <td className="px-3 py-3.5 text-right tabular-nums text-gray-900">
                            {isZero(category.expenses) ? "–" : formatAmount(category.expenses)}
                          </td>
                          <td className="px-6 py-3.5 text-right tabular-nums text-gray-700">
                            {category.transaction_count}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )
            ) : (
              <div className="flex flex-col gap-3 px-6 pb-6">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-6" />
                ))}
              </div>
            )}
          </Panel>
        </div>

        <section
          aria-label="Missing documentation flags"
          className="flex w-full flex-1 flex-col gap-3"
        >
          <h2 className="text-base font-bold text-gray-900">Missing documentation</h2>
          {flags.data ? (
            <>
              <FlagGroup
                title="Unmatched bank transactions"
                emptyText="None — every bank transaction is matched."
                transactions={flags.data.unmatched_bank_transactions}
                tone="amber"
                onViewDocument={setDocumentId}
              />
              <FlagGroup
                title="Unmatched expense-source transactions"
                emptyText="None — every invoice/receipt transaction is matched."
                transactions={flags.data.unmatched_expense_transactions}
                tone="rose"
                onViewDocument={setDocumentId}
              />
              <Link
                to="/reconciliation"
                className="pt-1 text-sm font-semibold text-navy-600 hover:underline"
              >
                Review in Reconciliation →
              </Link>
            </>
          ) : (
            <>
              <Skeleton className="h-24 rounded-lg" />
              <Skeleton className="h-24 rounded-lg" />
            </>
          )}
        </section>
      </div>

      {drillDownCategory && (
        <Panel
          label="Category drill-down"
          title={`Transactions for ${drillDownCategory.category_name}`}
          action={
            <button
              type="button"
              onClick={() => setDrillDownCategory(null)}
              aria-label="Close drill-down"
              className="rounded-md p-1 text-gray-500 hover:bg-gray-100"
            >
              <X className="size-4" aria-hidden />
            </button>
          }
        >
          {drillDown.data === undefined && (
            <p className="px-6 pb-5 text-sm text-gray-500">Loading transactions…</p>
          )}
          {drillDown.data && drillDown.data.length === 0 && (
            <p className="px-6 pb-5 text-sm text-gray-500">No transactions found.</p>
          )}
          {drillDown.data && drillDown.data.length > 0 && (
            <ul>
              {drillDown.data.map((transaction) => (
                <li
                  key={transaction.id}
                  className="flex items-center gap-4 border-t border-gray-100 px-6 py-3.5"
                >
                  <span className="w-28 text-sm text-gray-500">
                    {formatDate(transaction.txn_date)}
                  </span>
                  <Link
                    to={`/transactions/${transaction.id}`}
                    className="min-w-0 flex-1 truncate text-sm text-gray-900 hover:underline"
                  >
                    {transaction.description}
                  </Link>
                  <span className="w-28 text-right text-sm font-semibold tabular-nums text-gray-900">
                    {formatAmount(transaction.amount)}
                  </span>
                  <button
                    type="button"
                    onClick={() => setDocumentId(transaction.document_id)}
                    className="w-32 text-right text-sm font-semibold text-navy-600 hover:underline"
                  >
                    View document
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      {documentPreview.data && (
        <section
          aria-label="Source document"
          data-testid="document-preview"
          className="flex items-center justify-between rounded-xl border bg-white px-6 py-4"
        >
          <div className="flex flex-col gap-0.5">
            <h2 className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
              Source document
            </h2>
            <p className="text-sm font-semibold text-gray-900">{documentPreview.data.filename}</p>
          </div>
          <Badge variant="secondary" className="rounded-full">
            {documentPreview.data.status.replace("_", " ")}
          </Badge>
          <button
            type="button"
            onClick={() => setDocumentId(null)}
            aria-label="Close document"
            className="rounded-md p-1 text-gray-500 hover:bg-gray-100"
          >
            <X className="size-4" aria-hidden />
          </button>
        </section>
      )}
    </div>
  );
}
