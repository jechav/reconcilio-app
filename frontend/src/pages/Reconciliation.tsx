import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, Loader2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, Navigate } from "react-router-dom";

import {
  ApiError,
  createMatch,
  deleteMatch,
  getTransaction,
  listMatches,
  listUnmatchedTransactions,
  type DocumentType,
  type ReconciliationMatchOut,
  type TransactionOut,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { daysBetween, formatAmount, formatDate } from "@/lib/format";
import { getSession } from "@/session";

const BANK: DocumentType = "bank_statement";
const EXPENSE: DocumentType = "invoice_or_receipt";

function messageFor(error: unknown, fallback: string): string | null {
  if (!error) return null;
  return error instanceof ApiError ? error.message : fallback;
}

function useInvalidateReconciliation() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["reconciliation"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
    ]);
}

function SummaryCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "amber" | "rose" | "teal";
}) {
  return (
    <div
      className={cn(
        "flex flex-1 flex-col gap-1 rounded-xl border-l-4 px-5 py-4",
        tone === "amber" && "border-amber-500 bg-amber-50",
        tone === "rose" && "border-rose-500 bg-rose-50",
        tone === "teal" && "border-teal-500 bg-teal-50",
      )}
    >
      <span className="text-[13px] text-gray-700">{label}</span>
      <span className="text-2xl font-bold tabular-nums text-gray-900">{value}</span>
    </div>
  );
}

function EmptyState({ children }: { children: ReactNode }) {
  return <p className="px-6 py-10 text-center text-sm text-gray-500">{children}</p>;
}

