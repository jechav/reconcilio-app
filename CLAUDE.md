## Agent skills

### Issue tracker

Issues live as GitHub issues (uses the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical role labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout (`CONTEXT.md` + `docs/adr/` at repo root). See `docs/agents/domain.md`.

### Automated ticket execution (Ralph)

`/ralph` claims `ready-for-agent` issues (up to 2 concurrent), implements each in its own git worktree via `/implement`, validates, and opens a linked (possibly stacked) PR. See `docs/agents/ralph.md`.
