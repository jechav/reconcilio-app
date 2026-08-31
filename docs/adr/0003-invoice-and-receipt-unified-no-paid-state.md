---
status: accepted
---

# Invoice and Receipt are unified, with no paid/unpaid state

An Invoice (an obligation to pay, possibly still unpaid) and a Receipt (proof of a completed payment) are conceptually different in accounting terms, and a reasonable reader would expect the system to track which invoices are paid. We decided to model both as a single "expense-source Transaction" type with no payment-status field. Reconciliation against bank Transactions already produces the signal we actually want: an expense-source Transaction with no matching bank Transaction is exactly the "looks unpaid, or paid through an untracked channel" case that matters for tax risk. Adding an explicit paid/unpaid state would duplicate that signal without changing what the user sees. This can be revisited if a future requirement needs to distinguish "known unpaid" from "paid but unmatched."
