import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate } from "react-router-dom";

import { ApiError, exportTransactions, type ExportFormat } from "../api/client";
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

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function Export() {
  const session = getSession();
  const [startDate, setStartDate] = useState(defaultStartDate());
  const [endDate, setEndDate] = useState(defaultEndDate());
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<ExportFormat | null>(null);

  if (!session) {
    return <Navigate to="/login" replace />;
  }
  const token = session.access_token;

  async function handleExport(event: FormEvent, format: ExportFormat) {
    event.preventDefault();
    setError(null);
    setDownloading(format);
    try {
      const { blob, filename } = await exportTransactions(token, startDate, endDate, format);
      triggerDownload(blob, filename);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to export transactions.");
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div>
      <h1>Export</h1>
      <p>
        Export every Transaction in a date range for an accountant or tax software, including items
        that still need attention (uncategorized or unmatched), with explicit category, review
        status, and match status columns.
      </p>

      <form aria-label="Export transactions">
        <label htmlFor="export-start-date">Start date</label>
        <input
          id="export-start-date"
          type="date"
          value={startDate}
          onChange={(event) => setStartDate(event.target.value)}
        />
        <label htmlFor="export-end-date">End date</label>
        <input
          id="export-end-date"
          type="date"
          value={endDate}
          onChange={(event) => setEndDate(event.target.value)}
        />
        <button type="submit" onClick={(event) => handleExport(event, "csv")} disabled={downloading !== null}>
          {downloading === "csv" ? "Exporting…" : "Download CSV"}
        </button>
        <button type="submit" onClick={(event) => handleExport(event, "json")} disabled={downloading !== null}>
          {downloading === "json" ? "Exporting…" : "Download JSON"}
        </button>
      </form>

      {error && <p role="alert">{error}</p>}
    </div>
  );
}
