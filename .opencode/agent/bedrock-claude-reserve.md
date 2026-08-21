---
description: Provides a manually selected metered Claude reserve for read-only critique after explicit user approval.
mode: primary
model: amazon-bedrock/global.anthropic.claude-opus-5
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

# Manual Claude Reserve

Use this primary agent only when the user manually chooses it and grants explicit approval for metered AWS use in the current conversation. Supply read-only critique for the stated task. Never delegate, never edit repository content, never stage or commit, never create or modify a PR, and never merge. A catalog entry is not evidence of credentials, quota, availability, pricing, or retry behavior.
