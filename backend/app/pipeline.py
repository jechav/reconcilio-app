"""Stub extraction pipeline (issue #2).

Real OCR/LLM extraction, validation, and categorization land in a later
ticket. For now each node is a no-op pass-through so the async plumbing
(upload -> Celery job -> pipeline -> Document.status transitions) can be
proven end to end. The graph shape (classify -> ocr_extract -> llm_refine
-> validate -> categorize -> persist) is the one real extraction will grow
into, so later work only needs to replace node bodies, not this structure.
"""

import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.models import Document, DocumentStatus


class PipelineState(TypedDict):
    document_id: str


def classify(state: PipelineState) -> PipelineState:
    return state


def ocr_extract(state: PipelineState) -> PipelineState:
    return state


def llm_refine(state: PipelineState) -> PipelineState:
    return state


def validate(state: PipelineState) -> PipelineState:
    return state


def categorize(state: PipelineState) -> PipelineState:
    return state


def persist(state: PipelineState) -> PipelineState:
    return state


def _build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("classify", classify)
    graph.add_node("ocr_extract", ocr_extract)
    graph.add_node("llm_refine", llm_refine)
    graph.add_node("validate", validate)
    graph.add_node("categorize", categorize)
    graph.add_node("persist", persist)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "ocr_extract")
    graph.add_edge("ocr_extract", "llm_refine")
    graph.add_edge("llm_refine", "validate")
    graph.add_edge("validate", "categorize")
    graph.add_edge("categorize", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


_pipeline = _build_graph()


def run_pipeline(document_id: uuid.UUID, db: Session) -> Document:
    """Drive a Document through the stub pipeline: queued -> processing -> done.

    Callable directly (no HTTP, no Celery) so it can be exercised as a
    pipeline-as-a-function unit test, and reused by the Celery task.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError(f"Document {document_id} not found")

    document.status = DocumentStatus.processing
    db.commit()

    try:
        _pipeline.invoke({"document_id": str(document_id)})
    except Exception:
        document.status = DocumentStatus.failed
        db.commit()
        raise

    document.status = DocumentStatus.done
    db.commit()
    db.refresh(document)
    return document