function UnmatchedTable({
  transactions,
  loading,
  emptyText,
  onMatch,
}: {
  transactions: TransactionOut[] | undefined;
  loading: boolean;
  emptyText: string;
  onMatch: (transaction: TransactionOut) => void;
}) {
  if (loading) {
    return (
      <div className="flex flex-col gap-3 p-6">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-8 animate-pulse rounded-md bg-gray-100" />
        ))}
      </div>
    );
  }
  if (!transactions || transactions.length === 0) return <EmptyState>{emptyText}</EmptyState>;
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-y bg-gray-50 text-left text-xs font-semibold tracking-wide text-gray-500 uppercase">
          <th className="px-6 py-2.5 font-semibold">Date</th>
          <th className="px-3 py-2.5 text-right font-semibold">Amount</th>
          <th className="px-6 py-2.5 font-semibold">Vendor / description</th>
          <th className="px-6 py-2.5 text-right font-semibold">
            <span className="sr-only">Action</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {transactions.map((transaction) => (
          <tr key={transaction.id} className="border-b last:border-b-0">
            <td className="px-6 py-3.5 whitespace-nowrap text-gray-500">
              {formatDate(transaction.txn_date)}
            </td>
            <td className="px-3 py-3.5 text-right font-semibold tabular-nums text-gray-900">
              {formatAmount(transaction.amount)}
            </td>
            <td className="px-6 py-3.5">
              <Link
                to={`/transactions/${transaction.id}`}
                className="text-gray-900 hover:text-navy-600 hover:underline"
              >
                {transaction.description}
              </Link>
            </td>
            <td className="px-6 py-3.5 text-right">
              <Button
                type="button"
                size="sm"
                variant="outline"
                aria-label={`Match ${transaction.description}`}
                onClick={() => onMatch(transaction)}
              >
                <ArrowLeftRight aria-hidden />
                Match
              </Button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MatchDialog({
  transaction,
  side,
  token,
  onClose,
}: {
  transaction: TransactionOut;
  side: DocumentType;
  token: string;
  onClose: () => void;
}) {
  const invalidate = useInvalidateReconciliation();
  const otherSide = side === BANK ? EXPENSE : BANK;
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const candidates = useQuery({
    queryKey: ["reconciliation", "unmatched", otherSide],
    queryFn: () => listUnmatchedTransactions(token, otherSide),
  });

  const ranked = [...(candidates.data ?? [])].sort((a, b) => {
    const amountDiff = (t: TransactionOut) =>
      Math.abs(Math.abs(Number(t.amount)) - Math.abs(Number(transaction.amount)));
    return (
      amountDiff(a) - amountDiff(b) ||
      daysBetween(a.txn_date, transaction.txn_date) - daysBetween(b.txn_date, transaction.txn_date)
    );
  });

  const confirm = useMutation({
    mutationFn: (candidateId: string) =>
      side === BANK
        ? createMatch(token, transaction.id, candidateId)
        : createMatch(token, candidateId, transaction.id),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  const apiError = messageFor(confirm.error, "Failed to create match.");
  const otherLabel = otherSide === BANK ? "bank transactions" : "invoices and receipts";

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Match transaction</DialogTitle>
          <DialogDescription>
            Link this {side === BANK ? "bank transaction" : "invoice or receipt"} to one{" "}
            {otherSide === BANK ? "bank transaction" : "invoice or receipt"}. Closest amounts and
            dates are listed first.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-1 rounded-lg bg-navy-50 px-4 py-3">
          <span className="text-xs font-semibold tracking-wide text-navy-600 uppercase">
            {side === BANK ? "Bank transaction" : "Invoice or receipt"}
          </span>
          <div className="flex items-baseline justify-between gap-4">
            <span className="truncate text-sm font-semibold text-gray-900">
              {transaction.description}
            </span>
            <span className="text-sm font-semibold tabular-nums text-gray-900">
              {formatAmount(transaction.amount)}
            </span>
          </div>
          <span className="text-xs text-gray-700">{formatDate(transaction.txn_date)}</span>
        </div>

        <div className="flex flex-col gap-2">
          <span id="match-candidates-label" className="text-[13px] font-semibold text-gray-700">
            Unmatched {otherLabel}
          </span>
          {candidates.isPending && <p className="text-sm text-gray-500">Loading…</p>}
          {candidates.data && ranked.length === 0 && (
            <p className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-gray-500">
              No unmatched {otherLabel} to link to.
            </p>
          )}
          <div
            role="radiogroup"
            aria-labelledby="match-candidates-label"
            className="flex max-h-64 flex-col gap-2 overflow-y-auto"
          >
            {ranked.map((candidate) => {
              const selected = selectedId === candidate.id;
              const sameAmount =
                Math.abs(Number(candidate.amount)) === Math.abs(Number(transaction.amount));
              return (
                <button
                  key={candidate.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => setSelectedId(candidate.id)}
                  className={cn(
                    "flex items-center gap-3 rounded-lg border px-4 py-3 text-left transition-colors hover:bg-gray-50",
                    selected && "border-navy-600 bg-navy-50 hover:bg-navy-50",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "flex size-4 shrink-0 items-center justify-center rounded-full border-2",
                      selected ? "border-navy-600" : "border-gray-300",
                    )}
                  >
                    {selected && <span className="size-2 rounded-full bg-navy-600" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-gray-900">
                      {candidate.description}
                    </span>
                    <span className="block text-xs text-gray-500">
                      {formatDate(candidate.txn_date)}
                    </span>
                  </span>
                  {sameAmount && (
                    <Badge variant="secondary" className="rounded-full bg-teal-50 text-teal-700">
                      Same amount
                    </Badge>
                  )}
                  <span className="text-sm font-semibold tabular-nums text-gray-900">
                    {formatAmount(candidate.amount)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {apiError && (
          <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
            {apiError}
          </p>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={selectedId === null || confirm.isPending}
            onClick={() => selectedId && confirm.mutate(selectedId)}
          >
            {confirm.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Confirm match
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function UnmatchDialog({
  match,
  token,
  onClose,
}: {
  match: ReconciliationMatchOut;
  token: string;
  onClose: () => void;
}) {
  const invalidate = useInvalidateReconciliation();
  const remove = useMutation({
    mutationFn: () => deleteMatch(token, match.id),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  const apiError = messageFor(remove.error, "Failed to unmatch.");

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Unmatch transactions</DialogTitle>
          <DialogDescription>
            Both transactions go back to the unmatched lists so they can be matched again.
          </DialogDescription>
        </DialogHeader>
        {apiError && (
          <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
            {apiError}
          </p>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            {remove.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Unmatch
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MatchesTable({
  matches,
  loading,
  token,
  onUnmatch,
}: {
  matches: ReconciliationMatchOut[] | undefined;
  loading: boolean;
  token: string;
  onUnmatch: (match: ReconciliationMatchOut) => void;
}) {
  const ids = [
    ...new Set((matches ?? []).flatMap((m) => [m.bank_transaction_id, m.expense_transaction_id])),
  ];
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["reconciliation", "transaction", id],
      queryFn: () => getTransaction(token, id),
    })),
  });
  const byId = new Map(ids.map((id, i) => [id, results[i]?.data]));

  if (loading) {
    return (
      <div className="flex flex-col gap-3 p-6">
        {[0, 1].map((i) => (
          <div key={i} className="h-10 animate-pulse rounded-md bg-gray-100" />
        ))}
      </div>
    );
  }
  if (!matches || matches.length === 0) return <EmptyState>No matches yet.</EmptyState>;

  const cell = (transaction: TransactionOut | undefined) =>
    transaction ? (
      <Link to={`/transactions/${transaction.id}`} className="block hover:underline">
        <span className="block truncate text-sm font-semibold text-gray-900">
          {transaction.description}
        </span>
        <span className="block text-xs text-gray-500">
          {formatDate(transaction.txn_date)} · {formatAmount(transaction.amount)}
        </span>
      </Link>
    ) : (
      <span className="block h-8 w-40 animate-pulse rounded-md bg-gray-100" />
    );

  return (
    <ul>
      {matches.map((match) => (
        <li key={match.id} className="flex items-center gap-4 border-b px-6 py-3.5 last:border-b-0">
          <div className="min-w-0 flex-1">{cell(byId.get(match.bank_transaction_id))}</div>
          <ArrowLeftRight className="size-4 shrink-0 text-gray-500" aria-hidden />
          <div className="min-w-0 flex-1">{cell(byId.get(match.expense_transaction_id))}</div>
          <Badge
            variant="secondary"
            className={cn(
              "w-24 justify-center rounded-full",
              match.match_type === "manual" && "bg-navy-50 text-navy-600",
            )}
          >
            {match.match_type === "manual"
              ? "Manual"
              : `Auto ${Math.round(match.confidence * 100)}%`}
          </Badge>
          <Button type="button" size="sm" variant="outline" onClick={() => onUnmatch(match)}>
            Unmatch
          </Button>
        </li>
      ))}
    </ul>
  );
}

export function Reconciliation() {
  const session = getSession();
  const token = session?.access_token ?? "";
  const enabled = session !== null;

  const [matching, setMatching] = useState<{
    transaction: TransactionOut;
    side: DocumentType;
  } | null>(null);
  const [unmatching, setUnmatching] = useState<ReconciliationMatchOut | null>(null);

  const bank = useQuery({
    queryKey: ["reconciliation", "unmatched", BANK],
    queryFn: () => listUnmatchedTransactions(token, BANK),
    enabled,
  });
  const expenses = useQuery({
    queryKey: ["reconciliation", "unmatched", EXPENSE],
    queryFn: () => listUnmatchedTransactions(token, EXPENSE),
    enabled,
  });
  const matches = useQuery({
    queryKey: ["reconciliation", "matches"],
    queryFn: () => listMatches(token),
    enabled,
  });

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  const loadError =
    messageFor(bank.error, "Failed to load unmatched transactions.") ??
    messageFor(expenses.error, "Failed to load unmatched transactions.") ??
    messageFor(matches.error, "Failed to load matches.");

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
          Reconciliation
        </h1>
        <p className="text-sm text-gray-500">
          Unmatched items on either side are the tax-risk signal. Link them, or find the missing
          paperwork.
        </p>
      </header>

      {loadError && (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
          {loadError}
        </p>
      )}

      <div role="group" aria-label="Reconciliation summary" className="flex gap-4">
        <SummaryCard
          label="Unmatched bank transactions"
          value={bank.data?.length ?? 0}
          tone="amber"
        />
        <SummaryCard
          label="Unmatched invoices & receipts"
          value={expenses.data?.length ?? 0}
          tone="rose"
        />
        <SummaryCard label="Matched pairs" value={matches.data?.length ?? 0} tone="teal" />
      </div>

      <Tabs defaultValue="bank" className="gap-0 overflow-hidden rounded-xl border bg-white">
        <TabsList className="h-auto w-full justify-start gap-1 rounded-none border-b bg-white p-2">
          <TabsTrigger value="bank" className="flex-none px-4 py-2">
            Unmatched bank transactions ({bank.data?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="expenses" className="flex-none px-4 py-2">
            Unmatched invoices &amp; receipts ({expenses.data?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="matches" className="flex-none px-4 py-2">
            Matches ({matches.data?.length ?? 0})
          </TabsTrigger>
        </TabsList>
        <TabsContent value="bank">
          <UnmatchedTable
            transactions={bank.data}
            loading={bank.isPending}
            emptyText="Every bank transaction is matched."
            onMatch={(transaction) => setMatching({ transaction, side: BANK })}
          />
        </TabsContent>
        <TabsContent value="expenses">
          <UnmatchedTable
            transactions={expenses.data}
            loading={expenses.isPending}
            emptyText="Every invoice and receipt is matched."
            onMatch={(transaction) => setMatching({ transaction, side: EXPENSE })}
          />
        </TabsContent>
        <TabsContent value="matches">
          <MatchesTable
            matches={matches.data}
            loading={matches.isPending}
            token={token}
            onUnmatch={setUnmatching}
          />
        </TabsContent>
      </Tabs>

      {matching && (
        <MatchDialog
          key={matching.transaction.id}
          transaction={matching.transaction}
          side={matching.side}
          token={token}
          onClose={() => setMatching(null)}
        />
      )}
      {unmatching && (
        <UnmatchDialog match={unmatching} token={token} onClose={() => setUnmatching(null)} />
      )}
    </div>
  );
}
