---
name: Python quality
category: Coding
description: Conventions for readable, idiomatic Python that matches surrounding code.
default: false
---

Write code that reads like the code around it. Match the file's existing naming,
comment density, import style, and level of abstraction. A patch that is
technically better but stylistically foreign makes the file worse.

Do not add abstractions the task did not ask for. A bug fix does not need
surrounding cleanup; a single call site does not need a helper function. Do not
build for hypothetical future requirements.

Do not add error handling for cases that cannot happen. Validate at system
boundaries — user input, network responses, file parsing — and trust internal
code in between. `try/except` around a dictionary lookup you just populated is
noise.

Comment to state a constraint the code cannot express: why a magic number is
that value, which invariant a check protects, what a workaround works around.
Do not comment what the next line does.

Prefer standard library over a dependency, and an existing dependency over a
new one.

Type hints on function signatures; skip them on obvious locals. Use `pathlib`
over `os.path` for new code. F-strings over `.format()`.

When you write a test, make it fail for the reason it names. A test that passes
whether or not the bug exists is worse than no test.
