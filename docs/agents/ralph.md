# Ralph: automated ticket execution

Ralph is an on-demand loop, invoked via `/ralph`, that picks up `ready-for-agent`
issues, implements each in its own isolated git worktree, validates the
result, opens a pull request, and merges it into `main` (resolving conflicts
itself) — which in turn unblocks any dependent ticket, so a single
invocation can drain an entire dependency chain. It follows the
"Ralph Wiggum" methodology: a simple, repeatable loop rather than a smart
planner.

Ralph processes **exactly one ticket at a time, start to merge**, before
claiming the next one. This is a deliberate choice, not a performance
shortcut: it keeps `/implement`'s TDD → validate → merge flow fully
attended for each ticket, avoids the review load of several PRs landing at
once, and means a bad ticket is caught and left in `ralph:failed` before
any sibling ticket's work builds on top of it. There is exactly one ticket
in `ralph:in-progress` or `ralph:pr-open` at any moment during a Ralph run.

The per-ticket executor follows `/implement`'s *practices* (TDD at natural
seams, regular typechecking, a full test-suite run, self-review, incremental
commits) rather than invoking `/implement` as a skill — `/implement` has
`disable-model-invocation: true` and refuses non-interactive invocation, and
there is no human present in a Ralph run to invoke it themselves.

Each `/ralph` invocation loops against live GitHub state — pick the single
best eligible issue, claim it, dispatch it, wait for it to either merge or
fail, re-check for newly-eligible issues (a merge can unblock one), repeat
— until the pool is fully drained. It is **not** scheduled or backgrounded
— a human runs it when they want the pool drained. This keeps state
entirely in GitHub (labels + assignee), so nothing needs to survive locally
between invocations, and any bug in the claim logic can't compound silently
across unattended runs.

## State machine

Three orchestration-only labels track a ticket's progress through Ralph.
They are distinct from the five canonical triage labels in
[triage-labels.md](./triage-labels.md) — `ready-for-agent` is the entry
point, but `ralph:in-progress` / `ralph:pr-open` / `ralph:failed` only exist
to make Ralph's own state visible in the GitHub UI.

```
ready-for-agent, unassigned                          [pickup pool]
        |  claim (orchestrator, exactly one ticket, before dispatch):
        |    gh issue edit <n> --add-assignee @me --add-label ralph:in-progress --remove-label ready-for-agent
        v
ralph:in-progress, assigned          [claimed; the only in-flight ticket]
        |  orchestrator dispatches one Agent call and waits for it to
        |  finish (foreground) before doing anything else
        |
        +-- success: gh pr create ...
        |     gh issue edit <n> --remove-label ralph:in-progress --add-label ralph:pr-open
        |     -> ralph:pr-open, assigned  [executor reports back; worktree left in place]
        |         |
        |         |  orchestrator, immediately, before claiming anything else:
        |         |    gh pr merge <pr> --merge
        |         |    on conflict: resolve in the worktree, re-validate, retry
        |         v
        |     merged to main -> issue auto-closes (PR body's "Closes #n")
        |     git worktree remove <worktree-path>
        |     -> unblocks any dependent still waiting on this issue
        |     -> only now does the orchestrator pick the next ticket to claim
        |
        +-- failure (validation never goes green, OR merge conflict that
            can't be resolved cleanly):
              gh issue edit <n> --remove-label ralph:in-progress --add-label ralph:failed --remove-assignee @me
              (or --remove-label ralph:pr-open, if it failed at the merge step)
              gh issue comment <n> --body "<what was tried, why it failed, worktree path>"
              worktree left in place (not removed)
              -> ralph:failed, unassigned  [human queue: gh issue list --label ralph:failed; also blocks any dependent indefinitely]
              -> orchestrator moves on to pick the next eligible ticket
```

`ready-for-agent` is removed at **claim** time, not at PR-open time — this is
what keeps the pickup-pool query in sync with no separate bookkeeping. Ralph
enforces a strict concurrency cap of **one**: at most one issue carries
`ralph:in-progress` or `ralph:pr-open` at any moment. The orchestrator does
not claim, dispatch, or even pick a second ticket until the current one has
either merged to `main` or been moved to `ralph:failed`. This trades
throughput for attention — each ticket gets `/implement`'s full TDD →
validate → merge flow watched end to end before the next one starts, and a
failing ticket never lets a sibling build on top of unmerged, unvalidated
work.

## Dependency / stacking resolution

For each candidate issue `n` in the `ready-for-agent` pool:

```bash
gh api repos/<owner>/<repo>/issues/<n> --jq '.issue_dependencies_summary'
# {blocked_by, blocking, total_blocked_by, total_blocking} — blocked_by counts only OPEN blockers
```

| Blocker state | Action for dependent |
|---|---|
| `blocked_by == 0` | Eligible now. Base branch = `main`. |
| Every blocker's issue is **closed** (i.e. merged by Ralph — see "Postgres test isolation" and the orchestrator's merge step below) | Eligible now. Base branch = `main`. |
| Any blocker still open (not yet merged) | **Not eligible yet.** Skip — recheck after the next merge lands; a dependent never bases on an unmerged branch. |

