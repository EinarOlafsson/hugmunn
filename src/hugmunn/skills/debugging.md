---
name: Debugging
category: Coding
description: Find the actual cause instead of guessing — reproduce, bisect, verify.
when: something is failing, producing a wrong result, or behaving unexpectedly.
default: false
---

Reproduce before theorising. A bug you cannot trigger on demand is a bug you
cannot confirm you fixed. Get to a command that fails reliably, and keep it —
that command is your test.

Read the whole error. The last line names the symptom; the frame above the
library frames usually names the cause. A `KeyError` in a dict library means
your key was wrong, not that the library is broken.

Bisect rather than stare. Narrow the failing surface by halves — comment out
half the pipeline, check whether the bad value is already bad at the midpoint,
`git stash` recent changes. Two or three bisections beat an hour of reading.

Check your assumptions with `python_exec` or a print, one at a time. "This
dataframe has the column I think it has" and "this file is the file being
loaded" are wrong surprisingly often, and each takes ten seconds to verify.

Prefer evidence over plausibility. The most plausible cause is frequently not
the actual one, and a fix aimed at the plausible cause makes the real bug
harder to find because the symptom shifts.

**When you find it, explain why the symptom followed from the cause.** If you
cannot draw that line, you have found *a* problem, not necessarily *the*
problem. Fixing something and having the symptom disappear is not proof;
coincidence and masking are common.

Fix the cause, not the symptom. A `try/except` around a crash that hides a
`None` produced three functions earlier turns a loud failure into a silent
wrong answer, which is worse.

Verify the fix by re-running the reproduction. Then confirm the failing case
would still fail without the fix — a test that passes either way tests nothing.

Common classes worth checking early: an off-by-one in a slice or range; a
mutable default argument; a variable captured by a closure in a loop; a path
that is relative when you assumed absolute; stale state from a previous run;
integer division; something modified in place that another reference still
points at.
