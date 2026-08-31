---
status: accepted
---

# Single currency per Organization

Transactions and reconciliation matching rely on comparing amounts directly, and the dashboard aggregates by summing across Transactions. Supporting per-Transaction currency would require an FX-conversion layer (rate source, historical rate lookup, rounding rules) touching matching, aggregation, and export. We decided to scope v1 to a single currency set once per Organization at signup, with no conversion logic anywhere in the system. A multi-currency Organization is not supported — a business that transacts in more than one currency would need a workaround (e.g. a separate Organization per currency) until this is revisited.
