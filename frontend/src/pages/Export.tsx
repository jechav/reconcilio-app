import { useMutation } from "@tanstack/react-query";
import { FileJson, FileSpreadsheet, Loader2 } from "lucide-react";
import { useState } from "react";
import { Navigate } from "react-router-dom";

import { ApiError, exportTransactions, type ExportFormat } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

const COLUMNS = ["Date", "Description", "Amount", "Category", "Review status", "Match status"];

export function Export() {
  const session = getSession();
  const token = session?.access_token ?? "";

  const [startDate, setStartDate] = useState(defaultStartDate);
  const [endDate, setEndDate] = useState(defaultEndDate);

  const download = useMutation({
    mutationFn: (format: ExportFormat) => exportTransactions(token, startDate, endDate, format),
    onSuccess: ({ blob, filename }) => triggerDownload(blob, filename),
  });

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  const downloading = download.isPending ? download.variables : null;
  const error = download.error
    ? download.error instanceof ApiError
      ? download.error.message
      : "Failed to export transactions."
    : null;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
          Export
        </h1>
        <p className="max-w-2xl text-sm text-gray-500">
          Export every transaction in a date range for an accountant or tax software, including
          items that still need attention (uncategorized or unmatched).
        </p>
      </header>

      <form
        aria-label="Export transactions"
        onSubmit={(event) => event.preventDefault()}
        className="flex max-w-2xl flex-col gap-6 rounded-xl border bg-white p-6"
      >
        <div className="flex gap-4">
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="export-start-date" className="text-[13px] font-semibold text-gray-700">
              Start date
            </Label>
            <Input
              id="export-start-date"
              type="date"
              value={startDate}
              onChange={(event) => setStartDate(event.target.value)}
              className="h-10"
            />
          </div>
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="export-end-date" className="text-[13px] font-semibold text-gray-700">
              End date
            </Label>
            <Input
              id="export-end-date"
              type="date"
              value={endDate}
              onChange={(event) => setEndDate(event.target.value)}
              className="h-10"
            />
          </div>
        </div>

        <div className="flex flex-col gap-2 rounded-lg bg-gray-50 px-4 py-3">
          <p className="text-[13px] font-semibold text-gray-700">Included columns</p>
          <ul className="flex flex-wrap gap-1.5">
            {COLUMNS.map((column) => (
              <li
                key={column}
                className="rounded-full border bg-white px-2.5 py-0.5 text-xs text-gray-700"
              >
                {column}
              </li>
            ))}
          </ul>
        </div>

        {error && (
          <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
            {error}
          </p>
        )}

        <div className="flex gap-3">
          <Button
            type="button"
            onClick={() => download.mutate("csv")}
            disabled={download.isPending}
            className="h-11 flex-1"
          >
            {downloading === "csv" ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <FileSpreadsheet aria-hidden />
            )}
            {downloading === "csv" ? "Exporting…" : "Download CSV"}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => download.mutate("json")}
            disabled={download.isPending}
            className="h-11 flex-1"
          >
            {downloading === "json" ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <FileJson aria-hidden />
            )}
            {downloading === "json" ? "Exporting…" : "Download JSON"}
          </Button>
        </div>
      </form>
    </div>
  );
}
