import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Navigate } from "react-router-dom";

import { ApiError, getAuditLog, type AuditLogEntryOut } from "../api/client";
import { getSession } from "../session";

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
    return <p>No before/after values recorded.</p>;
  }
  return (
    <table aria-label={`Before/after diff for ${entry.action}`}>
      <thead>
        <tr>
          <th>Field</th>
          <th>Before</th>
          <th>After</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((field) => {
          const before = entry.before ? entry.before[field] : undefined;
          const after = entry.after ? entry.after[field] : undefined;
          const changed = JSON.stringify(before) !== JSON.stringify(after);
          return (
            <tr key={field} data-changed={changed}>
              <td>{field}</td>
              <td>{formatValue(before)}</td>
              <td>{formatValue(after)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function AuditLog() {
  const session = getSession();
  const [entityType, setEntityType] = useState("");
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [entries, setEntries] = useState<AuditLogEntryOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (!session) {
    return <Navigate to="/login" replace />;
  }
  const token = session.access_token;

  async function loadAuditLog() {
    setError(null);
    setLoading(true);
    try {
      const data = await getAuditLog(token, {
        entityType: entityType || undefined,
        actor: actor || undefined,
        action: action || undefined,
        startDate: startDate || undefined,
        endDate: endDate || undefined,
      });
      setEntries(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load audit log.");
    } finally {
      setLoading(false);
    }
  }

  const canView = session.role === "owner" || session.role === "admin";

  useEffect(() => {
    if (!canView) {
      return;
    }
    void loadAuditLog();
    // Intentionally load once on mount with no filters; subsequent loads
    // happen via the filter form's onSubmit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canView]);

  async function handleFilterSubmit(event: FormEvent) {
    event.preventDefault();
    await loadAuditLog();
  }

  if (!canView) {
    return (
      <div>
        <h1>Audit log</h1>
        <p role="alert">Only an owner or admin can view the audit log.</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Audit log</h1>
      <p>Every extraction, categorization, and reconciliation change made to your Organization's data.</p>

      <form aria-label="Filter audit log" onSubmit={handleFilterSubmit}>
        <label htmlFor="audit-entity-type">Entity type</label>
        <input
          id="audit-entity-type"
          value={entityType}
          onChange={(event) => setEntityType(event.target.value)}
          placeholder="transaction, document, category, reconciliation_match"
        />

        <label htmlFor="audit-actor">Actor</label>
        <input
          id="audit-actor"
          value={actor}
          onChange={(event) => setActor(event.target.value)}
          placeholder="system or a user id"
        />

        <label htmlFor="audit-action">Action</label>
        <input
          id="audit-action"
          value={action}
          onChange={(event) => setAction(event.target.value)}
          placeholder="e.g. transaction.category_corrected"
        />

        <label htmlFor="audit-start-date">Start date</label>
        <input
          id="audit-start-date"
          type="date"
          value={startDate}
          onChange={(event) => setStartDate(event.target.value)}
        />

        <label htmlFor="audit-end-date">End date</label>
        <input
          id="audit-end-date"
          type="date"
          value={endDate}
          onChange={(event) => setEndDate(event.target.value)}
        />

        <button type="submit" disabled={loading}>
          {loading ? "Loading…" : "Apply filters"}
        </button>
      </form>

      {error && <p role="alert">{error}</p>}

      <section aria-label="Audit log entries">
        {entries.length === 0 && !loading && <p>No audit log entries match these filters.</p>}
        <ul>
          {entries.map((entry) => (
            <li key={entry.id}>
              <button
                type="button"
                onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}
                aria-expanded={expandedId === entry.id}
              >
                <span>{new Date(entry.created_at).toLocaleString()}</span>{" "}
                <span>{entry.action}</span>{" "}
                <span>{entry.entity_type}</span>{" "}
                <span>{describeActor(entry)}</span>
              </button>
              {expandedId === entry.id && <EntryDiff entry={entry} />}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
