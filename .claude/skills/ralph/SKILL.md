---
name: ralph
description: "Automated ticket execution loop: claim ready-for-agent GitHub issues, implement each in an isolated worktree via /implement, validate, and open a linked (possibly stacked) PR."
disable-model-invocation: true
---

One `/ralph` invocation is one discovery-and-dispatch tick, not a standing
loop. Conventions (state machine, labels, dependency resolution, worktree
layout, PR template, test isolation) are documented in full in
[docs/agents/ralph.md](../../../docs/agents/ralph.md) — read it first if
anything below is ambiguous, it is the source of truth.

Infer `<owner>/<repo>` from `git remote -v` as usual.

## Orchestrator procedure

Run this yourself, in this turn, before spawning anything.

1. **Precondition**: confirm Postgres is reachable, e.g.
   `pg_isready -h localhost -p 5432` or a `psql ... -c 'select 1'`. If it's
   not reachable, stop and tell the user to bring up the shared
   docker-compose Postgres first — do not start it yourself.

2. **Discover** the pickup pool:
   ```bash
   gh issue list --state open --label ready-for-agent \
     --json number,title,body,labels,assignees
   ```

3. **Compute concurrency slots**:
   ```bash
   gh issue list --state open --label ralph:in-progress --json number
   ```
   `in_flight = len(...)`, `slots = 2 - in_flight`. If `slots <= 0`, report
   that no slots are free and stop — do not dispatch anything.

4. **Filter to the eligible set.** For each issue `n` from step 2, apply the
   dependency/stacking rules in `docs/agents/ralph.md` § "Dependency /
   stacking resolution":
   - Query `gh api repos/<owner>/<repo>/issues/<n> --jq '.issue_dependencies_summary'`.
   - `blocked_by == 0` → eligible, base = `main`.
   - `blocked_by > 0` → for every open blocker, check
     `gh pr list --head "claude/issue-<b>-*" --state open --json number,headRefName`.
     Eligible (stacked, base = that head branch) only if **every** open
     blocker has an open PR; otherwise skip this issue for this tick.
   - If `issue_dependencies_summary` is null, fall back to parsing a
     `## Blocked by` section in the issue body and apply the same logic
     per referenced issue via `gh issue view <b> --json state`.

5. **Select** up to `slots` eligible tickets, oldest issue number first.
   If none are eligible, report that plainly (e.g. "N issues in the pool,
   all blocked on tickets without an open PR yet") and stop.

6. **Claim sequentially**, one ticket at a time, *before* spawning any
   subagent — this is what prevents two subagents racing to claim the same
   ticket, since `gh` has no compare-and-swap:
   ```bash
   gh issue edit <n> --add-assignee @me --add-label ralph:in-progress --remove-label ready-for-agent
   ```

7. **Dispatch**: for each claimed ticket, spawn one `Agent` tool call
   (running concurrently, background is fine) with a fully self-contained
   prompt covering: the issue number, the resolved base branch, the
   worktree path (`.claude/worktrees/issue-<n>-<slug>`), the branch name
   (`claude/issue-<n>-<hash>`), whether this is a stacked PR (and which
   issue it depends on, for the `Depends on #<b>` PR body line), and the
   full "Per-ticket executor procedure" below (paste it into the prompt —
   the spawned agent has no access to this conversation).

8. **Report** a short summary: which tickets were dispatched, with what
   base branch each, and how many slots remain.

## Per-ticket executor procedure

Followed by each spawned agent, entirely inside its own worktree. The
issue number, base branch, worktree path, branch name, and stacking info
are given in your prompt — you do not need to re-derive them.

1. **Create the worktree**:
   ```bash
   git worktree add <worktree-path> -b <branch-name> <base-branch>
   ```
   Do all further work `cd`'d into `<worktree-path>`.

2. **Fetch the ticket**: `gh issue view <n> --comments`. This repo's issues
   currently carry the spec inline as `## What to build` / `## Acceptance
   criteria` / `## Blocked by` sections in the body (rather than a separate
   Agent Brief comment) — use whichever form is present, normalizing into
   one spec block: summary, desired behavior / what to build, acceptance
   criteria, out-of-scope notes if any.

3. **Read domain context** first, per `docs/agents/domain.md`: `CONTEXT.md`
   and any relevant `docs/adr/*.md` at the repo root, so implementation
   uses the established vocabulary.

4. **Implement**: follow `/implement`'s practices directly rather than
   invoking it as a skill — `/implement` has `disable-model-invocation:
   true`, so it refuses non-interactive invocation and there is no human
   here to run it themselves. Concretely: use TDD where it fits at
   pre-agreed seams, run typechecking and single test files regularly as
   you go, run the full test suite once near the end, review your own
   diff critically before calling it done (the same bar `/code-review`
   would apply), and commit to your branch as you go rather than in one
   final commit.

5. **Validate** (this is Ralph's own gate — there is no CI in this repo):
   ```bash
   # backend, isolated test DB:
   DB_NAME="reconcilio_test_issue_<n>"
   PGPASSWORD=reconcilio psql -h localhost -U reconcilio -d postgres \
     -c "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
     || PGPASSWORD=reconcilio createdb -h localhost -U reconcilio "$DB_NAME"
   export DATABASE_URL="postgresql+psycopg://reconcilio:reconcilio@localhost:5432/${DB_NAME}"
   (cd backend && uv run pytest && uv run mypy app)

   # frontend:
   (cd frontend && npm test && npm run typecheck && npm run build)
   ```
   All four must pass. If `/implement` already left something failing, fix
   it directly (still inside this worktree) rather than re-invoking
   `/implement` from scratch.

6. **On success**:
   ```bash
   git push -u origin <branch-name>
   gh pr create --base <base-branch> --head <branch-name> \
     --title "..." --body "$(cat <<'EOF'
   ## Summary
   - ...

   ## Test plan
   - [x] ...

   Depends on #<b>   # only if stacked; one line per open blocker, omit otherwise
   Closes #<n>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   gh issue edit <n> --remove-label ralph:in-progress --add-label ralph:pr-open
   gh issue comment <n> --body "Opened <pr-url>"
   git worktree remove <worktree-path>
   ```

7. **On failure** (validation still red after a genuine attempt to fix it):
   ```bash
   gh issue edit <n> --remove-label ralph:in-progress --add-label ralph:failed --remove-assignee @me
   gh issue comment <n> --body "<what was tried, why it's still failing, and that the worktree at <worktree-path> is left in place for debugging>"
   ```
   Do **not** remove the worktree. Do **not** retry — one attempt only, per
   `docs/agents/ralph.md`.
