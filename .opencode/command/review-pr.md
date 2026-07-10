---
description: Run the full PR review protocol — tests, lint, pre-commit, diff review — to zero findings.
agent: pr-reviewer
---

# /review-pr

Run the full PR review protocol (`/pr-review-toolkit:review-pr`) on the current branch vs `main`.

## Steps

1. `git diff --stat main...HEAD` to see what changed.
2. Run `pdm run test` — must pass with 0 failures.
3. Run `pdm run lint` — zero lint/type errors.
4. Run `npm --prefix frontend run check` — zero svelte-check errors.
5. Run `pre-commit run --all-files` — all hooks pass.
6. Review the diff thoroughly for correctness, test quality, and project conventions.
7. Report all findings in a structured table. If everything passes, confirm "✅ PR ready to merge."

## Output format

| Step | Status | Details |
|------|--------|---------|
| Tests | ✅/❌ | X passed, Y failed |
| Lint | ✅/❌ | any findings |
| ... | ... | ... |

Do not stop at the first failure — continue running remaining steps and report all issues together.
