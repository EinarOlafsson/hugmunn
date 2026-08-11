---
name: Refactoring
category: Coding
description: Changing structure without changing behaviour — and proving you didn't.
default: false
---

Refactoring means behaviour stays identical. If behaviour changes, that is a
rewrite, and it needs different care and a different conversation with the
user. Do not smuggle a fix into a refactor; the diff becomes unreviewable.

Establish the safety net first. Run the existing tests and note what passes.
Without that baseline you cannot tell whether you broke something or it was
already broken.

Use `edit_file` for surgical changes rather than regenerating whole files with
`write_file`. Regenerating is how functions quietly disappear — the model
reproduces 95% of a long file and nobody notices the missing branch until
production.

Work in small steps that each keep the code runnable. Rename, then extract,
then move — running tests between each. A large refactor that fails at the end
gives you no information about which step broke it.

Search before renaming. `search_text` for the symbol across the tree; a name
used in a string, a config file, or a test fixture will not be caught by a
mechanical rename and fails at runtime instead of import time.

Do not expand scope. Fixing formatting, adding type hints, and renaming
variables in files you are already touching feels free and makes the actual
change impossible to see in review. Separate commits.

Verify with `diff_files` or `git diff` before declaring done. Read your own
diff as a reviewer would — a diff you have not read is a diff you cannot
vouch for.

Delete rather than deprecate when nothing references it. Commented-out code and
unused helpers accumulate; version control already remembers them.

If you find a real bug mid-refactor, say so and either fix it separately or
note it — do not fold it in silently, and do not preserve it out of caution
without mentioning it.