Since an issue only auto-closes via a merged PR's `Closes #n` (Ralph never
closes an issue by hand), "issue closed" is a reliable merged-signal on its
own — no separate PR-state query is needed.

If `issue_dependencies_summary` is null/absent for an issue (native
dependencies not populated), fall back to parsing a `## Blocked by` section
in the issue body for `#<n>` references, and apply the same closed-check to
each referenced issue via `gh issue view <b> --json state`.

Among the eligible set, pick **one** — the lowest issue number, for
determinism — claim it, and dispatch it alone. Do not claim or dispatch any
other issue until this one has merged or failed. Re-run this eligibility
check after every merge and after every failure, since a merge can unblock
issues that weren't eligible a moment ago; repeat until the pool is
drained.

## Branch, worktree, and PR conventions

- Worktree path: `.claude/worktrees/issue-<n>-<slug>`
- Branch name: `claude/issue-<n>-<hash>` (matches the convention established
  by PR #13; `hash` is a short random suffix so a retried ticket doesn't
  collide with a leftover branch)
- Created by the per-ticket executor, immediately after its claim succeeds:
  ```bash
  git worktree add .claude/worktrees/issue-<n>-<slug> -b claude/issue-<n>-<hash> main
  ```
- On success (PR pushed): the per-ticket executor leaves the worktree in
  place — it does not remove it. The orchestrator removes it
  (`git worktree remove .claude/worktrees/issue-<n>-<slug>`) once it has
  merged the PR, so the worktree is still there if a merge conflict needs
  resolving in it first.
- On failure (validation or an unresolvable merge conflict): the worktree is
  left in place as the debugging artifact a human opens next.

PR body template (matches PR #13's format):

```markdown
## Summary
- ...

## Test plan
- [x] ...

Closes #<n>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

`--base` for `gh pr create` is always `main` — every ticket, including
dependents, only ever starts once its blocker is already merged, so there is
no stacking or PR-retargeting to manage.

## Postgres test isolation

Only one ticket validates at a time under the current one-at-a-time
orchestrator, but each ticket still gets its own test database named after
its issue number rather than a shared one — this keeps a requeued or
re-run ticket from colliding with leftover state from a previous attempt,
and keeps the recipe correct if the orchestrator is ever changed back to
running tickets concurrently:

```bash
DB_NAME="reconcilio_test_issue_<n>"
PGPASSWORD=reconcilio psql -h localhost -U reconcilio -d postgres \
  -c "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
  || PGPASSWORD=reconcilio createdb -h localhost -U reconcilio "$DB_NAME"

export DATABASE_URL="postgresql+psycopg://reconcilio:reconcilio@localhost:5432/${DB_NAME}"
cd backend && uv run pytest
```

`backend/tests/conftest.py` uses `os.environ.setdefault("DATABASE_URL", ...)`,
so this override works with zero backend code changes. Each ticket's schema
is created independently (`Base.metadata.create_all`, no Alembic involved),
so there's no cross-ticket contention.

This requires the shared docker-compose Postgres to already be running.
Ralph's orchestrator checks this precondition up front and aborts with a
clear message if it's unreachable — it does not start or stop shared infra
itself, since doing so from inside a per-ticket executor would itself be a
concurrency hazard.

The `reconcilio`/`reconcilio` credentials above are the `docker-compose.yml`
defaults, but a long-running local Postgres container can carry different
actual credentials (e.g. a container started before this repo was renamed
from TaxDocs still answering to `taxdocs`/`taxdocs`). If the recipe above
fails to connect, check what the running container actually accepts before
assuming the database is down.

## Failure handling and requeue

When a ticket lands in `ralph:failed`:

- It's unassigned, so `gh issue list --state open --label ralph:failed`
  is the queue a human checks.
- The comment left on the issue explains what was tried and why it didn't
  validate — or, for a failure at the merge step, why the merge conflict
  couldn't be resolved cleanly — and names the worktree path for direct
  debugging.
- Ralph does **not** auto-retry — a real failure retried unattended just
  burns tokens repeating the same mistake. Note that a failed ticket also
  blocks any dependent indefinitely, since a dependent only becomes
  eligible once its blocker is merged.
- To requeue: a human fixes the underlying issue (in the brief, in the
  code, or manually in the worktree), then runs
  `gh issue edit <n> --add-label ready-for-agent --remove-label ralph:failed`
  to put it back in the pickup pool.

## Future: scheduled / workflow-based execution

A single `/ralph` invocation now drains the whole pool on its own — claim,
dispatch, wait for merge or failure, re-check for newly-unblocked issues,
repeat until nothing eligible remains — rather than doing one tick and
stopping. It processes strictly one ticket at a time by design (see above),
not for lack of a concurrency mechanism. It's still manual-invocation-only,
not scheduled. If a future need calls for higher throughput or continuous,
unattended draining, the natural upgrade path is the `Workflow` tool (true
worktree isolation, native `parallel`/`pipeline` primitives) driven by a
recurring `CronCreate` schedule — but that would be a deliberate return to
concurrent execution, and should only be adopted if the one-at-a-time
attentiveness this design trades for is no longer needed. Not built now;
requires an explicit opt-in to the `Workflow` tool.
