## Agent skills

### Issue tracker

Issues live as GitHub issues (uses the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical role labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout (`CONTEXT.md` + `docs/adr/` at repo root). See `docs/agents/domain.md`.

### Automated ticket execution (Ralph)

`/ralph` drains the whole `ready-for-agent` pool in one invocation: claims every currently eligible issue, implements each in its own git worktree via `/implement`, validates, opens a PR, and merges it (resolving conflicts) before re-checking for newly-unblocked issues. See `docs/agents/ralph.md`.
