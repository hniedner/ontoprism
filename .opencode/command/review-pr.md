---
description: Converge all five pre-PR review dimensions on a committed clean branch diff.
agent: ontoprism-team
---

# /review-pr

Review the current branch only when all intended work is committed and the worktree is clean. Record the starting HEAD and review the committed base-to-HEAD diff. Run exact `pdm run verify` before review.

In the initial round, dispatch R1 `pr-code-reviewer`, R2 `pr-silent-failure-hunter`, R4 `pr-comment-analyzer`, and R5 `pr-type-design-analyzer` in parallel. After they finish, dispatch R3 `pr-test-analyzer` alone against the same HEAD. Confirm the worktree is clean and HEAD unchanged after R3 before accepting its verdict.

Send every verified actionable finding to `implementer` for a lasting fix and commit. Re-run applicable gates, then review only the reduced set of non-converged dimensions; R3 still runs alone. When all five dimensions converge, run final exact `pdm run verify` and report `PRE-PR REVIEW CONVERGED` with command evidence.

This command performs no push, no PR creation or mutation, and no merge unless a later, separate user request explicitly dispatches an allowed action. Never `gh pr merge`; a human merges. Do not claim merge readiness.
