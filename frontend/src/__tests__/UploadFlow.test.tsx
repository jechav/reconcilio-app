import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Upload } from "../pages/Upload";
import { saveSession } from "../session";

function renderUpload() {
  return render(
    <MemoryRouter initialEntries={["/upload"]}>
      <Routes>
        <Route path="/upload" element={<Upload />} />
      </Routes>
    </MemoryRouter>,
  );
}

const session = {
  access_token: "test-token",
  token_type: "bearer",
  user: { id: "u1", email: "owner@example.com" },
  organization: { id: "o1", name: "Acme Tax" },
  role: "owner" as const,
};

const queuedDocument = {
  id: "d1",
  filename: "invoice.pdf",
  content_type: "application/pdf",
  size_bytes: 1024,
  doc_type: "invoice_or_receipt",
  status: "queued",
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

beforeEach(() => {
  localStorage.clear();
  saveSession(session);
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("upload flow", () => {
  it("uploads a file, completes it, and polls until done", async () => {
    const fetchMock = fetch as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ document: queuedDocument, upload_url: "https://minio.local/presigned" }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) }) // PUT to MinIO
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...queuedDocument, status: "processing" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...queuedDocument, status: "done" }),
      });

    const user = userEvent.setup();
    renderUpload();

    const file = new File(["pdf-bytes"], "invoice.pdf", { type: "application/pdf" });
    const fileInput = screen.getByLabelText(/file/i);
    await user.upload(fileInput, file);
    await user.click(screen.getByRole("button", { name: /upload/i }));

    await waitFor(() => expect(screen.getByTestId("document-status")).toHaveTextContent("processing"));

    await waitFor(
      () => expect(screen.getByTestId("document-status")).toHaveTextContent("done"),
      { timeout: 5000 },
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      expect.stringContaining("/documents"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "https://minio.local/presigned",
      expect.objectContaining({ method: "PUT" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      expect.stringContaining("/documents/d1/complete"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows an error message when the upload request fails", async () => {
    const fetchMock = fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({ detail: "Unsupported file type '.exe'." }),
    });
    const user = userEvent.setup();

    renderUpload();
    const file = new File(["x"], "malware.exe", { type: "application/octet-stream" });
    await user.upload(screen.getByLabelText(/file/i), file);
    await user.click(screen.getByRole("button", { name: /upload/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unsupported file type '.exe'.");
  });
});
