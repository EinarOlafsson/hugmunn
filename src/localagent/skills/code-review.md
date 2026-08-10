---
name: Code review
category: Coding
description: Review checklist that finds real defects instead of style opinions.
default: false
---

Report every issue you find, including ones you are uncertain about. Do not
filter for importance while looking — coverage first, ranking second. For each
finding, give a confidence level and a severity so it can be triaged.

For each finding, state a concrete failure: the input or state that triggers it
and the wrong behaviour that results. "This could be clearer" is not a finding.
If you cannot describe how it breaks, it is a preference, not a bug.

Look in this order, because this is roughly the order of cost-to-fix-later:

1. **Correctness** — off-by-one, wrong operator, inverted condition, unhandled
   `None`, mutation of a shared default argument, a variable used before it is
   set on some path.
2. **Resource and lifetime** — files or sockets not closed, locks not released,
   unbounded growth, work started that is never awaited or joined.
3. **Concurrency** — shared state touched from two threads, a check-then-act
   that is not atomic, blocking work on a UI thread.
4. **Boundaries** — untrusted input reaching a filesystem path, a shell command,
   or a query without validation.
5. **Tests** — does a new test actually fail when the code is wrong?

Read the surrounding code before judging a line. Much of what looks wrong in a
diff is correct in context, and the reverse is more dangerous.

Say plainly when a section looks correct. A review that only lists problems
gives no signal about what was actually checked.
