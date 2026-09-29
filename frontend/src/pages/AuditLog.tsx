import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Lock } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";

import { ApiError, getAuditLog, type AuditLogEntryOut, type AuditLogFilters } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { getSession } from "@/session";

function describeActor(entry: AuditLogEntryOut): string {
  if (entry.actor === "system") {
    return "System";
  }
  return entry.actor_email ?? entry.actor;
}

/** Union of an entry's `before`/`after` field names, so a diff row shows up
 * for a field only one side has (e.g. a created or deleted entity). */
function diffFields(entry: AuditLogEntryOut): string[] {
  const names = new Set<string>();
  for (const key of Object.keys(entry.before ?? {})) names.add(key);
  for (const key of Object.keys(entry.after ?? {})) names.add(key);
  return [...names].sort();
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return typeof value === "string" ? value : JSON.stringify(value);
}

function EntryDiff({ entry }: { entry: AuditLogEntryOut }) {
  const fields = diffFields(entry);
  if (fields.length === 0) {
    return <p className="px-6 pb-4 text-sm text-gray-500">No before/after values recorded.</p>;
  }
  return (
    <div className="px-6 pb-4">
      <table
        aria-label={`Before/after diff for ${entry.action}`}
        className="w-full overflow-hidden rounded-lg border text-sm"
      >
        <thead>
          <tr className="bg-gray-50 text-left text-xs font-semibold tracking-wide text-gray-500 uppercase">
            <th className="px-4 py-2 font-semibold">Field</th>
            <th className="px-4 py-2 font-semibold">Before</th>
            <th className="px-4 py-2 font-semibold">After</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => {
            const before = entry.before ? entry.before[field] : undefined;
            const after = entry.after ? entry.after[field] : undefined;
            const changed = JSON.stringify(before) !== JSON.stringify(after);
            return (
              <tr key={field} data-changed={changed} className="border-t">
                <td className="px-4 py-2 font-mono text-xs text-gray-700">{field}</td>
                <td
                  className={cn(
                    "px-4 py-2 font-mono text-xs text-gray-500",
                    changed && "bg-rose-50 text-rose-700",
                  )}
                >
                  {formatValue(before)}
                </td>
                <td
                  className={cn(
                    "px-4 py-2 font-mono text-xs text-gray-500",
                    changed && "bg-teal-50 text-teal-700",
                  )}
                >
                  {formatValue(after)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const timestampFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

function FilterField({
  id,
  label,
  className,
  ...props
}: { id: string; label: string } & React.ComponentProps<typeof Input>) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id} className="text-[13px] font-semibold text-gray-700">
        {label}
      </Label>
      <Input id={id} className="h-10" {...props} />
    </div>
  );
}

const PAGE_TITLE = (
  <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">Audit log</h1>
);

export function AuditLog() {
  const session = getSession();
  const canView = session?.role === "owner" || session?.role === "admin";

  const [entityType, setEntityType] = useState("");
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [filters, setFilters] = useState<AuditLogFilters>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, error, isFetching } = useQuery({
    queryKey: ["audit-log", filters],
    queryFn: () => getAuditLog(session!.access_token, filters),
    enabled: canView,
  });

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  if (!canView) {
    return (
      <div className="flex flex-col gap-6">
        {PAGE_TITLE}
        <div className="flex max-w-xl items-start gap-3 rounded-xl border bg-white p-6">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-500">
            <Lock className="size-4" aria-hidden />
          </span>
          <p role="alert" className="text-sm text-gray-700">
            Only an owner or admin can view the audit log.
          </p>
        </div>
      </div>
    );
  }

  function handleFilterSubmit(event: FormEvent) {
    event.preventDefault();
    setFilters({
      entityType: entityType || undefined,
      actor: actor || undefined,
      action: action || undefined,
      startDate: startDate || undefined,
      endDate: endDate || undefined,
    });
    setExpandedId(null);
  }

  const errorMessage = error
    ? error instanceof ApiError
      ? error.message
      : "Failed to load audit log."
    : null;
  const entries = data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        {PAGE_TITLE}
        <p className="text-sm text-gray-500">
          Every extraction, categorization, and reconciliation change made to your
          Organization&apos;s data.
        </p>
      </header>

      <form
        aria-label="Filter audit log"
        onSubmit={handleFilterSubmit}
        className="flex flex-wrap items-end gap-4 rounded-xl border bg-white p-5"
      >
        <FilterField
          id="audit-entity-type"
          label="Entity type"
          value={entityType}
          onChange={(event) => setEntityType(event.target.value)}
          placeholder="transaction, document…"
          className="w-52"
        />
        <FilterField
          id="audit-actor"
          label="Actor"
          value={actor}
          onChange={(event) => setActor(event.target.value)}
          placeholder="system or a user id"
          className="w-52"
        />
        <FilterField
          id="audit-action"
          label="Action"
          value={action}
          onChange={(event) => setAction(event.target.value)}
          placeholder="transaction.category_corrected"
          className="w-64"
        />
        <FilterField
          id="audit-start-date"
          label="Start date"
          type="date"
          value={startDate}
          onChange={(event) => setStartDate(event.target.value)}
          className="w-40"
        />
        <FilterField
          id="audit-end-date"
          label="End date"
          type="date"
          value={endDate}
          onChange={(event) => setEndDate(event.target.value)}
          className="w-40"
        />
        <Button type="submit" disabled={isFetching} className="h-10">
          {isFetching ? "Loading…" : "Apply filters"}
        </Button>
      </form>

      {errorMessage && (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
          {errorMessage}
        </p>
      )}

      <section aria-label="Audit log entries" className="rounded-xl border bg-white">
        {data === undefined && !errorMessage && (
          <div className="flex flex-col gap-3 p-6">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-6 animate-pulse rounded-md bg-gray-100" />
            ))}
          </div>
        )}
        {data !== undefined && entries.length === 0 && (
          <p className="p-6 text-sm text-gray-500">No audit log entries match these filters.</p>
        )}
        <ul>
          {entries.map((entry) => {
            const expanded = expandedId === entry.id;
            return (
              <li key={entry.id} className="border-b last:border-b-0">
                <button
                  type="button"
                  onClick={() => setExpandedId(expanded ? null : entry.id)}
                  aria-expanded={expanded}
                  className="flex w-full items-center gap-4 px-6 py-3.5 text-left hover:bg-gray-50"
                >
                  <ChevronRight
                    className={cn(
                      "size-4 shrink-0 text-gray-500 transition-transform",
                      expanded && "rotate-90",
                    )}
                    aria-hidden
                  />
                  <span className="w-36 shrink-0 text-sm text-gray-500">
                    {timestampFormat.format(new Date(entry.created_at))}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px] font-semibold text-gray-900">
                    {entry.action}
                  </span>
                  <Badge variant="secondary" className="rounded-full">
                    {entry.entity_type}
                  </Badge>
                  <span className="w-48 shrink-0 truncate text-right text-sm text-gray-700">
                    {describeActor(entry)}
                  </span>
                </button>
                {expanded && <EntryDiff entry={entry} />}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
