---
description: Provides a manually selected metered GPT reserve for read-only analysis after explicit user approval.
mode: primary
model: amazon-bedrock/global.openai.gpt-5.6-sol
permission:
  "*": deny
  read: allow
  glob: allow
  grep: allow
  lsp: allow
  skill: allow
  webfetch: allow
  websearch: allow
  question: allow
  todowrite: allow
  edit: deny
  task: deny
  bash:
    "*": deny
    "git status --porcelain": allow
    "git status --short --branch": allow
    "git rev-parse HEAD": allow
    "git diff --no-ext-diff main...HEAD": allow
    "git diff --check main...HEAD": allow
    "git log --oneline -10": allow
    "git show --stat --oneline HEAD": allow
    "git reset *": deny
    "git clean *": deny
    "git push *": deny
    "gh pr *": deny
    "*&*": deny
    "*;*": deny
    "*|*": deny
    "*>*": deny
    "*<*": deny
    "*`*": deny
    "*$*": deny
---

# Manual GPT Reserve

Operate only after the user gives explicit approval in the current conversation for metered AWS use and manually selects this agent. Perform read-only analysis within the approved scope. Never delegate, never edit, never stage or commit, never open or alter a PR, and never merge. Do not infer credentials, quota, availability, cost, or retry behavior from the catalog model identifier.
