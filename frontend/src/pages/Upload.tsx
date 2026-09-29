import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, UploadCloud, X } from "lucide-react";
import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { Navigate } from "react-router-dom";

import {
  ApiError,
  completeDocumentUpload,
  getDocument,
  putFileToUploadUrl,
  requestDocumentUpload,
  type DocumentOut,
  type DocumentStatus,
  type DocumentType,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getSession } from "@/session";

const POLL_INTERVAL_MS = 1500;
// needs_review is terminal for the pipeline: extraction finished, but a
// human has to clear at least one Transaction before the Document is done.
const TERMINAL_STATUSES = new Set<DocumentStatus>(["done", "needs_review", "failed"]);

const DOC_TYPE_LABELS: Record<DocumentType, string> = {
  invoice_or_receipt: "Invoice or receipt",
  bank_statement: "Bank statement",
};

const STATUS_STYLES: Record<DocumentStatus, string> = {
  queued: "bg-gray-100 text-gray-700",
  processing: "bg-navy-50 text-navy-600",
  needs_review: "bg-amber-50 text-amber-700",
  done: "bg-teal-50 text-teal-700",
  failed: "bg-rose-50 text-rose-700",
};

const STATUS_LABELS: Record<DocumentStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  needs_review: "Needs review",
  done: "Done",
  failed: "Failed",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function StatusBadge({ status }: { status: DocumentStatus }) {
  return (
    <Badge variant="secondary" className={cn("rounded-full border-0", STATUS_STYLES[status])}>
      {STATUS_LABELS[status]}
    </Badge>
  );
}

type StepState = "done" | "active" | "pending" | "failed" | "attention";

function Step({ state, label }: { state: StepState; label: string }) {
  return (
    <li className="flex items-center gap-3">
      <span
        aria-hidden
        className={cn(
          "flex size-[22px] shrink-0 items-center justify-center rounded-full border-2",
          state === "done" && "border-teal-500 bg-teal-500 text-white",
          state === "active" && "border-navy-600",
          state === "pending" && "border-gray-300",
          state === "failed" && "border-rose-500 bg-rose-500 text-white",
          state === "attention" && "border-amber-500 bg-amber-500 text-white",
        )}
      >
        {state === "attention" && <span className="text-xs leading-none font-bold">!</span>}
        {state === "done" && <Check className="size-3" strokeWidth={3} />}
        {state === "failed" && <X className="size-3" strokeWidth={3} />}
        {state === "active" && <Loader2 className="size-3 animate-spin text-navy-600" />}
      </span>
      <span
        className={cn(
          "text-sm",
          state === "pending" ? "text-gray-500" : "text-gray-900",
          state === "active" && "font-semibold text-navy-600",
        )}
      >
        {label}
      </span>
    </li>
  );
}

function ProgressCard({ document }: { document: DocumentOut }) {
  const terminal = TERMINAL_STATUSES.has(document.status);
  const failed = document.status === "failed";
  const processing: StepState = !terminal ? "active" : failed ? "failed" : "done";
  const result: StepState = !terminal
    ? "pending"
    : failed
      ? "failed"
      : document.status === "needs_review"
        ? "attention"
        : "done";
  const resultLabel =
    document.status === "done"
      ? "Ready"
      : document.status === "needs_review"
        ? "Needs your review"
        : failed
          ? "Processing failed"
          : "Result";

  return (
    <section
      aria-label="Upload progress"
      className="flex flex-col gap-5 rounded-xl border bg-white p-6 xl:flex-1"
    >
      <div className="flex flex-col gap-1.5">
        <p className="truncate text-[15px] font-semibold text-gray-900">{document.filename}</p>
        <p className="text-[13px] text-gray-500">
          {DOC_TYPE_LABELS[document.doc_type]} · {formatSize(document.size_bytes)}
        </p>
        <p data-testid="document-status" className="text-[13px] text-gray-500">
          {document.filename}: {document.status}
        </p>
      </div>
      <ol className="flex flex-col gap-4">
        <Step state="done" label="Uploaded" />
        <Step state={processing} label="Extracting and categorizing" />
        <Step state={result} label={resultLabel} />
      </ol>
    </section>
  );
}

