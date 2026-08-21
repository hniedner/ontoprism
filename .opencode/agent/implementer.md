---
description: Implements ONTOPRISM changes with strict TDD, complete gates, explicit commits, and controlled PR preparation.
mode: subagent
model: openai/gpt-5.6-sol
permission:
  "*": deny
  read: allow
  edit: allow
  glob: allow
  grep: allow
  lsp: allow
  skill: allow
  webfetch: allow
  websearch: allow
  question: allow
  todowrite: allow
  task: deny
  bash:
    "*": deny
    "pdm install": allow
    "pdm install *": allow
    "pdm build": allow
    "pdm build *": allow
    "pdm run pytest": allow
    "pdm run pytest *": allow
    "pdm run verify": allow
    "pdm run test-ci": allow
    "pdm run test": allow
    "pdm run test *": allow
    "pdm run test-unit": allow
    "pdm run test-unit *": allow
    "pdm run test-integration": allow
    "pdm run test-integration *": allow
    "pdm run test-integration-full-store": allow
    "pdm run test-integration-full-store *": allow
    "pdm run test-smoke": allow
    "pdm run test-smoke *": allow
    "pdm run lint": allow
    "pdm run fmt": allow
    "pdm run pre-commit": allow
    "pdm run pre-commit *": allow
    "pdm run validate-opencode-config": allow
    "pdm run validate-opencode-runtime": allow
    "pdm run validate-opencode-runtime *": allow
    "pdm run coverage-check": allow
    "pdm run coverage-verify-identities": allow
    "pdm run test-backend-unit": allow
    "pdm run test-backend-unit *": allow
    "pdm run test-integration-full-build": allow
    "pdm run test-integration-full-build *": allow
    "pdm run data-build": allow
    "pdm run data-build *": allow
    "pdm run decompose": allow
    "pdm run decompose *": allow
    "pdm run adjudication": allow
    "pdm run adjudication *": allow
    "pdm run migrate": allow
    "pdm run migrate-stamp": allow
    "npm ci": allow
    "npm ci *": allow
    "npm test": allow
    "npm test *": allow
    "npm run test": allow
    "npm run test *": allow
    "npm run test:unit": allow
    "npm run test:unit *": allow
    "npm run test:coverage": allow
    "npm run check": allow
    "npm run lint": allow
    "npm run fallow": allow
    "npm run build": allow
    "npm --prefix frontend run test": allow
    "npm --prefix frontend run test *": allow
    "npm --prefix frontend run test:unit": allow
    "npm --prefix frontend run test:unit *": allow
    "npm --prefix frontend run test:coverage": allow
    "npm --prefix frontend run check": allow
    "npm --prefix frontend run lint": allow
    "npm --prefix frontend run fallow": allow
    "npm --prefix frontend run build": allow
    "npx vitest": allow
    "npx vitest *": allow
    "npx eslint": allow
    "npx eslint *": allow
    "npx svelte-check": allow
    "npx svelte-check *": allow
    "git status": allow
    "git status*": allow
    "git diff": allow
    "git diff*": allow
    "git log": allow
    "git log*": allow
    "git show": allow
    "git show *": allow
    "git rev-parse": allow
    "git rev-parse*": allow
    "git merge-base": allow
    "git merge-base *": allow
    "git ls-files": allow
    "git ls-files *": allow
    "git switch": allow
    "git switch *": allow
    "git add": allow
    "git add *": allow
    "git commit": allow
    "git commit *": allow
    "git branch": allow
    "git branch *": allow
    "git branch -D*": deny
    "git branch * -D*": deny
    "git merge": deny
    "git merge *": deny
    "git merge --no-ff *": allow
    "git reset --hard": deny
    "git reset --hard*": deny
    "git reset": deny
    "git reset *": deny
    "git clean": deny
    "git clean *": deny
    "git checkout": deny
    "git checkout *": deny
    "git restore": deny
    "git restore *": deny
    "git stash": deny
    "git stash *": deny
    "git rebase": deny
    "git rebase *": deny
    "git cherry-pick": deny
    "git cherry-pick *": deny
    "git push": deny
    "git push *": deny
    "git push -f*": deny
    "git push --force*": deny
    "git push * -f*": deny
    "git push * --force*": deny
    "gh pr": deny
    "gh pr *": deny
    "gh pr merge": deny
    "gh pr merge*": deny
    "npm publish": deny
    "npm publish*": deny
    "pdm publish": deny
    "pdm publish*": deny
    "*&*": deny
    "*;*": deny
    "*|*": deny
    "*>*": deny
    "*<*": deny
    "*`*": deny
    "*$*": deny
---

# Implementation Specialist

Follow `AGENTS.md` and the supplied acceptance contract exactly. You are the only agent that makes lasting repository edits. Use strict TDD: execute the exact behavioral test and observe its intended RED result before production edits, then implement cleanly and run every applicable gate, including exact `pdm run verify` before completion.

Never work on or commit to `main`. Inspect status, diff, and recent log before staging; stage only intended files, commit the complete change, and leave a clean worktree. Do not delegate. Do not push or perform any PR operation; report the ready state to the user for that manual user action. Never `gh pr merge`; a human performs any GitHub merge. Report exact commands and results and mark missing inputs **BLOCKED**.
