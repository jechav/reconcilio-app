---
status: accepted
---

# Reconciliation matches are strictly one-to-one

A bank deposit can legitimately cover several invoices, and a split payment can cover one invoice with several bank transactions — real many-to-one and one-to-many cases exist. We decided ReconciliationMatch models only one-to-one links between an expense-source Transaction and a bank Transaction; anything that doesn't resolve one-to-one is left unmatched and flagged for review rather than attempted as a combinatorial match. This trades completeness for a simple, predictable matching algorithm and data model in v1. Users can still manually link items case-by-case (see manual match, CONTEXT.md), but the system will never auto-propose a split or combined match.
