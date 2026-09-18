---
description: Pre-PR review of the committed branch diff in all five dimensions, run to convergence.
agent: ontoprism-team
---

# /review-pr

Run only when all intended work is committed and the worktree is clean. Record the starting HEAD.

1. Dispatch all five review dimensions, never a subset: `pr-code-reviewer`, `pr-silent-failure-hunter`, `pr-comment-analyzer` and `pr-type-design-analyzer` in parallel on the committed diff against the PR's base branch (`git diff --no-ext-diff <base>...HEAD`: the milestone branch for an issue PR, `main` for a milestone PR).
2. When they have finished, dispatch `pr-test-analyzer` alone against the same HEAD. Afterwards confirm `git status --porcelain` is empty and `git rev-parse HEAD` is unchanged; otherwise its result is inconclusive, the dimension has not converged, and the PR is not ready.
3. Address every verified finding and every reasonable suggestion with TDD, commit, and re-run only the dimensions that have not converged, on the fix range. Defer a suggestion to an issue only when the owner agrees it is out of scope.
4. Repeat until all five dimensions have converged: a full pass with no unresolved verified finding and its suggestions addressed. A converged dimension re-arms when a later fix touches what it reviews. There is no round ceiling.
5. Run `pdm run verify` once at the end and report the result with the command output.

This command does not push, open or edit a PR, or merge, and it does not establish merge authorization.
