import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileText, Loader2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  ApiError,
  correctTransactionCategory,
  getDocument,
  getTransaction,
  listCategories,
  listMatches,
  type DocumentStatus,
  type TransactionOut,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { formatAmount, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { getSession } from "@/session";

const DOC_STATUS_LABELS: Record<DocumentStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  needs_review: "Needs review",
  done: "Done",
  failed: "Failed",
};

function messageFor(error: unknown, fallback: string): string | null {
  if (!error) return null;
  return error instanceof ApiError ? error.message : fallback;
}

function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-4 rounded-xl border bg-white p-6">
      <h2 className="text-base font-bold text-gray-900">{title}</h2>
      {children}
    </section>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-semibold tracking-wide text-gray-500 uppercase">{label}</dt>
      <dd className="text-sm text-gray-900">{children}</dd>
    </div>
  );
}

function CategoryEditor({ transaction, token }: { transaction: TransactionOut; token: string }) {
  const queryClient = useQueryClient();
  const categories = useQuery({ queryKey: ["categories"], queryFn: () => listCategories(token) });
  const [selected, setSelected] = useState(transaction.category_id ?? "");

  const save = useMutation({
    mutationFn: (categoryId: string) =>
      correctTransactionCategory(token, transaction.id, categoryId),
    onSuccess: async (updated) => {
      queryClient.setQueryData(["transaction", updated.id], updated);
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Category saved");
    },
  });
  const error = messageFor(save.error, "Failed to save category.");
  const changed = selected !== "" && selected !== (transaction.category_id ?? "");
  const currentName = categories.data?.find((c) => c.id === transaction.category_id)?.name;

  return (
    <Card title="Category">
      <p className="text-sm text-gray-500">
        {transaction.category_id
          ? `Currently ${currentName ?? "…"} (confidence ${percent(transaction.category_confidence)}).`
          : "Currently uncategorized."}{" "}
        Choosing a category teaches future suggestions for your organization.
      </p>
      <div className="flex items-end gap-3">
        <div className="flex flex-1 flex-col gap-1.5">
          <Label htmlFor="transaction-category" className="text-[13px] font-semibold text-gray-700">
            Category
          </Label>
          <select
            id="transaction-category"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            disabled={categories.isPending}
            className={cn(
              "h-10 w-full rounded-md border bg-white px-3 text-sm text-gray-900",
              "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
            )}
          >
            <option value="" disabled>
              Select a category…
            </option>
            {categories.data?.map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
        </div>
        <Button
          type="button"
          className="h-10"
          disabled={!changed || save.isPending}
          onClick={() => save.mutate(selected)}
        >
          {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
          Save category
        </Button>
      </div>
      {error && (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
          {error}
        </p>
      )}
    </Card>
  );
}

export function TransactionDetail() {
  const { id } = useParams<{ id: string }>();
  const session = getSession();
  const token = session?.access_token ?? "";
  const enabled = session !== null && id !== undefined;

  const transaction = useQuery({
    queryKey: ["transaction", id],
    queryFn: () => getTransaction(token, id!),
    enabled,
  });
  const document = useQuery({
    queryKey: ["document", transaction.data?.document_id],
    queryFn: () => getDocument(token, transaction.data!.document_id),
    enabled: enabled && transaction.data !== undefined,
  });
  const matches = useQuery({
    queryKey: ["reconciliation", "matches"],
    queryFn: () => listMatches(token),
    enabled,
  });

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  const data = transaction.data;
  const match = matches.data?.find(
    (m) => m.bank_transaction_id === id || m.expense_transaction_id === id,
  );
  const loadError = messageFor(transaction.error, "Failed to load transaction.");

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <Link
        to="/reconciliation"
        className="flex w-fit items-center gap-1.5 text-sm font-semibold text-navy-600 hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Back to reconciliation
      </Link>

      {loadError && (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
          {loadError}
        </p>
      )}

      {!data && !loadError && <div className="h-24 animate-pulse rounded-xl bg-gray-100" />}

      {data && (
        <>
          <header className="flex items-start justify-between gap-4">
            <div className="flex min-w-0 flex-col gap-1">
              <h1 className="truncate text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
                {data.description}
              </h1>
              <p className="text-sm text-gray-500">{formatDate(data.txn_date)}</p>
            </div>
            <div className="flex flex-col items-end gap-2">
              <span className="text-[28px] leading-[34px] font-bold tracking-tight tabular-nums text-gray-900">
                {formatAmount(data.amount)}
              </span>
              <div className="flex gap-2">
                <Badge
                  variant="secondary"
                  className={cn(
                    "rounded-full",
                    data.status === "needs_review"
                      ? "bg-amber-50 text-amber-700"
                      : "bg-teal-50 text-teal-700",
                  )}
                >
                  {data.status === "needs_review" ? "Needs review" : "Resolved"}
                </Badge>
                <Badge
                  variant="secondary"
                  className={cn(
                    "rounded-full",
                    match ? "bg-teal-50 text-teal-700" : "bg-rose-50 text-rose-700",
                  )}
                >
                  {matches.isPending ? "Checking match…" : match ? "Matched" : "Unmatched"}
                </Badge>
              </div>
            </div>
          </header>

          <Card title="Details">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-5">
              <Fact label="Date">{formatDate(data.txn_date)}</Fact>
              <Fact label="Amount">{formatAmount(data.amount)}</Fact>
              <Fact label="Extraction confidence">{percent(data.confidence)}</Fact>
              <Fact label="Line in document">#{data.line_number}</Fact>
            </dl>
          </Card>

          <CategoryEditor key={data.category_id ?? "none"} transaction={data} token={token} />

          <Card title="Reconciliation">
            {match ? (
              <p className="text-sm text-gray-700">
                Linked to another transaction (
                {match.match_type === "manual" ? "manually" : "automatically"}
                ). Manage it in{" "}
                <Link to="/reconciliation" className="font-semibold text-navy-600 hover:underline">
                  Reconciliation
                </Link>
                .
              </p>
            ) : (
              <p className="text-sm text-gray-700">
                Nothing is linked to this transaction yet.{" "}
                <Link to="/reconciliation" className="font-semibold text-navy-600 hover:underline">
                  Find a match
                </Link>
                .
              </p>
            )}
          </Card>

          <Card title="Source document">
            {document.data ? (
              <div className="flex items-center gap-3 rounded-lg bg-gray-50 px-4 py-3">
                <FileText className="size-5 shrink-0 text-gray-500" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-gray-900">
                    {document.data.filename}
                  </p>
                  <p className="text-xs text-gray-500">
                    {document.data.doc_type === "bank_statement"
                      ? "Bank statement"
                      : "Invoice or receipt"}
                  </p>
                </div>
                <Badge variant="secondary" className="rounded-full">
                  {DOC_STATUS_LABELS[document.data.status]}
                </Badge>
              </div>
            ) : document.error ? (
              <p className="text-sm text-gray-500">The source document could not be loaded.</p>
            ) : (
              <div className="h-14 animate-pulse rounded-lg bg-gray-100" />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
