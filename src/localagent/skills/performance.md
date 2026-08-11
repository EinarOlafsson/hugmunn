---
name: Performance
category: Coding
description: Making slow code fast by measuring first, not by guessing where the time goes.
when: something is too slow, or you are tempted to optimise.
default: false
---

Measure before changing anything. Intuition about where time goes is wrong more
often than it is right, and an optimisation aimed at the wrong place costs
readability and buys nothing. `python_exec` with `time.perf_counter()` around
suspected sections, or `cProfile` for a whole script.

Fix the biggest cost first, and stop when it is fast enough. Halving a function
that accounts for 3% of runtime is not worth the complexity it introduces.

Know which kind of slow you have — the fixes have nothing in common:

| Symptom | Likely cause | Fix |
|---|---|---|
| CPU pinned at 100% | actual computation | vectorise, better algorithm |
| CPU idle, disk busy | I/O bound | batch reads, avoid re-reading |
| Memory climbing | holding everything | stream or chunk |
| Fast then suddenly slow | swapping | reduce footprint |
| GPU idle during a GPU job | data loading | more workers, prefetch |

In numerical Python, the usual culprit is a loop over rows that should be one
vectorised operation. A pandas `iterrows` loop is typically 100× slower than
the equivalent column operation, and a `.apply` is not much better.

Watch for accidental quadratic behaviour: a lookup inside a loop over the same
collection, repeated string concatenation, or `list.insert(0, x)` in a loop.
These are fine at n=100 and fatal at n=100,000.

Reading a file inside a loop that iterates over the same file is the most common
I/O mistake. Read once into memory, or restructure so each item is touched once.

Do not cache before you have measured, and never cache something that can go
stale without a way to invalidate it. A stale cache produces wrong answers,
which is worse than slow ones.

Re-measure after the change and report the actual numbers. "Optimised the
loop" without a before and after is an unverified claim.
