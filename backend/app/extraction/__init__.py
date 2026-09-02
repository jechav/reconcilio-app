"""Real invoice/receipt extraction: Textract first pass + LLM refinement.

See docs/agents/... and CONTEXT.md for the domain vocabulary (ExtractionResult,
confidence threshold). This package is deliberately split from app/pipeline.py
so the pipeline graph stays about *orchestration* (node wiring, state) while
this package owns the actual third-party integrations behind small,
mockable interfaces.
"""
