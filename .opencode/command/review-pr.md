---
description: One bounded pre-PR review round on the committed branch diff.
agent: ontoprism-team
---

# /review-pr

Run only when all intended work is committed and the worktree is clean. Record the starting HEAD.

1. Dispatch all five review dimensions, never a subset: `pr-code-reviewer`, `pr-silent-failure-hunter`, `pr-comment-analyzer` and `pr-type-design-analyzer` in parallel on the committed `main...HEAD` diff.
2. When they have finished, dispatch `pr-test-analyzer` alone against the same HEAD. Afterwards confirm `git status --porcelain` is empty and `git rev-parse HEAD` is unchanged; otherwise its result is inconclusive.
3. Fix verified **blockers** with TDD, commit, and re-run only the dimensions that reported blockers. File **follow-ups** as issues when the owner asks, or list them in the PR body.
4. Two rounds is the ceiling. If blockers remain, report that the PR is too large and propose a split.
5. Run `pdm run verify` once at the end and report the result with the command output.

This command does not push, open or edit a PR, or merge, and it does not establish merge authorization.
