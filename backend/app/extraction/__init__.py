"""Real extraction: Textract first pass + LLM refinement, for both the
invoice/receipt (issue #3) and bank-statement (issue #4) paths, plus
CSV/OFX structured parsing (issue #4 only -- no OCR/LLM equivalent).

See CONTEXT.md for the domain vocabulary (ExtractionResult, Transaction,
confidence threshold). This package is deliberately split from
app/pipeline.py so the pipeline graph stays about *orchestration* (node
wiring, state) while this package owns the actual third-party integrations
and parsing behind small, mockable interfaces.
"""
