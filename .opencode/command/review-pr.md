---
description: Pre-PR review of the committed branch diff in all five dimensions, run to convergence.
agent: ontoprism-team
---

# /review-pr

Run only when all intended work is committed and the worktree is clean. Record the starting HEAD.

1. Dispatch all five review dimensions, never a subset: `pr-code-reviewer`, `pr-silent-failure-hunter`, `pr-comment-analyzer` and `pr-type-design-analyzer` in parallel on the committed diff against the PR's base branch (`git diff --no-ext-diff <base>...HEAD`: the milestone branch for an issue PR, `main` for a milestone PR).
2. When they have finished, dispatch `pr-test-analyzer` alone against the same HEAD. Afterwards confirm `git status --porcelain` is empty and `git rev-parse HEAD` is unchanged; otherwise its result is inconclusive, the dimension has not converged, and the PR is not ready.
3. Address every verified finding and every reasonable suggestion with TDD, commit, and re-run only the dimensions that have not converged, on the fix range. A finding you cannot verify is dropped with a one-line reason. The only other exception is a major out-of-scope finding: follow "What a finding becomes" in `AGENTS.md`; you may dispatch `issue-steward` for it. Write each drop and each deferral into the PR-body draft (`tmp/plans/<name>.md`) when you decide it: a deferral with its issue number or comment URL and the sentence of the issue body that puts it out of scope. Brief every re-run dimension with its previous findings and the outcome of each (fixed in `<sha>`, dropped with the reason, or deferred with its line from the draft).
4. Repeat until all five dimensions have converged: a full pass with no unresolved verified finding and its suggestions addressed. A converged dimension re-arms when a later fix touches what it reviews. There is no round ceiling.
5. Run `pdm run verify` once at the end and report the result with the command output. The PR-body draft already lists every dropped finding and every deferral; the engineer publishes it to the PR body (`pr-create --body-file` or `pr-edit --body-file`) before the merge.

This command does not push, open or edit a PR, or merge, and it does not establish merge authorization.
