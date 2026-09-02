# Ralph: automated ticket execution

Ralph is an on-demand loop, invoked via `/ralph`, that picks up `ready-for-agent`
issues, implements them in an isolated git worktree, validates the result,
and opens a linked pull request — stacking PRs when a ticket depends on
another that's still in flight. It follows the "Ralph Wiggum" methodology: a
simple, repeatable loop rather than a smart planner.

The per-ticket executor follows `/implement`'s *practices* (TDD at natural
seams, regular typechecking, a full test-suite run, self-review, incremental
commits) rather than invoking `/implement` as a skill — `/implement` has
`disable-model-invocation: true` and refuses non-interactive invocation, and
there is no human present in a Ralph run to invoke it themselves.

Each `/ralph` invocation does exactly one discovery-and-dispatch tick against
live GitHub state. It is **not** scheduled or backgrounded — a human runs it
each time they want the pool drained further. This keeps state entirely in
GitHub (labels + assignee), so nothing needs to survive locally between
invocations, and any bug in the claim logic can't compound silently across
unattended ticks.

## State machine

Three orchestration-only labels track a ticket's progress through Ralph.
They are distinct from the five canonical triage labels in
[triage-labels.md](./triage-labels.md) — `ready-for-agent` is the entry
point, but `ralph:in-progress` / `ralph:pr-open` / `ralph:failed` only exist
to make Ralph's own state visible in the GitHub UI.

```
ready-for-agent, unassigned                          [pickup pool]
        |  claim (orchestrator, sequential, before fan-out):
        |    gh issue edit <n> --add-assignee @me --add-label ralph:in-progress --remove-label ready-for-agent
        v
ralph:in-progress, assigned                           [claimed; concurrency count = count of this label]
        |
        +-- success: gh pr create ...
        |     gh issue edit <n> --remove-label ralph:in-progress --add-label ralph:pr-open
        |     -> ralph:pr-open, assigned  [terminal; issue auto-closes when the PR (or PR chain) merges to main]
        |
        +-- failure (validation never goes green):
              gh issue edit <n> --remove-label ralph:in-progress --add-label ralph:failed --remove-assignee @me
              gh issue comment <n> --body "<what was tried, why it failed, worktree path>"
              worktree left in place (not removed)
              -> ralph:failed, unassigned  [human queue: gh issue list --label ralph:failed]
```

`ready-for-agent` is removed at **claim** time, not at PR-open time — this is
what keeps the pickup-pool query and the in-flight count in sync with no
separate bookkeeping. The concurrency cap (max 2 concurrent tickets) is
enforced purely as `len(gh issue list --state open --label ralph:in-progress)`.

## Dependency / stacking resolution

For each candidate issue `n` in the `ready-for-agent` pool:

```bash
gh api repos/<owner>/<repo>/issues/<n> --jq '.issue_dependencies_summary'
# {blocked_by, blocking, total_blocked_by, total_blocking} — blocked_by counts only OPEN blockers
```

| Blocker state | Action for dependent |
|---|---|
| `blocked_by == 0` | Eligible now. Base branch = `main`. |
| Every open blocker has an **open PR** | Eligible now, stacked. Base branch = that blocker's PR head branch. |
| Any open blocker has no PR yet (unclaimed, or still implementing) | **Not eligible this tick.** Skip — a dependent only starts once its blocker has a real PR to stack on, never on a worktree branch that might still be amended. |

If `issue_dependencies_summary` is null/absent for an issue (native
dependencies not populated), fall back to parsing a `## Blocked by` section
in the issue body for `#<n>` references, and apply the same
open/has-a-PR logic to each referenced issue via `gh issue view <b> --json state`.

To find a blocker's open PR:

```bash
gh pr list --head "claude/issue-<b>-*" --state open --json number,headRefName,baseRefName
```

Among the eligible set, select up to `2 - in_flight` tickets, **oldest issue
number first**, for determinism.

## Branch, worktree, and PR conventions

- Worktree path: `.claude/worktrees/issue-<n>-<slug>`
- Branch name: `claude/issue-<n>-<hash>` (matches the convention established
  by PR #13; `hash` is a short random suffix so a retried ticket doesn't
  collide with a leftover branch)
- Created by the per-ticket executor, immediately after its claim succeeds:
  ```bash
  git worktree add .claude/worktrees/issue-<n>-<slug> -b claude/issue-<n>-<hash> <base-branch>
  ```
- On success (PR pushed): `git worktree remove .claude/worktrees/issue-<n>-<slug>`
  — the branch persists as a ref, only the working directory goes away.
- On failure: the worktree is left in place as the debugging artifact a
  human opens next.

PR body template (matches PR #13's format):

```markdown
## Summary
- ...

## Test plan
- [x] ...

Depends on #<b>       <!-- only for stacked PRs, one line per open blocker -->
Closes #<n>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

`--base` for `gh pr create` is the literal blocker branch name for a stacked
PR, or `main` otherwise. GitHub retargets a stacked PR to `main` on its own
once the blocker PR merges — Ralph does not manage restacking.

## Postgres test isolation

Two tickets can be validating concurrently, so each gets its own test
database rather than sharing one:

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
- The issue comment left by the executor explains what was tried and why
  it didn't validate, and names the worktree path for direct debugging.
- Ralph does **not** auto-retry — a real failure retried unattended just
  burns tokens repeating the same mistake.
- To requeue: a human fixes the underlying issue (in the brief, in the
  code, or manually in the worktree), then runs
  `gh issue edit <n> --add-label ready-for-agent --remove-label ralph:failed`
  to put it back in the pickup pool.

## Future: scheduled / workflow-based execution

This iteration is deliberately manual-invocation-only. If usage grows to
where continuous draining of the pool is wanted, the natural upgrade path
is the `Workflow` tool (true worktree isolation, a native concurrency cap,
`parallel`/`pipeline` primitives) driven by a recurring `CronCreate`
schedule — the orchestrator/executor split above maps directly onto a
workflow's discovery step and per-ticket `agent()` calls. Not built now;
requires an explicit opt-in to the `Workflow` tool.
