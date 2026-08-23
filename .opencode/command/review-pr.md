---
description: Converge all five pre-PR review dimensions on a committed clean branch diff.
agent: ontoprism-team
---

# /review-pr

Review the current branch only when all intended work is committed and the worktree is clean. Record the starting HEAD and review the committed base-to-HEAD diff. Run `pdm run validate-opencode-config` and the actual external contract `pdm run validate-opencode-runtime`, then dispatch `implementer` to run exact `pdm run verify` before review. Runtime validation launches fresh CLI processes; it does not activate changed configuration in the current session. Quit and restart OpenCode before relying on configuration changes.

In the initial round, dispatch R1 `pr-code-reviewer`, R2 `pr-silent-failure-hunter`, R4 `pr-comment-analyzer`, and R5 `pr-type-design-analyzer` in parallel. After they finish, dispatch R3 `pr-test-analyzer` alone against the same HEAD. Confirm the worktree is clean and HEAD unchanged after R3 before accepting its verdict.

Send every verified actionable finding to `implementer` for a lasting fix and commit. Re-run applicable gates, then review only the reduced set of non-converged dimensions; R3 still runs alone. When all five dimensions converge, run final exact `pdm run verify` and report `PRE-PR REVIEW CONVERGED` with command evidence.

This command performs no push, no PR creation or update, and no merge; it does not establish merge authorization. A later user message must explicitly authorize the exact PR number in the current conversation before the orchestrator may recheck the hard gates and run the constrained squash-merge command. Do not claim merge readiness.

If a Task result is missing or cancelled, perform exactly one event-driven reconciliation before any status claim or redispatch: inspect `git status --porcelain`, `git rev-parse HEAD`, and `git log --oneline -10`. Never infer from silence. Never duplicate an unresolved writer; if the inspection cannot prove whether its work completed, report the task blocked rather than redispatching it. Do not poll child sessions or use polling loops.
