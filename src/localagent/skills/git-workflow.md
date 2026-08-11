---
name: Git workflow
category: Coding
description: Commits, branches, and recovering from mistakes without losing work.
when: committing, branching, merging, or anything has gone wrong in a repository.
default: false
---

Check state before acting. `git status` and `git diff` before every commit —
staging a file you did not mean to is the most common way secrets and scratch
files reach a remote.

Never commit credentials, tokens, or large binaries. A secret in history stays
in history even after you delete the file; it must be revoked, not removed.
Data and model weights belong in `.gitignore`, not in the repository.

Write commit messages that explain *why*. The diff already shows what changed.
"Fix off-by-one in shard check — a half-downloaded model reported ready" is
useful a year later; "fix bug" is not.

One logical change per commit. A commit that fixes a bug, renames three
variables, and reformats a file cannot be reviewed, reverted, or bisected.

Branch for anything non-trivial, and never commit directly to the default
branch of a shared repository unless that is the agreed workflow.

Before pushing, confirm you are pushing what you think, where you think:
`git log origin/main..HEAD` shows what is about to go, and `git remote -v`
shows where. Running a repository command from the wrong directory is a common
and expensive mistake.

## Recovering

Almost nothing is lost. `git reflog` shows every state HEAD has been in,
including after a bad reset or a deleted branch, and `git reset --hard <sha>`
from reflog gets it back.

- Committed to the wrong branch → `git branch correct-branch`, then reset the
  wrong one back.
- Want to undo a commit that is already pushed → `git revert`, not `git reset`.
  Rewriting shared history breaks everyone else's clone.
- Uncommitted work in the way → `git stash`, and remember `git stash pop`.

**Do not run `git reset --hard`, `git clean -fd`, or a force-push without
saying what will be lost first.** These are the operations that destroy work
that was never committed, and reflog cannot recover what was never in git.
