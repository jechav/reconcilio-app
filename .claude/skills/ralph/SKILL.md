---
name: ralph
description: "Automated ticket execution loop: claim every eligible ready-for-agent GitHub issue, implement each in its own isolated worktree via /implement, validate, open a linked PR, and merge it (resolving conflicts) before picking up newly-unblocked issues."
disable-model-invocation: true
---

One `/ralph` invocation drains the whole `ready-for-agent` pool in a loop —
claim, dispatch, merge each PR as it lands, re-check for newly-unblocked
issues, repeat — rather than doing a single tick. It is still only
invoked manually, not scheduled. Conventions (state machine, labels,
dependency resolution, worktree layout, PR template, test isolation) are
documented in full in [docs/agents/ralph.md](../../../docs/agents/ralph.md)
— read it first if anything below is ambiguous, it is the source of truth.

Infer `<owner>/<repo>` from `git remote -v` as usual.

## Orchestrator procedure

Run this yourself, in this turn, before spawning anything. This loops until
the pool is drained: dispatch every currently eligible issue, merge each PR
as it lands (which can unblock more issues), re-check eligibility, and
repeat until nothing eligible remains and nothing is in flight.

1. **Precondition**: confirm Postgres is reachable, e.g.
   `pg_isready -h localhost -p 5432` or a `psql ... -c 'select 1'`. If it's
   not reachable, stop and tell the user to bring up the shared
   docker-compose Postgres first — do not start it yourself.

2. **Discover** the pickup pool:
   ```bash
   gh issue list --state open --label ready-for-agent \
     --json number,title,body,labels,assignees
   ```

3. **Loop** until there are no unclaimed `ready-for-agent` issues left *and*
   nothing is in flight (`ralph:in-progress` or `ralph:pr-open`):

   a. **Filter to the eligible set.** For each unclaimed issue `n`, apply
      the dependency rules in `docs/agents/ralph.md` § "Dependency /
      stacking resolution":
      - Query `gh api repos/<owner>/<repo>/issues/<n> --jq '.issue_dependencies_summary'`.
      - `blocked_by == 0` → eligible, base = `main`.
      - `blocked_by > 0` → eligible, base = `main`, only if **every**
        blocker issue is **closed** (`gh issue view <b> --json state`).
        Otherwise not eligible yet.
      - If `issue_dependencies_summary` is null, fall back to parsing a
        `## Blocked by` section in the issue body and apply the same
        closed-check per referenced issue.

   b. If nothing is eligible and nothing is currently in flight, stop
      looping and report (e.g. "N issues remain, all blocked on tickets
      that haven't merged yet").

   c. **Claim** every eligible issue sequentially, one at a time, *before*
      spawning any subagent — this is what prevents two subagents racing
      to claim the same ticket, since `gh` has no compare-and-swap:
      ```bash
      gh issue edit <n> --add-assignee @me --add-label ralph:in-progress --remove-label ready-for-agent
      ```

   d. **Dispatch**: for each newly-claimed ticket, spawn one `Agent` tool
      call (running concurrently, background is fine — there is no cap on
      how many run at once) with a fully self-contained prompt covering:
      the issue number, the worktree path
      (`.claude/worktrees/issue-<n>-<slug>`), the branch name
      (`claude/issue-<n>-<hash>`), that the base branch is `main`, and the
      full "Per-ticket executor procedure" below (paste it into the
      prompt — the spawned agent has no access to this conversation).

   e. **As each per-ticket agent reports back**, handle it:
      - *Validation failure*: nothing further to do — the executor already
        left it in `ralph:failed` per its own procedure.
      - *PR opened*: merge it yourself, sequentially (only one merge into
        `main` at a time, even if several PRs finish around the same
        time):
        ```bash
        gh pr merge <pr-number> --merge
        ```
        - On success: `git worktree remove <worktree-path>`. The issue
          auto-closes via the PR's `Closes #n`. This may have just
          unblocked other issues — go back to (a).
        - On conflict: `cd` into `<worktree-path>`, `git fetch origin main`,
          `git merge origin/main`, resolve conflicts (use the
          `resolving-merge-conflicts` skill), re-run the full validation
          gates from executor step 5, commit, push, then retry the merge.
          If the conflict genuinely can't be resolved cleanly even after a
          real attempt, treat it as a failure instead of forcing a bad
          merge: `gh issue edit <n> --remove-label ralph:pr-open --add-label ralph:failed --remove-assignee @me`,
          comment explaining what was tried and why, leave the worktree in
          place, go back to (a) (this issue's dependents stay blocked).

   f. Continue the loop from (a).

4. **Report** a final summary once the loop ends: how many tickets were
   merged, how many landed in `ralph:failed` (and why), and how many
   remain un-eligible because they're blocked on a failed ticket.

## Per-ticket executor procedure

Followed by each spawned agent, entirely inside its own worktree. The
issue number, worktree path, and branch name are given in your prompt — you
do not need to re-derive them. The base branch is always `main`.

1. **Create the worktree**:
   ```bash
   git worktree add <worktree-path> -b <branch-name> main
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
   gh pr create --base main --head <branch-name> \
     --title "..." --body "$(cat <<'EOF'
   ## Summary
   - ...

   ## Test plan
   - [x] ...

   Closes #<n>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   gh issue edit <n> --remove-label ralph:in-progress --add-label ralph:pr-open
   gh issue comment <n> --body "Opened <pr-url>"
   ```
   Leave the worktree in place — do **not** remove it. The orchestrator
   merges this PR itself and removes the worktree once merged (it may need
   the worktree to resolve a merge conflict first).

7. **On failure** (validation still red after a genuine attempt to fix it):
   ```bash
   gh issue edit <n> --remove-label ralph:in-progress --add-label ralph:failed --remove-assignee @me
   gh issue comment <n> --body "<what was tried, why it's still failing, and that the worktree at <worktree-path> is left in place for debugging>"
   ```
   Do **not** remove the worktree. Do **not** retry — one attempt only, per
   `docs/agents/ralph.md`.