export function Upload() {
  const queryClient = useQueryClient();
  const session = getSession();
  const token = session?.access_token ?? "";

  const [docType, setDocType] = useState<DocumentType>("invoice_or_receipt");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [previous, setPrevious] = useState<DocumentOut[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: current, error: pollError } = useQuery({
    queryKey: ["document", currentId],
    queryFn: () => getDocument(token, currentId!),
    enabled: currentId !== null && session !== null,
    staleTime: POLL_INTERVAL_MS,
    refetchIntervalInBackground: true,
    refetchInterval: (query) =>
      query.state.data && TERMINAL_STATUSES.has(query.state.data.status) ? false : POLL_INTERVAL_MS,
  });

  const upload = useMutation({
    mutationFn: async ({ file, docType }: { file: File; docType: DocumentType }) => {
      const { document, upload_url } = await requestDocumentUpload(token, file, docType);
      queryClient.setQueryData(["document", document.id], document);
      setCurrentId(document.id);
      await putFileToUploadUrl(upload_url, file);
      const completed = await completeDocumentUpload(token, document.id);
      queryClient.setQueryData(["document", document.id], completed);
    },
  });

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  function chooseFile(next: File | null) {
    setFile(next);
    setFormError(null);
  }

  function handleDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files?.[0] ?? null);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setFormError("Choose a file to upload.");
      return;
    }
    setFormError(null);
    if (current) setPrevious((docs) => [current, ...docs]);
    setCurrentId(null);
    upload.mutate(
      { file, docType },
      {
        onSuccess: () => {
          setFile(null);
          if (fileInputRef.current) fileInputRef.current.value = "";
        },
      },
    );
  }

  const errorMessage =
    formError ??
    (upload.error instanceof ApiError
      ? upload.error.message
      : upload.error
        ? "Upload failed. Please try again."
        : pollError instanceof ApiError
          ? pollError.message
          : pollError
            ? "Failed to fetch document status."
            : null);
  const sessionDocs = [...(current ? [current] : []), ...previous];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
          Upload documents
        </h1>
        <p className="text-sm text-gray-500">
          Add invoices, receipts or bank statements. We extract and categorize every transaction.
        </p>
      </header>

      <div className="flex flex-col gap-6 xl:flex-row xl:items-start">
        <form
          aria-label="Upload document"
          onSubmit={handleSubmit}
          className="flex flex-col gap-5 rounded-xl border bg-white p-6 xl:flex-[1.3]"
        >
          <div className="flex flex-col gap-2">
            <span id="doc-type-label" className="text-[13px] font-semibold text-gray-700">
              Document type
            </span>
            <div
              role="radiogroup"
              aria-labelledby="doc-type-label"
              className="flex gap-1 rounded-lg bg-gray-100 p-1"
            >
              {(Object.keys(DOC_TYPE_LABELS) as DocumentType[]).map((type) => (
                <button
                  key={type}
                  type="button"
                  role="radio"
                  aria-checked={docType === type}
                  onClick={() => setDocType(type)}
                  className={cn(
                    "flex-1 rounded-md px-3 py-2 text-sm text-gray-500 transition-colors",
                    docType === type && "bg-white font-semibold text-navy-600 shadow-sm",
                  )}
                >
                  {DOC_TYPE_LABELS[type]}
                </button>
              ))}
            </div>
          </div>

          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            className={cn(
              "flex flex-col items-center gap-3 rounded-xl border-2 border-dashed border-gray-300 bg-gray-50 px-6 py-12 text-center",
              dragging && "border-navy-600 bg-navy-50",
            )}
          >
            <span className="flex size-12 items-center justify-center rounded-full bg-navy-100 text-navy-600">
              <UploadCloud className="size-[22px]" aria-hidden />
            </span>
            {file ? (
              <>
                <p className="text-[15px] font-semibold text-gray-900">{file.name}</p>
                <p className="text-[13px] text-gray-500">{formatSize(file.size)}</p>
              </>
            ) : (
              <>
                <p className="text-[15px] font-semibold text-gray-900">
                  Drag a file here, or{" "}
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="text-navy-600 underline underline-offset-2"
                  >
                    browse
                  </button>
                </p>
                <p className="text-[13px] text-gray-500">PDF, JPG, PNG, CSV or OFX · up to 20 MB</p>
              </>
            )}
            <input
              ref={fileInputRef}
              type="file"
              aria-label="File"
              className="sr-only"
              onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
            />
          </div>

          {errorMessage && (
            <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
              {errorMessage}
            </p>
          )}

          <Button type="submit" disabled={upload.isPending} className="h-11 w-full">
            {upload.isPending && <Loader2 className="animate-spin" aria-hidden />}
            {upload.isPending ? "Uploading…" : "Upload"}
          </Button>
        </form>

        {current && <ProgressCard document={current} />}
      </div>

      {sessionDocs.length > 0 && (
        <section aria-label="Uploaded this session" className="rounded-xl border bg-white">
          <h2 className="px-6 py-[18px] text-base font-bold text-gray-900">
            Uploaded this session
          </h2>
          <ul>
            {sessionDocs.map((document) => (
              <li
                key={document.id}
                className="flex items-center gap-4 border-t border-gray-100 px-6 py-3.5"
              >
                <span className="min-w-0 flex-1 truncate text-sm text-gray-900">
                  {document.filename}
                </span>
                <span className="w-36 text-sm text-gray-500">
                  {DOC_TYPE_LABELS[document.doc_type]}
                </span>
                <span className="flex w-32">
                  <StatusBadge status={document.status} />
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
