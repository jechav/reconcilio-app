import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Navigate } from "react-router-dom";

import {
  ApiError,
  completeDocumentUpload,
  getDocument,
  putFileToUploadUrl,
  requestDocumentUpload,
  type DocumentOut,
  type DocumentType,
} from "../api/client";
import { getSession } from "../session";

const POLL_INTERVAL_MS = 1500;
// needs_review is terminal for the pipeline: extraction finished, but a
// human has to clear at least one Transaction before the Document is done.
const TERMINAL_STATUSES = new Set(["done", "needs_review", "failed"]);

export function Upload() {
  const session = getSession();
  const [docType, setDocType] = useState<DocumentType>("invoice_or_receipt");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [document, setDocument] = useState<DocumentOut | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  if (!session) {
    return <Navigate to="/login" replace />;
  }
  const token = session.access_token;

  function schedulePoll(documentId: string) {
    pollTimer.current = setTimeout(async () => {
      try {
        const latest = await getDocument(token, documentId);
        setDocument(latest);
        if (!TERMINAL_STATUSES.has(latest.status)) {
          schedulePoll(documentId);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Failed to fetch document status.");
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const file = fileInputRef.current?.files?.[0];
    if (!file) {
      setError("Choose a file to upload.");
      return;
    }

    setError(null);
    setSubmitting(true);
    setDocument(null);
    try {
      const { document: created, upload_url } = await requestDocumentUpload(token, file, docType);
      setDocument(created);
      await putFileToUploadUrl(upload_url, file);
      const completed = await completeDocumentUpload(token, created.id);
      setDocument(completed);
      if (!TERMINAL_STATUSES.has(completed.status)) {
        schedulePoll(created.id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h1>Upload a document</h1>

      <form onSubmit={handleSubmit} aria-label="Upload document">
        <label htmlFor="doc-type">Document type</label>
        <select
          id="doc-type"
          name="doc_type"
          value={docType}
          onChange={(event) => setDocType(event.target.value as DocumentType)}
        >
          <option value="invoice_or_receipt">Invoice or receipt</option>
          <option value="bank_statement">Bank statement</option>
        </select>

        <label htmlFor="doc-file">File</label>
        <input id="doc-file" name="file" type="file" ref={fileInputRef} />

        {error && <p role="alert">{error}</p>}

        <button type="submit" disabled={submitting}>
          {submitting ? "Uploading…" : "Upload"}
        </button>
      </form>

      {document && (
        <p data-testid="document-status">
          {document.filename}: {document.status}
        </p>
      )}
    </div>
  );
}
