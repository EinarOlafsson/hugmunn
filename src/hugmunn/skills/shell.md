---
name: Shell & CLI
category: Coding
description: Writing shell commands that do what you meant, and failing safely when they don't.
when: composing a shell command, especially one that deletes, moves, or overwrites.
default: false
---

Look before you destroy. Anything with `rm`, `mv`, overwrite redirection, or a
wildcard gets a dry run first — `ls` the glob, or `echo` the command. Shell
deletion has no undo and no trash.

Quote every variable: `"$path"`, not `$path`. An unquoted variable containing a
space becomes two arguments, which turns `rm $file` into deleting the wrong
things. The same applies to `"$@"`.

Beware the self-matching `pgrep -f` / `pkill -f`. The pattern you search for
appears in your own command line, so the command matches and kills itself. Use
a bracket trick — `pgrep -f "[m]yserver"` — or match on a PID.

`set -euo pipefail` at the top of any script that matters: exit on error, on
undefined variable, and on a failure anywhere in a pipeline. Without it a
script cheerfully continues after a failed step and reports success.

Check that a variable is non-empty before using it in a path. `rm -rf "$DIR/"`
with an unset `DIR` deletes from the filesystem root.

Prefer absolute paths in scripts, and never assume the working directory.
`cd` in a compound command changes what follows it in ways that surprise.

Redirect and capture deliberately. `2>&1` merges stderr into stdout; `>` and
`>>` overwrite and append respectively, and choosing the wrong one silently
discards a log.

For long output, pipe to a file rather than the terminal, and use
`--line-buffered` with `grep` in a pipeline that is being watched live —
otherwise matches sit in a buffer and appear to be missing.

When a command will take more than a few seconds, say so before running it,
and prefer a bounded `timeout` over an unbounded wait.
